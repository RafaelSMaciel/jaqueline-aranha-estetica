import json
import logging
import os
from datetime import datetime, timedelta

from django.contrib import messages
from django.db import OperationalError, ProgrammingError, transaction
from django.db.models import Q
from django.shortcuts import redirect, render
from django.utils import timezone
from django_ratelimit.decorators import ratelimit

from ..models import (
    AceitePrivacidade,
    AssinaturaTermoProcedimento,
    Atendimento,
    Cliente,
    Notificacao,
    Procedimento,
    Profissional,
    VersaoTermo,
)
from ..utils.email import (
    enviar_confirmacao_agendamento_email,
    enviar_aprovacao_profissional_email,
    enviar_termos_pendentes_email,
)
from ..utils.precos import preco_base_map, preco_para
from ..utils.whatsapp import SITE_URL, gerar_token

logger = logging.getLogger(__name__)

WHATSAPP_NUMERO = os.environ.get('WHATSAPP_NUMERO', '5517999990000')
CLINIC_NAME = os.environ.get('CLINIC_NAME', 'Shiva Zen')


def agendamento_publico(request):
    """
    Página de agendamento público — 3 steps:
    1. Escolher procedimento
    2. Escolher data/horário (calendário)
    3. Informar dados + confirmar
    """
    procedimentos_com_preco = []
    try:
        procedimentos = list(Procedimento.objects.filter(ativo=True))
        precos = preco_base_map(procedimentos)
        for proc in procedimentos:
            valor = precos.get(proc.pk)
            procedimentos_com_preco.append({
                'id': proc.pk,
                'nome': proc.nome,
                'descricao': proc.descricao or '',
                'duracao_minutos': proc.duracao_minutos,
                'preco': float(valor) if valor is not None else 0,
            })
    except (OperationalError, ProgrammingError):
        logger.warning('Tabelas de procedimento/preço não encontradas.')

    # Pré-seleção de procedimento (vindo de promoções)
    proc_preselect = request.GET.get('procedimento', '')

    context = {
        'procedimentos': procedimentos_com_preco,
        'procedimentos_json': json.dumps(procedimentos_com_preco),
        'whatsapp_numero': WHATSAPP_NUMERO,
        'proc_preselect': proc_preselect,
    }

    return render(request, 'agenda/agendamento_publico.html', context)


@ratelimit(key='ip', rate='10/m', method='POST', block=True)
def confirmar_agendamento(request):
    """
    Processa a confirmação do agendamento SEM login.
    Recebe: nome, telefone, procedimento, profissional, datetime.
    Cria/encontra cliente pelo telefone e agenda.
    """
    if request.method != 'POST':
        return redirect('shivazen:agendamento_publico')

    nome = request.POST.get('nome', '').strip()
    telefone = request.POST.get('telefone', '').strip()
    data_nascimento_str = request.POST.get('data_nascimento', '').strip()
    email = request.POST.get('email', '').strip() or None
    procedimento_id = request.POST.get('procedimento')
    profissional_id = request.POST.get('profissional')
    datetime_str = request.POST.get('datetime')

    if not all([nome, telefone, data_nascimento_str, procedimento_id, profissional_id, datetime_str]):
        messages.error(request, 'Todos os campos obrigatórios devem ser preenchidos.')
        return redirect('shivazen:agendamento_publico')

    # Parse data de nascimento
    try:
        from datetime import date as _date
        data_nascimento = datetime.strptime(data_nascimento_str, '%Y-%m-%d').date()
    except ValueError:
        messages.error(request, 'Data de nascimento inválida.')
        return redirect('shivazen:agendamento_publico')

    # Validar idade mínima: 18 anos
    hoje = timezone.now().date()
    idade = hoje.year - data_nascimento.year - (
        (hoje.month, hoje.day) < (data_nascimento.month, data_nascimento.day)
    )
    if idade < 18:
        messages.error(request, 'É necessário ter pelo menos 18 anos para agendar.')
        return redirect('shivazen:agendamento_publico')

    try:
        procedimento = Procedimento.objects.get(pk=procedimento_id)
        profissional = Profissional.objects.get(pk=profissional_id)
        data_hora = datetime.fromisoformat(datetime_str)
        data_hora_fim = data_hora + timedelta(minutes=procedimento.duracao_minutos)

        # Transacao atomica: cliente + atendimento + notificacao de termo
        # devem persistir juntos ou nenhum (evita Cliente orfao em falha).
        with transaction.atomic():
            cliente, created = Cliente.objects.select_for_update().get_or_create(
                telefone=telefone,
                defaults={
                    'nome_completo': nome,
                    'data_nascimento': data_nascimento,
                    'email': email,
                    'ativo': True,
                }
            )
            if not created:
                atualizar = False
                if cliente.nome_completo != nome:
                    cliente.nome_completo = nome
                    atualizar = True
                if not cliente.data_nascimento and data_nascimento:
                    cliente.data_nascimento = data_nascimento
                    atualizar = True
                if not cliente.email and email:
                    cliente.email = email
                    atualizar = True
                if atualizar:
                    cliente.save()

            # Verificar conflitos dentro da transacao com SELECT FOR UPDATE
            # para evitar race condition em reservas simultaneas no mesmo slot.
            conflito = Atendimento.objects.select_for_update().filter(
                profissional=profissional,
                data_hora_inicio__lt=data_hora_fim,
                data_hora_fim__gt=data_hora,
                status__in=['PENDENTE', 'AGENDADO', 'CONFIRMADO']
            ).first() is not None

            if conflito:
                messages.error(request, 'Este horário já foi reservado. Por favor, escolha outro.')
                return redirect('shivazen:agendamento_publico')

            preco_obj = preco_para(procedimento, profissional)
            valor = float(preco_obj.valor) if preco_obj else None

            atendimento = Atendimento.objects.create(
                cliente=cliente,
                profissional=profissional,
                procedimento=procedimento,
                data_hora_inicio=data_hora,
                data_hora_fim=data_hora_fim,
                valor_cobrado=valor,
                status='PENDENTE'
            )

            # --- Notificacao de termos pendentes (dentro da transacao) ---
            termos_pendentes = VersaoTermo.objects.filter(
                Q(tipo='LGPD') | Q(procedimento=procedimento),
                ativa=True,
            )

            assinados_ids = set()
            assinados_ids.update(
                AceitePrivacidade.objects.filter(cliente=cliente).values_list('versao_termo_id', flat=True)
            )
            assinados_ids.update(
                AssinaturaTermoProcedimento.objects.filter(cliente=cliente).values_list('versao_termo_id', flat=True)
            )

            tem_pendente = any(t.pk not in assinados_ids for t in termos_pendentes)
            dados_termo = None
            if tem_pendente:
                token_termo = gerar_token()
                Notificacao.objects.create(
                    atendimento=atendimento,
                    tipo='LEMBRETE',
                    canal='EMAIL',
                    status_envio='PENDENTE',
                    token=token_termo,
                )
                site_url = SITE_URL.rstrip('/')
                link_termo = f"{site_url}/termo/{token_termo}/"
                dados_termo = {
                    'nome': nome,
                    'link_termo': link_termo,
                }

        # --- Envio de emails fora da transacao (I/O externo) ---

        # Termos pendentes por email
        if dados_termo and email:
            enviar_termos_pendentes_email(email, dados_termo)

        # Confirmacao de agendamento por email pro cliente
        data_formatada = data_hora.strftime('%d/%m/%Y as %H:%M')
        dados_confirmacao = {
            'nome': nome,
            'procedimento': procedimento.nome,
            'profissional': profissional.nome,
            'data_hora': data_formatada,
            'valor': f"R$ {valor:.2f}" if valor else 'A consultar',
        }

        if email:
            enviar_confirmacao_agendamento_email(email, dados_confirmacao)

        # Notificar profissional por email sobre agendamento pendente
        site_url = SITE_URL.rstrip('/')
        prof_email = getattr(profissional, 'usuario', None)
        prof_email = prof_email.email if prof_email else None
        if prof_email:
            enviar_aprovacao_profissional_email(prof_email, {
                'profissional': profissional.nome,
                'cliente': nome,
                'procedimento': procedimento.nome,
                'data_hora': data_formatada,
                'link_aprovar': f"{site_url}/profissional/atendimento/{atendimento.pk}/aprovar/",
                'link_rejeitar': f"{site_url}/profissional/atendimento/{atendimento.pk}/rejeitar/",
            })

        # Armazenar na sessao para a pagina de sucesso
        request.session['agendamento_sucesso'] = {
            'nome': nome,
            'procedimento': procedimento.nome,
            'profissional': profissional.nome,
            'data_hora': data_formatada,
            'valor': f"R$ {valor:.2f}" if valor else 'A consultar',
            'pendente': True,
        }

        return redirect('shivazen:agendamento_sucesso')

    except Exception as e:
        # SEGURANÇA: Logar o erro real mas mostrar mensagem genérica ao usuário
        logger.error(f'Erro ao confirmar agendamento: {e}', exc_info=True)
        messages.error(request, 'Ocorreu um erro ao confirmar o agendamento. Tente novamente.')
        return redirect('shivazen:agendamento_publico')


def agendamento_sucesso(request):
    """Página de sucesso após agendamento com botão WhatsApp"""
    dados = request.session.pop('agendamento_sucesso', None)
    if not dados:
        return redirect('shivazen:agendamento_publico')
    return render(request, 'agenda/agendamento_sucesso.html', {'dados': dados})


def meus_agendamentos(request):
    """
    Página para ver agendamentos pelo telefone celular.
    Fluxo: informar celular → verificar código → ver agendamentos.
    """
    step = request.GET.get('step', '1')
    telefone = request.session.get('telefone_verificado')

    if step == '1' or not telefone:
        # Step 1: Informar celular
        return render(request, 'agenda/meus_agendamentos.html', {'step': '1'})

    # Step 3: Mostrar agendamentos
    clientes = Cliente.objects.filter(telefone=telefone)
    agendamentos = Atendimento.objects.filter(
        cliente__in=clientes
    ).select_related('profissional', 'procedimento').order_by('-data_hora_inicio')

    agendamentos_futuros = agendamentos.filter(
        data_hora_inicio__gte=timezone.now(),
        status__in=['PENDENTE', 'AGENDADO', 'CONFIRMADO']
    )

    agendamentos_passados = agendamentos.filter(
        data_hora_inicio__lt=timezone.now()
    ) | agendamentos.filter(
        status__in=['REALIZADO', 'CANCELADO']
    )

    context = {
        'step': '3',
        'telefone': telefone,
        'agendamentos_futuros': agendamentos_futuros[:20],
        'agendamentos_passados': agendamentos_passados.distinct()[:20],
        'whatsapp_numero': WHATSAPP_NUMERO,
    }

    return render(request, 'agenda/meus_agendamentos.html', context)


JANELA_MINIMA_REAGENDAMENTO = timedelta(hours=24)


@ratelimit(key='ip', rate='10/m', method='POST', block=True)
def reagendar_agendamento(request, token):
    """
    Fluxo publico de reagendamento via token seguro.
    GET: exibe calendario para escolher nova data/horario.
    POST: cria novo Atendimento apontando reagendado_de -> antigo,
          marca antigo como REAGENDADO. Tudo em transacao atomica.
    """
    try:
        atendimento = Atendimento.objects.select_related(
            'cliente', 'profissional', 'procedimento'
        ).get(token_cancelamento=token)
    except Atendimento.DoesNotExist:
        messages.error(request, 'Agendamento nao encontrado.')
        return redirect('shivazen:agendamento_publico')

    agora = timezone.now()
    if atendimento.data_hora_inicio <= agora:
        messages.error(request, 'Nao e possivel reagendar atendimentos passados.')
        return redirect('shivazen:meus_agendamentos')

    if atendimento.status in ['CANCELADO', 'REALIZADO', 'FALTOU', 'REAGENDADO']:
        messages.error(
            request,
            f'Este atendimento esta {atendimento.get_status_display().lower()} e nao pode ser reagendado.'
        )
        return redirect('shivazen:meus_agendamentos')

    if (atendimento.data_hora_inicio - agora) < JANELA_MINIMA_REAGENDAMENTO:
        messages.error(
            request,
            'Reagendamento requer no minimo 24h de antecedencia. '
            'Entre em contato pelo WhatsApp para ajustes de ultima hora.'
        )
        return redirect('shivazen:meus_agendamentos')

    if request.method == 'GET':
        procedimentos_json = json.dumps([{
            'id': atendimento.procedimento.pk,
            'nome': atendimento.procedimento.nome,
            'duracao_minutos': atendimento.procedimento.duracao_minutos,
        }])
        context = {
            'atendimento': atendimento,
            'procedimentos_json': procedimentos_json,
            'whatsapp_numero': WHATSAPP_NUMERO,
        }
        return render(request, 'agenda/reagendar.html', context)

    datetime_str = request.POST.get('datetime', '').strip()
    profissional_id = request.POST.get('profissional') or atendimento.profissional_id

    if not datetime_str:
        messages.error(request, 'Selecione uma nova data e horario.')
        return redirect('shivazen:reagendar_agendamento', token=token)

    try:
        nova_data = datetime.fromisoformat(datetime_str)
    except ValueError:
        messages.error(request, 'Data/horario invalidos.')
        return redirect('shivazen:reagendar_agendamento', token=token)

    if nova_data <= agora:
        messages.error(request, 'Escolha uma data futura.')
        return redirect('shivazen:reagendar_agendamento', token=token)

    try:
        profissional = Profissional.objects.get(pk=profissional_id, ativo=True)
    except Profissional.DoesNotExist:
        messages.error(request, 'Profissional indisponivel.')
        return redirect('shivazen:reagendar_agendamento', token=token)

    nova_data_fim = nova_data + timedelta(minutes=atendimento.procedimento.duracao_minutos)

    try:
        with transaction.atomic():
            antigo = Atendimento.objects.select_for_update().get(pk=atendimento.pk)

            if antigo.status in ['CANCELADO', 'REALIZADO', 'FALTOU', 'REAGENDADO']:
                messages.error(
                    request,
                    'Este atendimento ja foi processado em outra operacao.'
                )
                return redirect('shivazen:meus_agendamentos')

            conflito = Atendimento.objects.select_for_update().filter(
                profissional=profissional,
                data_hora_inicio__lt=nova_data_fim,
                data_hora_fim__gt=nova_data,
                status__in=['PENDENTE', 'AGENDADO', 'CONFIRMADO']
            ).exclude(pk=antigo.pk).first() is not None

            if conflito:
                messages.error(request, 'Este horario acabou de ser reservado. Escolha outro.')
                return redirect('shivazen:reagendar_agendamento', token=token)

            novo = Atendimento.objects.create(
                cliente=antigo.cliente,
                profissional=profissional,
                procedimento=antigo.procedimento,
                promocao=antigo.promocao,
                reagendado_de=antigo,
                data_hora_inicio=nova_data,
                data_hora_fim=nova_data_fim,
                valor_cobrado=antigo.valor_cobrado,
                valor_original=antigo.valor_original,
                descricao_preco=antigo.descricao_preco,
                status='AGENDADO',
            )

            antigo.status = 'REAGENDADO'
            antigo.save()

        data_fmt = nova_data.strftime('%d/%m/%Y as %H:%M')
        request.session['agendamento_sucesso'] = {
            'nome': antigo.cliente.nome_completo,
            'procedimento': antigo.procedimento.nome,
            'profissional': profissional.nome,
            'data_hora': data_fmt,
            'valor': f'R$ {float(novo.valor_cobrado):.2f}' if novo.valor_cobrado else 'A consultar',
            'pendente': True,
            'reagendamento': True,
        }
        return redirect('shivazen:agendamento_sucesso')

    except Exception as e:
        logger.error(f'Erro ao reagendar atendimento {atendimento.pk}: {e}', exc_info=True)
        messages.error(request, 'Ocorreu um erro ao reagendar. Tente novamente.')
        return redirect('shivazen:reagendar_agendamento', token=token)



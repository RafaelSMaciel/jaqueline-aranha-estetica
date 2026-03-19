from django.shortcuts import render, redirect
from django.contrib import messages
from django.http import JsonResponse
from django.utils import timezone
from django.urls import reverse
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit
from datetime import datetime, timedelta
import json
import random
import string
import logging

from ..models import (
    Profissional, Procedimento, Atendimento, BloqueioAgenda,
    Cliente, Preco, DisponibilidadeProfissional, CodigoVerificacao
)

logger = logging.getLogger(__name__)

# Número do WhatsApp da clínica
WHATSAPP_NUMERO = '5517000000000'  # TODO: Substituir pelo número real da clínica


def agendamento_publico(request):
    """
    Página de agendamento público — 3 steps:
    1. Escolher procedimento
    2. Escolher data/horário (calendário)
    3. Informar dados + confirmar
    """
    procedimentos = Procedimento.objects.filter(ativo=True)

    # Enriquecer procedimentos com preços
    procedimentos_com_preco = []
    for proc in procedimentos:
        preco_obj = Preco.objects.filter(procedimento=proc, profissional__isnull=True).first()
        if not preco_obj:
            preco_obj = Preco.objects.filter(procedimento=proc).first()
        procedimentos_com_preco.append({
            'id': proc.id_procedimento,
            'nome': proc.nome,
            'descricao': proc.descricao or '',
            'duracao_minutos': proc.duracao_minutos,
            'preco': float(preco_obj.valor) if preco_obj else 0,
        })

    # Pré-seleção de procedimento (vindo de promoções)
    proc_preselect = request.GET.get('procedimento', '')

    context = {
        'procedimentos': procedimentos_com_preco,
        'procedimentos_json': json.dumps(procedimentos_com_preco),
        'whatsapp_numero': WHATSAPP_NUMERO,
        'proc_preselect': proc_preselect,
    }

    return render(request, 'agenda/agendamento_publico.html', context)


def api_horarios_disponiveis(request):
    """
    AJAX endpoint: retorna horários disponíveis para uma data + procedimento.
    GET params: data (YYYY-MM-DD), procedimento_id
    """
    data_str = request.GET.get('data', '')
    procedimento_id = request.GET.get('procedimento_id', '')

    if not data_str or not procedimento_id:
        return JsonResponse({'error': 'Parâmetros obrigatórios: data, procedimento_id'}, status=400)

    try:
        data_selecionada = datetime.strptime(data_str, '%Y-%m-%d').date()
    except ValueError:
        return JsonResponse({'error': 'Data inválida'}, status=400)

    try:
        procedimento = Procedimento.objects.get(pk=procedimento_id, ativo=True)
    except Procedimento.DoesNotExist:
        return JsonResponse({'error': 'Procedimento não encontrado'}, status=404)

    # Buscar profissionais que fazem esse procedimento
    from ..models import ProfissionalProcedimento
    prof_ids = ProfissionalProcedimento.objects.filter(
        procedimento=procedimento
    ).values_list('profissional_id', flat=True)

    profissionais = Profissional.objects.filter(
        id_profissional__in=prof_ids, ativo=True
    )

    # Se não há profissionais vinculados, pegar todos os ativos
    if not profissionais.exists():
        profissionais = Profissional.objects.filter(ativo=True)

    dia_semana = data_selecionada.isoweekday() % 7 + 1

    horarios = []

    for prof in profissionais:
        # Verificar disponibilidade do profissional nesse dia
        try:
            disp = DisponibilidadeProfissional.objects.get(
                profissional=prof, dia_semana=dia_semana
            )
        except DisponibilidadeProfissional.DoesNotExist:
            continue

        # Gerar slots de 30 em 30 minutos dentro do horário do profissional
        intervalo = timedelta(minutes=30)
        hora_atual = datetime.combine(data_selecionada, disp.hora_inicio)
        hora_fim = datetime.combine(data_selecionada, disp.hora_fim)
        # O último slot precisa ter espaço para a duração do procedimento
        hora_limite = hora_fim - timedelta(minutes=procedimento.duracao_minutos)

        while hora_atual <= hora_limite:
            dt_aware = timezone.make_aware(hora_atual)
            fim_procedimento = dt_aware + timedelta(minutes=procedimento.duracao_minutos)

            # Checar se o slot está ocupado
            ocupado = Atendimento.objects.filter(
                profissional=prof,
                data_hora_inicio__lt=fim_procedimento,
                data_hora_fim__gt=dt_aware,
                status_atendimento__in=['AGENDADO', 'CONFIRMADO']
            ).exists()

            # Checar bloqueios
            bloqueado = BloqueioAgenda.objects.filter(
                profissional=prof,
                data_hora_inicio__lte=dt_aware,
                data_hora_fim__gte=dt_aware
            ).exists()

            # Não mostrar horários passados
            passado = dt_aware < timezone.now()

            if not ocupado and not bloqueado and not passado:
                horario_str = hora_atual.strftime('%H:%M')
                # Verificar se esse horário já foi adicionado com esse profissional
                horarios.append({
                    'horario': horario_str,
                    'datetime_iso': dt_aware.isoformat(),
                    'profissional_id': prof.id_profissional,
                    'profissional_nome': prof.nome,
                })

            hora_atual += intervalo

    # Agrupar horários por horário (um horário pode ter vários profissionais)
    horarios_agrupados = {}
    for h in horarios:
        key = h['horario']
        if key not in horarios_agrupados:
            horarios_agrupados[key] = {
                'horario': key,
                'datetime_iso': h['datetime_iso'],
                'profissionais': []
            }
        horarios_agrupados[key]['profissionais'].append({
            'id': h['profissional_id'],
            'nome': h['profissional_nome']
        })

    # Ordenar por horário
    resultado = sorted(horarios_agrupados.values(), key=lambda x: x['horario'])

    return JsonResponse({
        'data': data_str,
        'procedimento': procedimento.nome,
        'duracao': procedimento.duracao_minutos,
        'horarios': resultado
    })


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
    procedimento_id = request.POST.get('procedimento')
    profissional_id = request.POST.get('profissional')
    datetime_str = request.POST.get('datetime')

    if not all([nome, telefone, procedimento_id, profissional_id, datetime_str]):
        messages.error(request, 'Todos os campos são obrigatórios.')
        return redirect('shivazen:agendamento_publico')

    try:
        procedimento = Procedimento.objects.get(pk=procedimento_id)
        profissional = Profissional.objects.get(pk=profissional_id)
        data_hora = datetime.fromisoformat(datetime_str)
        data_hora_fim = data_hora + timedelta(minutes=procedimento.duracao_minutos)

        # Encontrar ou criar cliente pelo telefone
        cliente, created = Cliente.objects.get_or_create(
            telefone=telefone,
            defaults={
                'nome_completo': nome,
                'ativo': True,
            }
        )
        if not created and cliente.nome_completo != nome:
            # Atualiza nome se mudou
            cliente.nome_completo = nome
            cliente.save()

        # Verificar conflitos
        conflito = Atendimento.objects.filter(
            profissional=profissional,
            data_hora_inicio__lt=data_hora_fim,
            data_hora_fim__gt=data_hora,
            status_atendimento__in=['AGENDADO', 'CONFIRMADO']
        ).exists()

        if conflito:
            messages.error(request, 'Este horário já foi reservado. Por favor, escolha outro.')
            return redirect('shivazen:agendamento_publico')

        # Buscar preço
        preco_obj = Preco.objects.filter(
            procedimento=procedimento, profissional=profissional
        ).first()
        if not preco_obj:
            preco_obj = Preco.objects.filter(
                procedimento=procedimento, profissional__isnull=True
            ).first()

        valor = float(preco_obj.valor) if preco_obj else None

        # Criar agendamento
        atendimento = Atendimento.objects.create(
            cliente=cliente,
            profissional=profissional,
            procedimento=procedimento,
            data_hora_inicio=data_hora,
            data_hora_fim=data_hora_fim,
            valor_cobrado=valor,
            status_atendimento='AGENDADO'
        )

        # Montar mensagem WhatsApp
        data_formatada = data_hora.strftime('%d/%m/%Y às %H:%M')
        msg_wpp = (
            f"✅ *Agendamento Confirmado - Shiva Zen*\n\n"
            f"👤 Nome: {nome}\n"
            f"📋 Procedimento: {procedimento.nome}\n"
            f"👩‍⚕️ Profissional: {profissional.nome}\n"
            f"📅 Data/Hora: {data_formatada}\n"
            f"💰 Valor: R$ {valor:.2f}" if valor else ""
        )

        import urllib.parse
        wpp_url = f"https://wa.me/{WHATSAPP_NUMERO}?text={urllib.parse.quote(msg_wpp)}"

        # Armazenar na sessão para a página de sucesso
        request.session['agendamento_sucesso'] = {
            'nome': nome,
            'procedimento': procedimento.nome,
            'profissional': profissional.nome,
            'data_hora': data_formatada,
            'valor': f"R$ {valor:.2f}" if valor else 'A consultar',
            'wpp_url': wpp_url,
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
        status_atendimento__in=['AGENDADO', 'CONFIRMADO']
    )

    agendamentos_passados = agendamentos.filter(
        data_hora_inicio__lt=timezone.now()
    ) | agendamentos.filter(
        status_atendimento__in=['REALIZADO', 'CANCELADO']
    )

    context = {
        'step': '3',
        'telefone': telefone,
        'agendamentos_futuros': agendamentos_futuros[:20],
        'agendamentos_passados': agendamentos_passados.distinct()[:20],
        'whatsapp_numero': WHATSAPP_NUMERO,
    }

    return render(request, 'agenda/meus_agendamentos.html', context)


@ratelimit(key='ip', rate='5/m', method='POST', block=False)
def verificar_telefone(request):
    """AJAX: gerar ou validar código de verificação por telefone."""
    if request.method == 'POST':
        # SEGURANÇA: Rate limiting
        if getattr(request, 'limited', False):
            return JsonResponse({'error': 'Muitas tentativas. Aguarde um momento.'}, status=429)

        data = json.loads(request.body) if request.content_type == 'application/json' else request.POST
        action = data.get('action', '')
        telefone = data.get('telefone', '').strip()

        if not telefone:
            return JsonResponse({'error': 'Telefone obrigatório'}, status=400)

        if action == 'enviar':
            # Verificar se existe algum cliente com esse telefone
            if not Cliente.objects.filter(telefone=telefone).exists():
                return JsonResponse({
                    'error': 'Nenhum agendamento encontrado com esse telefone.'
                }, status=404)

            # Gerar código de 6 dígitos
            codigo = ''.join(random.choices(string.digits, k=6))

            # Invalidar códigos anteriores
            CodigoVerificacao.objects.filter(telefone=telefone, usado=False).update(usado=True)

            # Criar novo código
            CodigoVerificacao.objects.create(telefone=telefone, codigo=codigo)

            # Em produção, enviar via SMS/WhatsApp API
            logger.info(f'Código de verificação gerado para telefone: {telefone[-4:]}')

            return JsonResponse({
                'success': True,
                'message': 'Código de verificação enviado para seu telefone.',
                # SEGURANÇA: codigo_debug REMOVIDO — nunca expor código ao frontend
            })

        elif action == 'verificar':
            codigo_input = data.get('codigo', '').strip()
            verificacao = CodigoVerificacao.objects.filter(
                telefone=telefone, codigo=codigo_input, usado=False
            ).order_by('-criado_em').first()

            if verificacao and verificacao.esta_valido:
                verificacao.usado = True
                verificacao.save()

                # Salvar na sessão
                request.session['telefone_verificado'] = telefone

                return JsonResponse({
                    'success': True,
                    'redirect': reverse('shivazen:meus_agendamentos') + '?step=3'
                })
            else:
                return JsonResponse({
                    'error': 'Código inválido ou expirado.'
                }, status=400)

    return JsonResponse({'error': 'Método não permitido'}, status=405)


def api_dias_disponiveis(request):
    """
    AJAX: retorna quais dias do mês têm disponibilidade para um procedimento.
    GET params: mes (YYYY-MM), procedimento_id
    """
    mes_str = request.GET.get('mes', '')
    procedimento_id = request.GET.get('procedimento_id', '')

    if not mes_str or not procedimento_id:
        return JsonResponse({'error': 'Parâmetros obrigatórios'}, status=400)

    try:
        ano, mes = map(int, mes_str.split('-'))
        primeiro_dia = datetime(ano, mes, 1).date()
        if mes == 12:
            ultimo_dia = datetime(ano + 1, 1, 1).date() - timedelta(days=1)
        else:
            ultimo_dia = datetime(ano, mes + 1, 1).date() - timedelta(days=1)
    except ValueError:
        return JsonResponse({'error': 'Mês inválido'}, status=400)

    try:
        procedimento = Procedimento.objects.get(pk=procedimento_id, ativo=True)
    except Procedimento.DoesNotExist:
        return JsonResponse({'error': 'Procedimento não encontrado'}, status=404)

    # Para cada dia do mês, verificar se algum profissional tem disponibilidade
    from ..models import ProfissionalProcedimento
    prof_ids = ProfissionalProcedimento.objects.filter(
        procedimento=procedimento
    ).values_list('profissional_id', flat=True)

    profissionais = Profissional.objects.filter(
        id_profissional__in=prof_ids, ativo=True
    )
    if not profissionais.exists():
        profissionais = Profissional.objects.filter(ativo=True)

    # Pegar dias da semana em que os profissionais trabalham
    dias_disponibilidade = set()
    for prof in profissionais:
        disps = DisponibilidadeProfissional.objects.filter(profissional=prof)
        for d in disps:
            dias_disponibilidade.add(d.dia_semana)

    hoje = timezone.now().date()
    dias_com_disponibilidade = []
    dia_atual = primeiro_dia

    while dia_atual <= ultimo_dia:
        dia_semana = dia_atual.isoweekday() % 7 + 1
        if dia_semana in dias_disponibilidade and dia_atual >= hoje:
            dias_com_disponibilidade.append(dia_atual.isoformat())
        dia_atual += timedelta(days=1)

    return JsonResponse({
        'mes': mes_str,
        'dias_disponiveis': dias_com_disponibilidade,
    })


def cancelar_agendamento(request):
    """Cancela um agendamento via AJAX (verifica telefone)"""
    if request.method != 'POST':
        return JsonResponse({'erro': 'Método não permitido'}, status=405)

    try:
        data = json.loads(request.body)
        atendimento_id = data.get('atendimento_id')
        telefone = data.get('telefone', '').strip()

        if not atendimento_id or not telefone:
            return JsonResponse({'erro': 'Dados incompletos'}, status=400)

        # Buscar atendimento
        try:
            atendimento = Atendimento.objects.select_related('cliente', 'procedimento').get(
                id_atendimento=atendimento_id
            )
        except Atendimento.DoesNotExist:
            return JsonResponse({'erro': 'Agendamento não encontrado'}, status=404)

        # Verificar se o telefone bate com o cliente
        telefone_limpo = ''.join(filter(str.isdigit, telefone))
        cliente_tel = ''.join(filter(str.isdigit, atendimento.cliente.telefone or ''))

        if telefone_limpo != cliente_tel:
            return JsonResponse({'erro': 'Telefone não corresponde ao agendamento'}, status=403)

        # Verificar se é futuro
        if atendimento.data_hora_inicio <= timezone.now():
            return JsonResponse({'erro': 'Não é possível cancelar agendamentos passados'}, status=400)

        # Cancelar
        atendimento.status_atendimento = 'CANCELADO'
        atendimento.save()

        return JsonResponse({
            'sucesso': True,
            'mensagem': f'Agendamento de {atendimento.procedimento.nome} cancelado com sucesso.',
        })

    except json.JSONDecodeError:
        return JsonResponse({'erro': 'Dados inválidos'}, status=400)
    except Exception as e:
        # SEGURANÇA: Logar erro real mas devolver mensagem genérica
        logger.error(f'Erro ao cancelar agendamento: {e}', exc_info=True)
        return JsonResponse({'erro': 'Ocorreu um erro interno. Tente novamente.'}, status=500)

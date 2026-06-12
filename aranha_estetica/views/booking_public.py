"""Fluxo publico de agendamento — 3 steps (procedimento, data/horario, confirmar).

Slot lock via cache + SELECT FOR UPDATE. Anti-bot: honeypot + Turnstile + OTP
(quando cliente ja existe). Emails de notificacao sempre via Celery (async).
"""
import json
import logging
import os
from datetime import datetime, timedelta

from django.contrib import messages
from django.core.cache import cache
from django.db import DatabaseError, IntegrityError, OperationalError, ProgrammingError, transaction
from django.db.models import Q
from django.shortcuts import redirect, render
from django.utils import timezone
from django_ratelimit.decorators import ratelimit

from ..models import (
    AceitePrivacidade,
    AssinaturaTermoProcedimento,
    Atendimento,
    Cliente,
    Feriado,
    FormularioAnamnese,
    Notificacao,
    Procedimento,
    Profissional,
    RespostaAnamnese,
    VersaoTermo,
)
from ..utils.captcha import turnstile_enabled, turnstile_site_key, verificar_turnstile
from ..utils.pii import mask_email, mask_telefone
from ..utils.precos import preco_base_map, preco_para
from ..utils.security import client_ip as _client_ip
from ..utils.whatsapp import SITE_URL, gerar_token

logger = logging.getLogger(__name__)

WHATSAPP_NUMERO = os.environ.get('WHATSAPP_NUMERO', '5517999990000')
CLINIC_NAME = os.environ.get('CLINIC_NAME', 'Jaqueline Aranha Estética')


@ratelimit(key='ip', rate='60/h', method='GET', block=True)
def agendamento_publico(request):
    """Pagina publica de agendamento.

    Rate limit GET: 60/h por IP — anti-enumeration/scraping (listing
    expoe nome+preco+categoria de Procedimento).
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
                'categoria': proc.categoria,
                'categoria_label': proc.get_categoria_display(),
            })
    except (OperationalError, ProgrammingError):
        logger.warning('booking_tabelas_procedimento_indisponiveis')

    proc_preselect = request.GET.get('procedimento', '')

    categorias_disponiveis = sorted({
        (p['categoria'], p['categoria_label']) for p in procedimentos_com_preco
    })

    formularios_anamnese = []
    try:
        formularios_anamnese = [
            {
                'id': row['id'],
                'nome': row['nome'],
                'escopo': row['escopo'],
                'categoria': row['categoria'],
                'procedimento_id': row['procedimento_id'],
                'obrigatorio': row['obrigatorio'],
                'schema': row['schema_json'],
            }
            for row in FormularioAnamnese.objects.filter(ativo=True).values(
                'id', 'nome', 'escopo', 'categoria',
                'procedimento_id', 'obrigatorio', 'schema_json',
            )
        ]
    except (OperationalError, ProgrammingError):
        pass

    context = {
        'procedimentos': procedimentos_com_preco,
        'categorias_disponiveis': categorias_disponiveis,
        'formularios_anamnese_data': formularios_anamnese,
        'whatsapp_numero': WHATSAPP_NUMERO,
        'proc_preselect': proc_preselect,
        'turnstile_site_key': turnstile_site_key(),
        'turnstile_enabled': turnstile_enabled(),
    }

    return render(request, 'agenda/agendamento_publico.html', context)


@ratelimit(key='ip', rate='10/m', method='POST', block=True)
def confirmar_agendamento(request):
    """Processa confirmacao SEM login.

    Protecoes: honeypot, Turnstile, OTP por SMS (cliente cadastrado), slot lock.
    """
    if request.method != 'POST':
        return redirect('aranha:agendamento_publico')

    if request.POST.get('website', '').strip():
        logger.info('booking_honeypot_triggered', extra={'ip': _client_ip(request)})
        return redirect('aranha:agendamento_publico')

    if turnstile_enabled():
        captcha_token = request.POST.get('cf-turnstile-response', '')
        if not verificar_turnstile(captcha_token, ip=_client_ip(request)):
            messages.error(request, 'Validacao de seguranca falhou. Tente novamente.')
            return redirect('aranha:agendamento_publico')

    nome = request.POST.get('nome', '').strip()
    from ..validators import normalizar_telefone
    telefone = normalizar_telefone(request.POST.get('telefone', ''))
    data_nascimento_str = request.POST.get('data_nascimento', '').strip()
    email = request.POST.get('email', '').strip() or None
    procedimento_id = request.POST.get('procedimento')
    profissional_id = request.POST.get('profissional')
    datetime_str = request.POST.get('datetime')
    consent_email_marketing = request.POST.get('consent_email_marketing') == 'on'
    consent_whatsapp_nps = request.POST.get('consent_whatsapp_nps') == 'on'
    consent_whatsapp_confirmacao = request.POST.get('consent_whatsapp_confirmacao') == 'on'

    if not all([nome, telefone, data_nascimento_str, procedimento_id, profissional_id, datetime_str]):
        messages.error(request, 'Todos os campos obrigatórios devem ser preenchidos.')
        return redirect('aranha:agendamento_publico')

    if email:
        cliente_existente = Cliente.objects.filter(email__iexact=email, ativo=True).exists()
        otp_email = request.session.get('otp_agendamento_email')
        otp_exp = request.session.get('otp_agendamento_expira')
        otp_ok = bool(otp_email) and otp_email == email.lower()
        if otp_ok and otp_exp:
            try:
                otp_ok = datetime.fromisoformat(otp_exp) > timezone.now()
            except ValueError:
                otp_ok = False
        if cliente_existente and not otp_ok:
            messages.error(request, 'Confirme com o codigo SMS enviado ao seu telefone antes de prosseguir.')
            return redirect('aranha:agendamento_publico')

    try:
        data_nascimento = datetime.strptime(data_nascimento_str, '%Y-%m-%d').date()
    except ValueError:
        messages.error(request, 'Data de nascimento inválida.')
        return redirect('aranha:agendamento_publico')

    hoje = timezone.now().date()
    idade = hoje.year - data_nascimento.year - (
        (hoje.month, hoje.day) < (data_nascimento.month, data_nascimento.day)
    )
    if idade < 18:
        messages.error(request, 'É necessário ter pelo menos 18 anos para agendar.')
        return redirect('aranha:agendamento_publico')

    try:
        procedimento = Procedimento.objects.get(pk=procedimento_id)
        profissional = Profissional.objects.get(pk=profissional_id)
        data_hora = datetime.fromisoformat(datetime_str)
        data_hora_fim = data_hora + timedelta(minutes=procedimento.duracao_minutos)

        if Feriado.objects.filter(data=data_hora.date(), bloqueia_agendamento=True).exists():
            messages.error(request, 'Esta data e um feriado/recesso — nao aceitamos agendamentos.')
            return redirect('aranha:agendamento_publico')

        slot_key = f'booking_slot:{profissional_id}:{datetime_str}'
        if not cache.add(slot_key, '1', timeout=30):
            messages.error(request, 'Este horario esta sendo confirmado por outra pessoa. Tente outro.')
            return redirect('aranha:agendamento_publico')

        with transaction.atomic():
            agora = timezone.now()
            ip_origem = _client_ip(request)
            defaults = {
                'nome': nome,
                'data_nascimento': data_nascimento,
                'email': email,
                'ativo': True,
            }
            if consent_email_marketing:
                defaults.update({
                    'consent_email_marketing': True,
                    'consent_email_marketing_em': agora,
                    'consent_email_marketing_ip': ip_origem,
                })
            if consent_whatsapp_nps:
                defaults.update({
                    'consent_whatsapp_nps': True,
                    'consent_whatsapp_nps_em': agora,
                    'consent_whatsapp_nps_ip': ip_origem,
                })
            if consent_whatsapp_confirmacao:
                defaults.update({
                    'consent_whatsapp_confirmacao': True,
                    'consent_whatsapp_confirmacao_em': agora,
                    'consent_whatsapp_confirmacao_ip': ip_origem,
                })

            cliente, created = Cliente.objects.select_for_update().get_or_create(
                telefone=telefone,
                defaults=defaults,
            )
            if not created:
                atualizar = False
                if cliente.nome != nome:
                    cliente.nome = nome
                    atualizar = True
                if not cliente.data_nascimento and data_nascimento:
                    cliente.data_nascimento = data_nascimento
                    atualizar = True
                if not cliente.email and email:
                    cliente.email = email
                    atualizar = True
                if consent_email_marketing and not cliente.consent_email_marketing:
                    cliente.consent_email_marketing = True
                    cliente.consent_email_marketing_em = agora
                    cliente.consent_email_marketing_ip = ip_origem
                    atualizar = True
                if consent_whatsapp_nps and not cliente.consent_whatsapp_nps:
                    cliente.consent_whatsapp_nps = True
                    cliente.consent_whatsapp_nps_em = agora
                    cliente.consent_whatsapp_nps_ip = ip_origem
                    atualizar = True
                if consent_whatsapp_confirmacao and not cliente.consent_whatsapp_confirmacao:
                    cliente.consent_whatsapp_confirmacao = True
                    cliente.consent_whatsapp_confirmacao_em = agora
                    cliente.consent_whatsapp_confirmacao_ip = ip_origem
                    atualizar = True
                if atualizar:
                    cliente.save()

            conflito = Atendimento.objects.select_for_update().filter(
                profissional=profissional,
                data_hora_inicio__lt=data_hora_fim,
                data_hora_fim__gt=data_hora,
                status__in=['PENDENTE', 'AGENDADO', 'CONFIRMADO']
            ).first() is not None

            if conflito:
                messages.error(request, 'Este horário já foi reservado. Por favor, escolha outro.')
                return redirect('aranha:agendamento_publico')

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

            anamnese_raw = request.POST.get('anamnese_respostas', '').strip()
            if anamnese_raw:
                try:
                    anamnese_data = json.loads(anamnese_raw)
                    if isinstance(anamnese_data, dict):
                        for form_id_str, respostas in anamnese_data.items():
                            try:
                                form_id = int(form_id_str)
                                f = FormularioAnamnese.objects.filter(pk=form_id, ativo=True).first()
                                if f and isinstance(respostas, dict):
                                    RespostaAnamnese.objects.create(
                                        formulario=f, cliente=cliente,
                                        atendimento=atendimento, respostas_json=respostas,
                                    )
                            except (ValueError, TypeError):
                                continue
                except (ValueError, TypeError):
                    logger.warning('booking_anamnese_json_invalido')

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
                    status='PENDENTE',
                    token=token_termo,
                )
                site_url = SITE_URL.rstrip('/')
                link_termo = f"{site_url}/termo/{token_termo}/"
                dados_termo = {
                    'nome': nome,
                    'link_termo': link_termo,
                }

        from ..tasks import send_email_async

        data_formatada = data_hora.strftime('%d/%m/%Y as %H:%M')
        site_url = SITE_URL.rstrip('/')

        if dados_termo and email:
            send_email_async.delay('enviar_termos_pendentes_email', email, dados_termo)

        prof_email_obj = getattr(profissional, 'usuario', None)
        prof_email = prof_email_obj.email if prof_email_obj else None
        if prof_email:
            send_email_async.delay('enviar_aprovacao_profissional_email', prof_email, {
                'profissional': profissional.nome,
                'cliente': nome,
                'procedimento': procedimento.nome,
                'data_hora': data_formatada,
                'link_aprovar': f"{site_url}/profissional/atendimento/{atendimento.pk}/aprovar/",
                'link_rejeitar': f"{site_url}/profissional/atendimento/{atendimento.pk}/rejeitar/",
            })

        request.session['agendamento_sucesso'] = {
            'nome': nome,
            'procedimento': procedimento.nome,
            'profissional': profissional.nome,
            'data_hora': data_formatada,
            'valor': f"R$ {valor:.2f}" if valor else 'A consultar',
            'pendente': True,
        }
        request.session.pop('otp_agendamento_email', None)
        request.session.pop('otp_agendamento_expira', None)

        return redirect('aranha:agendamento_sucesso')

    except (DatabaseError, IntegrityError) as exc:
        logger.error(
            'booking_confirmar_falha',
            extra={
                'erro': str(exc),
                'email': mask_email(email) if email else None,
                'telefone': mask_telefone(telefone),
            },
            exc_info=True,
        )
        messages.error(request, 'Ocorreu um erro ao confirmar o agendamento. Tente novamente.')
        return redirect('aranha:agendamento_publico')


def agendamento_sucesso(request):
    """Pagina de sucesso apos agendamento com botao WhatsApp."""
    dados = request.session.pop('agendamento_sucesso', None)
    if not dados:
        return redirect('aranha:agendamento_publico')
    return render(request, 'agenda/agendamento_sucesso.html', {'dados': dados})

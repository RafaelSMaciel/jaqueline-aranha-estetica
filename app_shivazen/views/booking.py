import json
import logging
import os
from datetime import datetime, timedelta

from django.contrib import messages
from django.core.cache import cache
from django.db import OperationalError, ProgrammingError, transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from ..models import (
    AceitePrivacidade,
    AssinaturaTermoProcedimento,
    Atendimento,
    Cliente,
    Notificacao,
    OtpCode,
    Procedimento,
    Profissional,
    VersaoTermo,
)
from ..services import otp_service
from ..utils.captcha import turnstile_enabled, turnstile_site_key, verificar_turnstile
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
        'turnstile_site_key': turnstile_site_key(),
        'turnstile_enabled': turnstile_enabled(),
    }

    return render(request, 'agenda/agendamento_publico.html', context)


from ..utils.security import client_ip as _client_ip  # unify


@require_POST
@ratelimit(key='ip', rate='5/m', method='POST', block=True)
def solicitar_otp_agendamento(request):
    """AJAX: envia OTP via SMS (Zenvia). Requer telefone — sem fallback email."""
    email = (request.POST.get('email') or '').strip().lower()
    telefone = (request.POST.get('telefone') or '').strip() or None
    captcha_token = request.POST.get('cf-turnstile-response', '')

    if turnstile_enabled() and not verificar_turnstile(captcha_token, ip=_client_ip(request)):
        return JsonResponse({'ok': False, 'erro': 'captcha'}, status=400)

    if not email or '@' not in email:
        return JsonResponse({'ok': False, 'erro': 'email_invalido'}, status=400)

    existe = Cliente.objects.filter(email__iexact=email, ativo=True).exists()
    # Se cliente ja existe, usa telefone cadastrado (prioridade sobre form)
    if existe and not telefone:
        c = Cliente.objects.filter(email__iexact=email, ativo=True).only('telefone').first()
        if c and c.telefone:
            telefone = c.telefone

    if not telefone:
        return JsonResponse({'ok': False, 'erro': 'telefone_ausente'}, status=400)

    ok, motivo, canal_usado = otp_service.solicitar_otp(
        email,
        request=request,
        proposito=OtpCode.PROPOSITO_AGENDAMENTO,
        telefone=telefone,
        canal_preferido=OtpCode.CANAL_SMS,
    )
    if not ok and motivo == 'aguarde':
        return JsonResponse({'ok': False, 'erro': 'aguarde'}, status=429)
    if not ok:
        return JsonResponse({'ok': False, 'erro': motivo}, status=400)
    return JsonResponse({'ok': True, 'cliente_existente': existe, 'canal': canal_usado})


@require_POST
@ratelimit(key='ip', rate='10/m', method='POST', block=True)
def verificar_otp_agendamento(request):
    """AJAX: valida codigo; se cliente existe, devolve pre-fill."""
    email = (request.POST.get('email') or '').strip().lower()
    codigo = (request.POST.get('codigo') or '').strip()

    ok, motivo = otp_service.verificar_otp(email, codigo, proposito=OtpCode.PROPOSITO_AGENDAMENTO)
    if not ok:
        return JsonResponse({'ok': False, 'erro': motivo}, status=400)

    request.session['otp_agendamento_email'] = email
    request.session['otp_agendamento_expira'] = (timezone.now() + timedelta(minutes=30)).isoformat()

    cliente = Cliente.objects.filter(email__iexact=email, ativo=True).first()
    prefill = None
    if cliente:
        prefill = {
            'nome': cliente.nome_completo,
            'telefone': cliente.telefone or '',
            'data_nascimento': cliente.data_nascimento.isoformat() if cliente.data_nascimento else '',
        }
    return JsonResponse({'ok': True, 'prefill': prefill})


@ratelimit(key='ip', rate='10/m', method='POST', block=True)
def confirmar_agendamento(request):
    """
    Processa a confirmação do agendamento SEM login.
    Recebe: nome, telefone, procedimento, profissional, datetime.
    Cria/encontra cliente pelo telefone e agenda.
    Protecoes: honeypot, Turnstile, OTP (se email existir) e slot lock.
    """
    if request.method != 'POST':
        return redirect('shivazen:agendamento_publico')

    # Honeypot — bots que preenchem tudo falham aqui
    if request.POST.get('website', '').strip():
        logger.info('[BOOKING] honeypot triggered ip=%s', _client_ip(request))
        return redirect('shivazen:agendamento_publico')

    # Turnstile CAPTCHA
    if turnstile_enabled():
        captcha_token = request.POST.get('cf-turnstile-response', '')
        if not verificar_turnstile(captcha_token, ip=_client_ip(request)):
            messages.error(request, 'Validacao de seguranca falhou. Tente novamente.')
            return redirect('shivazen:agendamento_publico')

    nome = request.POST.get('nome', '').strip()
    telefone = request.POST.get('telefone', '').strip()
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
        return redirect('shivazen:agendamento_publico')

    # Se ja existe cliente com esse email, exige OTP verificado
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
            messages.error(request, 'Verifique seu e-mail com o codigo enviado antes de confirmar.')
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

        # Feriado/recesso: bloqueia agendamento (defesa em profundidade alem da listagem de horarios).
        from ..models import Feriado
        if Feriado.objects.filter(data=data_hora.date(), bloqueia_agendamento=True).exists():
            messages.error(request, 'Esta data e um feriado/recesso — nao aceitamos agendamentos.')
            return redirect('shivazen:agendamento_publico')

        # Lock de reserva de slot (cache, 30s) — reduz janela de race entre
        # dois clicks simultaneos no mesmo horario antes do SELECT FOR UPDATE.
        slot_key = f'booking_slot:{profissional_id}:{datetime_str}'
        if not cache.add(slot_key, '1', timeout=30):
            messages.error(request, 'Este horario esta sendo confirmado por outra pessoa. Tente outro.')
            return redirect('shivazen:agendamento_publico')

        # Transacao atomica: cliente + atendimento + notificacao de termo
        # devem persistir juntos ou nenhum (evita Cliente orfao em falha).
        with transaction.atomic():
            agora = timezone.now()
            ip_origem = _client_ip(request)
            defaults = {
                'nome_completo': nome,
                'data_nascimento': data_nascimento,
                'email': email,
                'ativo': True,
            }
            if consent_email_marketing:
                defaults.update({
                    'consent_email_marketing': True,
                    'consent_email_marketing_at': agora,
                    'consent_email_marketing_ip': ip_origem,
                })
            if consent_whatsapp_nps:
                defaults.update({
                    'consent_whatsapp_nps': True,
                    'consent_whatsapp_nps_at': agora,
                    'consent_whatsapp_nps_ip': ip_origem,
                })
            if consent_whatsapp_confirmacao:
                defaults.update({
                    'consent_whatsapp_confirmacao': True,
                    'consent_whatsapp_confirmacao_at': agora,
                    'consent_whatsapp_confirmacao_ip': ip_origem,
                })

            cliente, created = Cliente.objects.select_for_update().get_or_create(
                telefone=telefone,
                defaults=defaults,
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
                if consent_email_marketing and not cliente.consent_email_marketing:
                    cliente.consent_email_marketing = True
                    cliente.consent_email_marketing_at = agora
                    cliente.consent_email_marketing_ip = ip_origem
                    atualizar = True
                if consent_whatsapp_nps and not cliente.consent_whatsapp_nps:
                    cliente.consent_whatsapp_nps = True
                    cliente.consent_whatsapp_nps_at = agora
                    cliente.consent_whatsapp_nps_ip = ip_origem
                    atualizar = True
                if consent_whatsapp_confirmacao and not cliente.consent_whatsapp_confirmacao:
                    cliente.consent_whatsapp_confirmacao = True
                    cliente.consent_whatsapp_confirmacao_at = agora
                    cliente.consent_whatsapp_confirmacao_ip = ip_origem
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

        # --- Envio de emails via Celery (nao bloqueia request) ---
        from ..tasks import send_email_async

        data_formatada = data_hora.strftime('%d/%m/%Y as %H:%M')
        dados_confirmacao = {
            'nome': nome,
            'procedimento': procedimento.nome,
            'profissional': profissional.nome,
            'data_hora': data_formatada,
            'valor': f"R$ {valor:.2f}" if valor else 'A consultar',
        }
        site_url = SITE_URL.rstrip('/')

        def _enqueue(fn_name, *args):
            try:
                send_email_async.delay(fn_name, *args)
            except Exception as e:
                logger.warning('[EMAIL] Celery indisponivel, fallback sync: %s', e)
                from ..utils import email as _em
                getattr(_em, fn_name)(*args)

        if dados_termo and email:
            _enqueue('enviar_termos_pendentes_email', email, dados_termo)

        # Confirmacao de agendamento: exibida na tela + "Meus Agendamentos".
        # Email reservado para promocoes/marketing/pacotes/compras.
        # Lembrete D-1 e NPS via WhatsApp (se consent).

        prof_email_obj = getattr(profissional, 'usuario', None)
        prof_email = prof_email_obj.email if prof_email_obj else None
        if prof_email:
            _enqueue('enviar_aprovacao_profissional_email', prof_email, {
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
        # Limpa OTP session apos sucesso
        request.session.pop('otp_agendamento_email', None)
        request.session.pop('otp_agendamento_expira', None)

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
    """Listagem autenticada via OTP por email."""
    email = request.session.get('meus_agendamentos_email')
    if not email:
        return render(request, 'agenda/meus_agendamentos.html', {
            'step': '1',
            'turnstile_site_key': turnstile_site_key(),
            'turnstile_enabled': turnstile_enabled(),
        })

    clientes = Cliente.objects.filter(email__iexact=email, ativo=True)
    agendamentos = Atendimento.objects.filter(
        cliente__in=clientes
    ).select_related('profissional', 'procedimento').order_by('-data_hora_inicio')

    agendamentos_futuros = agendamentos.filter(
        data_hora_inicio__gte=timezone.now(),
        status__in=['PENDENTE', 'AGENDADO', 'CONFIRMADO']
    )
    agendamentos_passados = agendamentos.filter(
        Q(data_hora_inicio__lt=timezone.now()) | Q(status__in=['REALIZADO', 'CANCELADO', 'FALTOU', 'REAGENDADO'])
    )

    return render(request, 'agenda/meus_agendamentos.html', {
        'step': '3',
        'email': email,
        'agendamentos_futuros': agendamentos_futuros[:20],
        'agendamentos_passados': agendamentos_passados.distinct()[:20],
        'whatsapp_numero': WHATSAPP_NUMERO,
    })


@require_POST
@ratelimit(key='ip', rate='5/m', method='POST', block=True)
def meus_agendamentos_enviar_otp(request):
    """Envia OTP para login em 'Meus Agendamentos'."""
    email = (request.POST.get('email') or '').strip().lower()
    captcha_token = request.POST.get('cf-turnstile-response', '')

    if turnstile_enabled() and not verificar_turnstile(captcha_token, ip=_client_ip(request)):
        return JsonResponse({'ok': False, 'erro': 'captcha'}, status=400)
    if not email or '@' not in email:
        return JsonResponse({'ok': False, 'erro': 'email_invalido'}, status=400)

    # OTP via SMS (Zenvia) exclusivo. Requer telefone cadastrado.
    telefone = None
    cliente = Cliente.objects.filter(email__iexact=email, ativo=True).only('telefone').first()
    if cliente and cliente.telefone:
        telefone = cliente.telefone
    if not telefone:
        return JsonResponse({'ok': False, 'erro': 'telefone_nao_cadastrado'}, status=400)

    ok, motivo, canal_usado = otp_service.solicitar_otp(
        email,
        request=request,
        proposito=OtpCode.PROPOSITO_LOGIN,
        telefone=telefone,
        canal_preferido=OtpCode.CANAL_SMS,
    )
    if not ok and motivo == 'aguarde':
        return JsonResponse({'ok': False, 'erro': 'aguarde'}, status=429)
    if not ok:
        return JsonResponse({'ok': False, 'erro': motivo}, status=400)
    return JsonResponse({'ok': True, 'canal': canal_usado})


@require_POST
@ratelimit(key='ip', rate='10/m', method='POST', block=True)
def meus_agendamentos_verificar_otp(request):
    """Valida OTP; cria sessao se ok."""
    email = (request.POST.get('email') or '').strip().lower()
    codigo = (request.POST.get('codigo') or '').strip()

    ok, motivo = otp_service.verificar_otp(email, codigo, proposito=OtpCode.PROPOSITO_LOGIN)
    if not ok:
        return JsonResponse({'ok': False, 'erro': motivo}, status=400)

    request.session['meus_agendamentos_email'] = email
    request.session.set_expiry(3600)  # 1h
    return JsonResponse({'ok': True, 'redirect': '/meus-agendamentos/'})


def meus_agendamentos_logout(request):
    request.session.pop('meus_agendamentos_email', None)
    return redirect('shivazen:meus_agendamentos')


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

    # Feriado/recesso: impede reagendar para data bloqueada.
    from ..models import Feriado
    if Feriado.objects.filter(data=nova_data.date(), bloqueia_agendamento=True).exists():
        messages.error(request, 'A data escolhida e um feriado/recesso. Escolha outro dia.')
        return redirect('shivazen:reagendar_agendamento', token=token)

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



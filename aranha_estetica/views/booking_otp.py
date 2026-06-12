"""OTP endpoints para fluxo publico de agendamento + login Meus Agendamentos.

Canal exclusivo SMS via Zenvia. Email apenas como identificador do cliente.
"""
import logging
from datetime import timedelta

from django.http import JsonResponse
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from ..models import Cliente, CodigoOtp
from ..services import otp_service
from ..utils.captcha import turnstile_enabled, verificar_turnstile
from ..utils.pii import mask_email, mask_telefone
from ..utils.security import client_ip as _client_ip

logger = logging.getLogger(__name__)


@require_POST
@ratelimit(key='ip', rate='5/m', method='POST', block=True)
def solicitar_otp_agendamento(request):
    """AJAX: envia OTP via SMS (Zenvia). Telefone obrigatorio. Email opcional."""
    email = (request.POST.get('email') or '').strip().lower()
    telefone = (request.POST.get('telefone') or '').strip() or None
    captcha_token = request.POST.get('cf-turnstile-response', '')

    if turnstile_enabled() and not verificar_turnstile(captcha_token, ip=_client_ip(request)):
        return JsonResponse({'ok': False, 'erro': 'captcha'}, status=400)

    if not telefone:
        return JsonResponse({'ok': False, 'erro': 'telefone_ausente'}, status=400)

    # Lookup por forma canonica (digits-only) — telefone e normalizado no save()
    from ..validators import normalizar_telefone
    digitos = normalizar_telefone(telefone)
    cliente_existente = (
        Cliente.objects.filter(telefone=digitos, ativo=True).first()
        if len(digitos) >= 10 else None
    )
    if cliente_existente and not email:
        email = (cliente_existente.email or '').lower()

    # Email pseudo p/ chave do OTP quando cliente nao tem email (cadastro via SMS-only)
    if not email or '@' not in email:
        email = CodigoOtp.email_para_telefone(telefone)

    existe = Cliente.objects.filter(email__iexact=email, ativo=True).exists() or bool(cliente_existente)

    ok, motivo, canal_usado = otp_service.solicitar_otp(
        email,
        request=request,
        proposito=CodigoOtp.PROPOSITO_AGENDAMENTO,
        telefone=telefone,
        canal_preferido=CodigoOtp.CANAL_SMS,
    )
    if not ok and motivo == 'aguarde':
        return JsonResponse({'ok': False, 'erro': 'aguarde'}, status=429)
    if not ok:
        logger.info('otp_solicitar_falhou', extra={
            'email': mask_email(email), 'telefone': mask_telefone(telefone), 'motivo': motivo,
        })
        return JsonResponse({'ok': False, 'erro': motivo}, status=400)
    return JsonResponse({'ok': True, 'cliente_existente': existe, 'canal': canal_usado})


@require_POST
@ratelimit(key='ip', rate='10/m', method='POST', block=True)
def verificar_otp_agendamento(request):
    """AJAX: valida codigo; se cliente existe, devolve pre-fill."""
    email = (request.POST.get('email') or '').strip().lower()
    codigo = (request.POST.get('codigo') or '').strip()

    ok, motivo = otp_service.verificar_otp(email, codigo, proposito=CodigoOtp.PROPOSITO_AGENDAMENTO)
    if not ok:
        return JsonResponse({'ok': False, 'erro': motivo}, status=400)

    request.session['otp_agendamento_email'] = email
    request.session['otp_agendamento_expira'] = (timezone.now() + timedelta(minutes=30)).isoformat()

    cliente = Cliente.objects.filter(email__iexact=email, ativo=True).first()
    prefill = None
    if cliente:
        prefill = {
            'nome': cliente.nome,
            'telefone': cliente.telefone or '',
            'data_nascimento': cliente.data_nascimento.isoformat() if cliente.data_nascimento else '',
        }
    return JsonResponse({'ok': True, 'prefill': prefill})


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

    telefone = None
    cliente = Cliente.objects.filter(email__iexact=email, ativo=True).only('telefone').first()
    if cliente and cliente.telefone:
        telefone = cliente.telefone
    if not telefone:
        return JsonResponse({'ok': False, 'erro': 'telefone_nao_cadastrado'}, status=400)

    ok, motivo, canal_usado = otp_service.solicitar_otp(
        email,
        request=request,
        proposito=CodigoOtp.PROPOSITO_LOGIN,
        telefone=telefone,
        canal_preferido=CodigoOtp.CANAL_SMS,
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

    ok, motivo = otp_service.verificar_otp(email, codigo, proposito=CodigoOtp.PROPOSITO_LOGIN)
    if not ok:
        return JsonResponse({'ok': False, 'erro': motivo}, status=400)

    request.session['meus_agendamentos_email'] = email
    request.session.set_expiry(3600)
    return JsonResponse({'ok': True, 'redirect': '/meus-agendamentos/'})


def meus_agendamentos_logout(request):
    request.session.pop('meus_agendamentos_email', None)
    return redirect('aranha:meus_agendamentos')

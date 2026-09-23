"""Setup e gestao de 2FA (TOTP) da equipe (admin e profissional)."""
import base64
import io
from urllib.parse import urlencode

import qrcode
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from django_otp.plugins.otp_static.models import StaticDevice
from django_otp.plugins.otp_totp.models import TOTPDevice
from django_ratelimit.decorators import ratelimit

from ..utils import dois_fatores
from ..utils.audit import registrar_log


def _destino_padrao(user):
    return 'aranha:painel_overview' if user.is_staff else 'aranha:profissional_agenda'


def _safe_next(request, raw, fallback='aranha:painel_overview'):
    """Valida ?next= contra open redirect; cai no fallback se externo/invalido."""
    return dois_fatores.safe_next(request, raw, fallback)


def _url_setup(next_url):
    base = reverse('aranha:admin_2fa_setup')
    return f'{base}?{urlencode({"next": next_url})}' if next_url else base


@login_required
@ratelimit(key='user', rate='10/m', method='POST', block=True)
def admin_2fa_setup(request):
    """Tela de configuracao de 2FA TOTP — gera QR code e valida primeiro token."""
    device_confirmado = TOTPDevice.objects.filter(user=request.user, confirmed=True).first()
    device_pendente = TOTPDevice.objects.filter(user=request.user, confirmed=False).first()
    next_url = _safe_next(request, request.POST.get('next') or request.GET.get('next'), '')

    # SEGURANCA: com 2FA ativo, gerenciar (trocar/desativar) exige a sessao ja
    # verificada — senao so a senha bastaria para remover o segundo fator.
    if device_confirmado and not dois_fatores.sessao_verificada(request):
        challenge = reverse('aranha:admin_2fa_challenge')
        return redirect(f'{challenge}?{urlencode({"next": request.path})}')

    if request.method == 'POST':
        acao = request.POST.get('acao', '')

        if acao == 'gerar':
            TOTPDevice.objects.filter(user=request.user, confirmed=False).delete()
            TOTPDevice.objects.create(
                user=request.user, name=f'{request.user.email}-totp', confirmed=False
            )
            messages.info(request, 'Escaneie o QR code com o app autenticador e confirme o código.')
            return redirect(_url_setup(next_url))

        if acao == 'confirmar':
            if not device_pendente:
                messages.error(request, 'Nenhum dispositivo pendente. Gere um novo QR code.')
                return redirect(_url_setup(next_url))

            token = request.POST.get('token', '').strip().replace(' ', '')
            if device_pendente.verify_token(token):
                device_pendente.confirmed = True
                device_pendente.save()
                TOTPDevice.objects.filter(
                    user=request.user, confirmed=True
                ).exclude(pk=device_pendente.pk).delete()
                registrar_log(request.user, 'Ativou 2FA TOTP', 'totpdevice', device_pendente.pk)
                # Acabou de provar a posse do novo dispositivo: sessao verificada
                dois_fatores.marcar_verificado(request, device_pendente)
                messages.success(request, '2FA ativado com sucesso!')
                if next_url:
                    return redirect(next_url)
                return redirect('aranha:admin_2fa_setup')

            messages.error(request, 'Código inválido. Tente novamente.')
            return redirect(_url_setup(next_url))

        if acao == 'desativar':
            if device_confirmado:
                # Defesa extra: confirmar a posse do fator no proprio pedido
                if not dois_fatores.verificar_token(request.user, request.POST.get('token')):
                    messages.error(request, 'Código inválido. Digite o código atual do app para desativar.')
                    return redirect('aranha:admin_2fa_setup')
                TOTPDevice.objects.filter(user=request.user).delete()
                StaticDevice.objects.filter(user=request.user).delete()
                registrar_log(request.user, 'Desativou 2FA TOTP', 'totpdevice', None)
                if dois_fatores.obrigatorio_para(request.user):
                    # ADMIN nao fica sem 2FA: cadastra o novo aparelho em seguida
                    request.session.pop(dois_fatores.SESSION_VERIFICADO, None)
                    request.session[dois_fatores.SESSION_CADASTRO_PENDENTE] = True
                    messages.warning(request, '2FA removido. Cadastre o novo aparelho para continuar.')
                else:
                    messages.success(request, '2FA desativado.')
            return redirect('aranha:admin_2fa_setup')

    qr_b64 = None
    secret_b32 = None
    if device_pendente and not device_confirmado:
        uri = device_pendente.config_url
        img = qrcode.make(uri)
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        qr_b64 = base64.b64encode(buf.getvalue()).decode()
        secret_b32 = base64.b32encode(bytes.fromhex(device_pendente.key)).decode()

    context = {
        'device_confirmado': device_confirmado,
        'device_pendente': device_pendente,
        'qr_b64': qr_b64,
        'secret_b32': secret_b32,
        'next': next_url,
        'cadastro_obrigatorio': bool(request.session.get(dois_fatores.SESSION_CADASTRO_PENDENTE)),
        'obrigatorio': dois_fatores.obrigatorio_para(request.user),
    }
    return render(request, 'painel/2fa_setup.html', context)


@login_required
@require_POST
@ratelimit(key='user', rate='5/m', method='POST', block=True)
def admin_2fa_verify(request):
    """Verifica token 2FA pos-login (se usuario tiver 2FA ativo)."""
    next_url = _safe_next(request, request.POST.get('next'), _destino_padrao(request.user))

    if not dois_fatores.tem_2fa(request.user):
        return redirect(next_url)

    # Aceita o TOTP de qualquer device confirmado e os codigos de backup
    device = dois_fatores.verificar_token(request.user, request.POST.get('token'))
    if device is not None:
        # Flag propria + django_otp.login (libera o /django-admin-sv/) + nova chave
        dois_fatores.marcar_verificado(request, device)
        registrar_log(request.user, 'Validou 2FA', 'totpdevice', device.pk)
        return redirect(next_url)

    messages.error(request, 'Código 2FA inválido.')
    challenge = reverse('aranha:admin_2fa_challenge')
    nxt = request.POST.get('next') or ''
    return redirect(f'{challenge}?{urlencode({"next": nxt})}' if nxt else challenge)


@login_required
def admin_2fa_challenge(request):
    """Formulario de input do codigo 2FA pos-login."""
    next_url = _safe_next(request, request.GET.get('next'), fallback='')
    if not dois_fatores.tem_2fa(request.user):
        return redirect(next_url or _destino_padrao(request.user))
    if dois_fatores.sessao_verificada(request):
        return redirect(next_url or _destino_padrao(request.user))

    return render(request, 'painel/2fa_challenge.html', {'next': next_url})

"""Setup e gestao de 2FA (TOTP) para usuarios admin."""
import base64
import io

import qrcode
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST
from django_otp.plugins.otp_totp.models import TOTPDevice

from ..utils.audit import registrar_log


@login_required
def admin_2fa_setup(request):
    """Tela de configuracao de 2FA TOTP — gera QR code e valida primeiro token."""
    device_confirmado = TOTPDevice.objects.filter(user=request.user, confirmed=True).first()
    device_pendente = TOTPDevice.objects.filter(user=request.user, confirmed=False).first()

    if request.method == 'POST':
        acao = request.POST.get('acao', '')

        if acao == 'gerar':
            TOTPDevice.objects.filter(user=request.user, confirmed=False).delete()
            device = TOTPDevice.objects.create(
                user=request.user, name=f'{request.user.email}-totp', confirmed=False
            )
            messages.info(request, 'Escaneie o QR code com Google Authenticator/Authy e confirme o codigo.')
            return redirect('aranha:admin_2fa_setup')

        if acao == 'confirmar':
            if not device_pendente:
                messages.error(request, 'Nenhum dispositivo pendente. Gere um novo QR code.')
                return redirect('aranha:admin_2fa_setup')

            token = request.POST.get('token', '').strip().replace(' ', '')
            if device_pendente.verify_token(token):
                device_pendente.confirmed = True
                device_pendente.save()
                TOTPDevice.objects.filter(
                    user=request.user, confirmed=True
                ).exclude(pk=device_pendente.pk).delete()
                registrar_log(request.user, 'Ativou 2FA TOTP', 'totpdevice', device_pendente.pk)
                messages.success(request, '2FA ativado com sucesso!')
                return redirect('aranha:admin_2fa_setup')

            messages.error(request, 'Codigo invalido. Tente novamente.')
            return redirect('aranha:admin_2fa_setup')

        if acao == 'desativar':
            if device_confirmado:
                TOTPDevice.objects.filter(user=request.user).delete()
                registrar_log(request.user, 'Desativou 2FA TOTP', 'totpdevice', None)
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
    }
    return render(request, 'painel/2fa_setup.html', context)


@login_required
@require_POST
def admin_2fa_verify(request):
    """Verifica token 2FA pos-login (se usuario tiver 2FA ativo)."""
    token = request.POST.get('token', '').strip().replace(' ', '')
    next_url = request.POST.get('next') or 'aranha:painel_overview'

    device = TOTPDevice.objects.filter(user=request.user, confirmed=True).first()
    if not device:
        return redirect(next_url)

    if device.verify_token(token):
        request.session['otp_verified'] = True
        registrar_log(request.user, 'Validou 2FA', 'totpdevice', device.pk)
        return redirect(next_url)

    messages.error(request, 'Codigo 2FA invalido.')
    return redirect('aranha:admin_2fa_challenge')


@login_required
def admin_2fa_challenge(request):
    """Formulario de input do codigo 2FA pos-login."""
    next_url = request.GET.get('next', '')
    device = TOTPDevice.objects.filter(user=request.user, confirmed=True).first()
    if not device:
        return redirect(next_url or 'aranha:painel_overview')

    return render(request, 'painel/2fa_challenge.html', {'next': next_url})

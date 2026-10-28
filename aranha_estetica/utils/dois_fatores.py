"""Helpers do 2FA (TOTP) da equipe — usados pelo middleware, login e telas de 2FA.

Sessao "verificada" = passou pelo desafio TOTP nesta sessao, registrado via
django_otp.login() — o mesmo que o AdminSiteOTPRequired (/django-admin-sv/)
exige em request.user.is_verified(). A flag 'otp_verified' segue gravada so
por compatibilidade (nao e mais consultada).
"""
from django.conf import settings

from .seguranca import safe_next  # noqa: F401  (re-export: validador unico de ?next=)

SESSION_VERIFICADO = 'otp_verified'
# Marcado no login de ADMIN sem TOTP quando o 2FA e obrigatorio: o middleware
# prende a sessao na tela de cadastro ate o primeiro codigo ser confirmado.
SESSION_CADASTRO_PENDENTE = '2fa_cadastro_pendente'
# Gravada por usuario_login em TODA sessao da equipe (inclusive nas abertas
# antes do 2FA obrigatorio existir). E a unica porta de entrada publicada: o
# login do /django-admin-sv/ redireciona p/ ele e as rotas do two_factor/DRF
# nao estao expostas. O middleware confere a obrigatoriedade a cada request
# nessas sessoes; sessao sem a marca so nasce de force_login/client.login.
SESSION_LOGIN_EQUIPE = 'usuario_id'


def obrigatorio_para(user) -> bool:
    """2FA obrigatorio?

    ADMIN: setting ADMIN_2FA_OBRIGATORIO (default: fora de DEBUG).
    PROFISSIONAL vinculado (le prontuario e alertas de saude, LGPD art. 11):
    setting PROFISSIONAL_2FA_OBRIGATORIO (ligado em prod; desligado em dev/testes).
    """
    if getattr(user, 'is_staff', False):
        return bool(getattr(settings, 'ADMIN_2FA_OBRIGATORIO', not settings.DEBUG))
    if getattr(user, 'profissional_id', None):
        return bool(getattr(settings, 'PROFISSIONAL_2FA_OBRIGATORIO', False))
    return False


def sessao_do_login_equipe(request) -> bool:
    """Sessao aberta pelo login da equipe (usuario_login) deste mesmo usuario."""
    pk = getattr(request.user, 'pk', None)
    return pk is not None and request.session.get(SESSION_LOGIN_EQUIPE) == pk


def tem_2fa(user) -> bool:
    """Usuario tem TOTP confirmado."""
    from django_otp.plugins.otp_totp.models import TOTPDevice
    return TOTPDevice.objects.filter(user=user, confirmed=True).exists()


def sessao_verificada(request) -> bool:
    """Fonte da verdade = django_otp (device da sessao ainda existe e e do usuario).

    Mesma regra do AdminSiteOTPRequired: evita loop painel "verificado" x admin
    "nao verificado" e revoga a sessao se o device for removido/recriado.
    """
    is_verified = getattr(request.user, 'is_verified', None)
    return bool(callable(is_verified) and is_verified())


def marcar_verificado(request, device) -> None:
    """Marca a sessao como verificada (flag propria + django_otp) e troca a chave."""
    from django_otp import login as otp_login
    otp_login(request, device)
    request.session[SESSION_VERIFICADO] = True
    request.session.pop(SESSION_CADASTRO_PENDENTE, None)
    # Troca o id da sessao ao elevar o nivel de autenticacao (anti-fixation)
    request.session.cycle_key()


def verificar_token(user, token):
    """Confere o codigo em todos os devices confirmados (TOTP + backup do setup_2fa).

    Devolve o device que aceitou ou None. Cobre a recuperacao via
    `setup_2fa <email> --force`, que cria um device novo ao lado do antigo.
    """
    from django_otp import match_token
    token = (token or '').strip().replace(' ', '')
    if not token:
        return None
    return match_token(user, token)


QTD_CODIGOS_BACKUP = 10


def gerar_codigos_backup(user) -> list[str]:
    """Troca os codigos de backup do usuario por QTD_CODIGOS_BACKUP novos e devolve-os.

    Um StaticDevice 'backup' por usuario; cada codigo vale uma vez (verificar_token aceita).
    Quem chama mostra a lista uma unica vez: nao guardar em sessao nem em log.
    """
    from django_otp.plugins.otp_static.models import StaticDevice, StaticToken

    device, _ = StaticDevice.objects.get_or_create(user=user, name='backup', defaults={'confirmed': True})
    if not device.confirmed:
        device.confirmed = True
        device.save(update_fields=['confirmed'])
    device.token_set.all().delete()
    codigos = [StaticToken.random_token() for _ in range(QTD_CODIGOS_BACKUP)]
    StaticToken.objects.bulk_create([StaticToken(device=device, token=c) for c in codigos])
    return codigos


def codigos_backup_restantes(user) -> int:
    from django_otp.plugins.otp_static.models import StaticToken
    return StaticToken.objects.filter(device__user=user, device__confirmed=True).count()

"""Helpers do 2FA (TOTP) da equipe — usados pelo middleware, login e telas de 2FA.

Sessao "verificada" = passou pelo desafio TOTP nesta sessao, registrado via
django_otp.login() — o mesmo que o AdminSiteOTPRequired (/django-admin-sv/)
exige em request.user.is_verified(). A flag 'otp_verified' segue gravada so
por compatibilidade (nao e mais consultada).
"""
from django.conf import settings

from .security import safe_next  # noqa: F401  (re-export: validador unico de ?next=)

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
    setting PROFISSIONAL_2FA_OBRIGATORIO (opt-in; default desligado).
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

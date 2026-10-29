"""Cloudflare Turnstile — captcha antes do envio de SMS (protege a cota e o custo do OTP).

Chaves em settings.TURNSTILE_SITE_KEY / TURNSTILE_SECRET_KEY (lidas na chamada). Falha fechada:
fora de DEBUG, sem a chave secreta, erro de rede ou resposta invalida da Cloudflare, o token e
recusado. Em DEBUG sem chave passa (dev local sem captcha).
"""
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

VERIFY_URL = 'https://challenges.cloudflare.com/turnstile/v0/siteverify'
TIMEOUT_SEGUNDOS = 5


def turnstile_site_key() -> str:
    return settings.TURNSTILE_SITE_KEY


def turnstile_enabled() -> bool:
    return bool(settings.TURNSTILE_SECRET_KEY)


def verificar_turnstile(token, ip=None) -> bool:
    """True se a Cloudflare confirmou o token. Sem chave: True so em DEBUG."""
    secret = settings.TURNSTILE_SECRET_KEY
    if not secret:
        if settings.DEBUG:
            return True
        logger.warning('turnstile_secret_ausente_em_producao')
        return False
    if not token:
        return False
    try:
        resp = requests.post(
            VERIFY_URL,
            data={'secret': secret, 'response': token, 'remoteip': ip or ''},
            timeout=TIMEOUT_SEGUNDOS,
        )
        data = resp.json()
    except (requests.exceptions.RequestException, ValueError) as exc:
        logger.error('turnstile_erro_http', extra={'erro': type(exc).__name__})
        return False
    if not isinstance(data, dict) or data.get('success') is not True:
        codigos = data.get('error-codes') if isinstance(data, dict) else None
        logger.info('turnstile_falha_validacao', extra={'codes': codigos})
        return False
    return True


CAMPO_TOKEN = 'cf-turnstile-response'


def captcha_invalido(request) -> bool:
    """POST sem o token do widget (ou com token recusado) quando o captcha esta ligado.

    Sem a chave secreta o captcha esta desligado (dev/testes): nada a conferir.
    """
    if not turnstile_enabled():
        return False
    from .seguranca import client_ip
    return not verificar_turnstile(request.POST.get(CAMPO_TOKEN, ''), ip=client_ip(request))

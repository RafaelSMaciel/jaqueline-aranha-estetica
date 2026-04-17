"""Cloudflare Turnstile verification helper.

Usa env TURNSTILE_SECRET_KEY + TURNSTILE_SITE_KEY. Em DEBUG sem chave,
retorna True (dev-friendly). Fora de DEBUG sem chave, retorna False
para nao vazar bypass em prod.
"""
import logging
import os

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

VERIFY_URL = 'https://challenges.cloudflare.com/turnstile/v0/siteverify'


def turnstile_site_key():
    return os.environ.get('TURNSTILE_SITE_KEY', '')


def turnstile_enabled():
    return bool(os.environ.get('TURNSTILE_SECRET_KEY'))


def verificar_turnstile(token, ip=None):
    """Retorna True se token valido. Sem chave em DEBUG = True (dev)."""
    secret = os.environ.get('TURNSTILE_SECRET_KEY', '')
    if not secret:
        if settings.DEBUG:
            return True
        logger.warning('[TURNSTILE] secret key ausente em producao')
        return False
    if not token:
        return False
    try:
        resp = requests.post(
            VERIFY_URL,
            data={'secret': secret, 'response': token, 'remoteip': ip or ''},
            timeout=5,
        )
        data = resp.json()
        if not data.get('success'):
            logger.info('[TURNSTILE] falha: %s', data.get('error-codes'))
        return bool(data.get('success'))
    except Exception as e:
        logger.error('[TURNSTILE] erro HTTP: %s', e)
        return False

"""
SMS Notification Service — Plataforma de Clinicas (Provider: Zenvia REST API v2)

Uso atual: apenas OTP de autenticacao/agendamento (canal primario).
Todas as demais mensagens transacionais seguem por email.

Variaveis de ambiente:
  ZENVIA_API_TOKEN     Token X-API-TOKEN da Zenvia
  ZENVIA_FROM          Identificador do remetente (integracao SMS Zenvia)
  ZENVIA_API_URL       URL base (default https://api.zenvia.com/v2/channels/sms/messages)
  SMS_DEV_LOG_ONLY     Se true OU DEBUG, apenas loga sem chamar API
"""
import logging
import os
import time

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

ZENVIA_API_TOKEN = os.environ.get('ZENVIA_API_TOKEN', '')
ZENVIA_FROM = os.environ.get('ZENVIA_FROM', '')
ZENVIA_API_URL = os.environ.get(
    'ZENVIA_API_URL',
    'https://api.zenvia.com/v2/channels/sms/messages',
)
MAX_RETRIES = 3

CLINIC_NAME = os.environ.get('CLINIC_NAME', 'Shiva Zen')

# Rate limit por telefone (anti-abuse): 3 SMS por hora
SMS_MAX_POR_HORA = int(os.environ.get('SMS_MAX_POR_HORA', '3'))
# Rate limit por IP (cross-phone abuse): 10 SMS por hora por IP
SMS_MAX_POR_IP_HORA = int(os.environ.get('SMS_MAX_POR_IP_HORA', '10'))
# Rate limit global (burst protection): 60 SMS por hora
SMS_MAX_GLOBAL_HORA = int(os.environ.get('SMS_MAX_GLOBAL_HORA', '60'))


def _mask(telefone: str) -> str:
    digits = ''.join(ch for ch in (telefone or '') if ch.isdigit())
    if len(digits) <= 4:
        return '***' + digits
    return digits[:2] + '****' + digits[-2:]


def formatar_telefone(telefone: str) -> str:
    """Normaliza telefone para E.164 sem '+' (Zenvia aceita com e sem)."""
    digits = ''.join(ch for ch in (telefone or '') if ch.isdigit())
    if len(digits) in (10, 11):
        digits = '55' + digits
    return digits


def pode_enviar(telefone: str, ip: str = None) -> bool:
    """Rate limit defense-in-depth: telefone + IP + global."""
    tel_fmt = formatar_telefone(telefone)
    key_tel = f'sms_rl:tel:{tel_fmt}'
    key_global = 'sms_rl:global'

    if cache.get(key_tel, 0) >= SMS_MAX_POR_HORA:
        logger.warning('[SMS] rate limit telefone excedido %s', _mask(tel_fmt))
        return False
    if cache.get(key_global, 0) >= SMS_MAX_GLOBAL_HORA:
        logger.warning('[SMS] rate limit global excedido')
        return False
    if ip:
        key_ip = f'sms_rl:ip:{ip}'
        if cache.get(key_ip, 0) >= SMS_MAX_POR_IP_HORA:
            logger.warning('[SMS] rate limit IP excedido %s', ip)
            return False

    try:
        cache.set(key_tel, cache.get(key_tel, 0) + 1, timeout=3600)
        cache.set(key_global, cache.get(key_global, 0) + 1, timeout=3600)
        if ip:
            cache.set(f'sms_rl:ip:{ip}', cache.get(f'sms_rl:ip:{ip}', 0) + 1, timeout=3600)
    except Exception:
        pass
    return True


def enviar_sms(telefone: str, mensagem: str, _tentativa: int = 1) -> bool:
    """Envia SMS via Zenvia. Em dev (sem token ou DEBUG), apenas loga.

    Retorna True em sucesso, False em falha. Com retry exponencial em 5xx.
    """
    telefone_fmt = formatar_telefone(telefone)
    if not telefone_fmt:
        logger.warning('[SMS] telefone invalido')
        return False

    dev_only = bool(getattr(settings, 'DEBUG', False)) or not ZENVIA_API_TOKEN \
        or os.environ.get('SMS_DEV_LOG_ONLY', '').lower() == 'true'
    if dev_only:
        logger.info('[SMS DEV] Para: %s | %s', _mask(telefone_fmt), mensagem[:200])
        return True

    if not ZENVIA_FROM:
        logger.error('[SMS] ZENVIA_FROM nao configurado')
        return False

    payload = {
        'from': ZENVIA_FROM,
        'to': telefone_fmt,
        'contents': [{'type': 'text', 'text': mensagem}],
    }
    headers = {
        'X-API-TOKEN': ZENVIA_API_TOKEN,
        'Content-Type': 'application/json',
    }

    try:
        response = requests.post(ZENVIA_API_URL, json=payload, headers=headers, timeout=10)
        if response.status_code in (200, 201, 202):
            logger.info('[SMS] Enviado para %s', _mask(telefone_fmt))
            return True
        if response.status_code >= 500 and _tentativa < MAX_RETRIES:
            wait = 2 ** _tentativa
            logger.warning('[SMS] Erro %d, retry %d/%d em %ds',
                           response.status_code, _tentativa, MAX_RETRIES, wait)
            time.sleep(wait)
            return enviar_sms(telefone, mensagem, _tentativa=_tentativa + 1)
        logger.error('[SMS] Erro %d: %s', response.status_code, response.text[:200])
        return False
    except requests.exceptions.Timeout:
        if _tentativa < MAX_RETRIES:
            wait = 2 ** _tentativa
            time.sleep(wait)
            return enviar_sms(telefone, mensagem, _tentativa=_tentativa + 1)
        logger.error('[SMS] Timeout apos %d tentativas', MAX_RETRIES)
        return False
    except requests.exceptions.RequestException as e:
        logger.error('[SMS] Falha: %s', e)
        return False


def enviar_otp_sms(telefone: str, codigo: str, ip: str = None) -> bool:
    """Envia codigo OTP curto via SMS."""
    if not pode_enviar(telefone, ip=ip):
        return False
    mensagem = (
        f'{CLINIC_NAME}: seu codigo de verificacao e {codigo}. '
        f'Valido por 10 min. Nao compartilhe.'
    )
    return enviar_sms(telefone, mensagem)

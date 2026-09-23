"""
SMS Notification Service — Plataforma de Clinicas (Provider: Zenvia REST API v2)

Uso atual: apenas OTP de autenticacao/agendamento (canal primario).
Todas as demais mensagens transacionais seguem por email.

Variaveis de ambiente (lidas em tempo de chamada):
  ZENVIA_API_TOKEN     Token X-API-TOKEN da Zenvia
  ZENVIA_FROM          Identificador do remetente (integracao SMS Zenvia)
  ZENVIA_API_URL       URL base (default https://api.zenvia.com/v2/channels/sms/messages)
  SMS_DEV_LOG_ONLY     true = apenas loga sem chamar API (dev/testes)

Falha FECHADA: fora de DEBUG/SMS_DEV_LOG_ONLY e sem provedor configurado,
enviar_sms retorna False (nunca "sucesso" so logando). O conteudo da
mensagem (que carrega o codigo OTP) so vai para o log com DEBUG=True.
"""
import ipaddress
import logging
import os
from typing import Optional

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

ZENVIA_API_URL_DEFAULT = 'https://api.zenvia.com/v2/channels/sms/messages'
# Timeout curto (connect, read): o envio roda dentro do request do OTP e o
# prod tem 1 worker/4 threads — sem retry nem sleep aqui (cliente reenvia).
TIMEOUT = (3, 5)

# Rate limit por telefone (anti-abuse): 3 SMS por hora
SMS_MAX_POR_HORA = int(os.environ.get('SMS_MAX_POR_HORA', '3'))
# Rate limit por IP (cross-phone abuse): 10 SMS por hora por IP
SMS_MAX_POR_IP_HORA = int(os.environ.get('SMS_MAX_POR_IP_HORA', '10'))
# Rate limit global (burst protection): 60 SMS por hora
SMS_MAX_GLOBAL_HORA = int(os.environ.get('SMS_MAX_GLOBAL_HORA', '60'))
# TTL (segundos) da janela de rate limit dos contadores de quota = 1 hora
RATE_LIMIT_TTL = 3600


def _env(nome: str, default: str = '') -> str:
    return (os.environ.get(nome) or default).strip()


def sms_configurado() -> bool:
    """True se as credenciais da Zenvia existem (token + remetente)."""
    return bool(_env('ZENVIA_API_TOKEN') and _env('ZENVIA_FROM'))


def sms_modo_dev() -> bool:
    """Modo log-only: DEBUG ou flag explicita (settings/env SMS_DEV_LOG_ONLY)."""
    if getattr(settings, 'DEBUG', False):
        return True
    if getattr(settings, 'SMS_DEV_LOG_ONLY', False):
        return True
    return _env('SMS_DEV_LOG_ONLY').lower() == 'true'


def sms_disponivel() -> bool:
    """Canal utilizavel agora (provedor configurado ou modo dev)."""
    return sms_modo_dev() or sms_configurado()


def _mask(telefone: str) -> str:
    digits = ''.join(ch for ch in (telefone or '') if ch.isdigit())
    if len(digits) <= 4:
        return '***' + digits
    return digits[:2] + '****' + digits[-2:]


def formatar_telefone(telefone: str) -> str:
    """Normaliza p/ E.164 BR sem '+' (55 + DDD + numero). '' se nao for BR valido.

    Aceita 10/11 digitos (DDD+numero) ou 12/13 ja com DDI 55. Qualquer outro
    formato (internacional, curto, lixo) e recusado — evita SMS pumping.
    """
    digits = ''.join(ch for ch in (telefone or '') if ch.isdigit())
    if len(digits) in (12, 13) and digits.startswith('55'):
        return digits
    if len(digits) in (10, 11):
        return '55' + digits
    return ''


def _chave_ip(ip: str) -> str:
    """Chave da quota por IP. IPv6 agrupa pelo /64 (um unico cliente costuma
    receber o /64 inteiro: por endereco, trocar de IP burlaria a quota)."""
    try:
        addr = ipaddress.ip_address(str(ip).strip())
    except ValueError:
        return str(ip)
    if addr.version == 6:
        if addr.ipv4_mapped:
            return str(addr.ipv4_mapped)
        return f'{ipaddress.ip_network(f"{addr}/64", strict=False).network_address}/64'
    return str(addr)


def pode_enviar(telefone: str, ip: Optional[str] = None) -> bool:
    """Checa limites (telefone + IP + global) SEM consumir quota.
    Apos um envio bem-sucedido, chame registrar_envio() para contabilizar.
    """
    tel_fmt = formatar_telefone(telefone)
    if not tel_fmt:
        return False
    if cache.get(f'sms_rl:tel:{tel_fmt}', 0) >= SMS_MAX_POR_HORA:
        logger.warning('sms_rate_limit_telefone', extra={'telefone_mask': _mask(tel_fmt)})
        return False
    global_atual = cache.get('sms_rl:global', 0)
    if global_atual >= SMS_MAX_GLOBAL_HORA:
        logger.warning('sms_rate_limit_global')
        return False
    if global_atual >= SMS_MAX_GLOBAL_HORA * 0.8 and cache.add('sms_rl:alerta80', 1, RATE_LIMIT_TTL):
        # Alerta 1x/hora: possivel SMS pumping antes de esgotar a quota global.
        logger.error('sms_quota_global_80', extra={'atual': global_atual, 'max': SMS_MAX_GLOBAL_HORA})
    if ip and cache.get(f'sms_rl:ip:{_chave_ip(ip)}', 0) >= SMS_MAX_POR_IP_HORA:
        logger.warning('sms_rate_limit_ip', extra={'ip': ip})
        return False
    return True


def registrar_envio(telefone: str, ip: Optional[str] = None) -> None:
    """Incrementa os contadores de quota de forma ATOMICA (cache.add + incr).
    Chamado somente apos enviar_sms() retornar True — assim a quota nao e
    consumida quando o envio falha, e evita a corrida do read-modify-write.
    """
    tel_fmt = formatar_telefone(telefone)
    chaves = [f'sms_rl:tel:{tel_fmt}', 'sms_rl:global']
    if ip:
        chaves.append(f'sms_rl:ip:{_chave_ip(ip)}')
    for key in chaves:
        try:
            cache.add(key, 0, timeout=RATE_LIMIT_TTL)
            cache.incr(key)
        except ValueError:
            # cache.incr levanta ValueError se a chave expirou entre add e incr.
            pass
        except Exception:
            # Backend de cache indisponivel (ex.: Redis fora): logar para nao
            # desativar a protecao anti-abuso de SMS silenciosamente.
            logger.error(
                'sms_rate_limit_cache_falha',
                extra={'key': key, 'telefone_mask': _mask(tel_fmt)},
                exc_info=True,
            )


def enviar_sms(telefone: str, mensagem: str) -> bool:
    """Envia SMS via Zenvia (1 tentativa, timeout curto). True = aceito pelo provedor.

    Modo dev (DEBUG/SMS_DEV_LOG_ONLY): apenas loga e retorna True.
    Sem provedor configurado fora do modo dev: retorna False (falha fechada).
    """
    telefone_fmt = formatar_telefone(telefone)
    if not telefone_fmt:
        logger.warning('sms_telefone_invalido')
        return False

    if sms_modo_dev():
        extra = {'telefone_mask': _mask(telefone_fmt), 'tamanho': len(mensagem)}
        if getattr(settings, 'DEBUG', False):
            # Preview (com o codigo) so no dev local interativo — nunca em prod/testes.
            extra['preview'] = mensagem[:200]
        logger.info('sms_dev_log', extra=extra)
        return True

    token = _env('ZENVIA_API_TOKEN')
    remetente = _env('ZENVIA_FROM')
    if not token or not remetente:
        logger.error('sms_zenvia_nao_configurado')
        return False

    payload = {
        'from': remetente,
        'to': telefone_fmt,
        'contents': [{'type': 'text', 'text': mensagem}],
    }
    headers = {
        'X-API-TOKEN': token,
        'Content-Type': 'application/json',
    }

    try:
        response = requests.post(
            _env('ZENVIA_API_URL', ZENVIA_API_URL_DEFAULT),
            json=payload, headers=headers, timeout=TIMEOUT,
        )
    except requests.exceptions.Timeout:
        logger.error('sms_timeout', extra={'telefone_mask': _mask(telefone_fmt)})
        return False
    except requests.exceptions.RequestException as e:
        logger.error('sms_request_exception', extra={'error': str(e)})
        return False

    if response.status_code in (200, 201, 202):
        logger.info('sms_enviado', extra={'telefone_mask': _mask(telefone_fmt)})
        return True
    logger.error(
        'sms_erro_http',
        extra={'status': response.status_code, 'body': response.text[:200]},
    )
    return False


def enviar_otp_sms(telefone: str, codigo: str, ip: Optional[str] = None) -> bool:
    """Envia codigo OTP curto via SMS (respeita quotas; so contabiliza se enviado)."""
    if not formatar_telefone(telefone):
        logger.warning('sms_telefone_invalido')
        return False
    if not pode_enviar(telefone, ip=ip):
        return False
    from .branding import get_branding
    clinica = get_branding().get('CLINIC_NAME') or 'Clinica'
    # Sem acentos no corpo: SMS GSM-7 (acento vira UCS-2 e dobra o custo).
    mensagem = (
        f'{clinica}: seu codigo de verificacao e {codigo}. '
        f'Valido por 10 min. Nao compartilhe.'
    )
    enviado = enviar_sms(telefone, mensagem)
    if enviado:
        registrar_envio(telefone, ip=ip)
    return enviado

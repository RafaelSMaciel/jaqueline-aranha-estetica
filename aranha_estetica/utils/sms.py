"""
SMS do codigo OTP (agendamento online, "Meus agendamentos" e "Meus dados") — unico uso do SMS;
as demais mensagens seguem por e-mail ou WhatsApp.

Provedores (settings.SMS_PROVEDOR; o resto do sistema so chama enviar_sms/enviar_otp_sms):
  smsgo   (padrao) pre-pago sem mensalidade. SMSGO_API_KEY (a SMSGo-key da conta) vira um token
          Bearer de 48 h (GET /v1/auth/token, guardado no cache); o envio e POST /v1/sms/send/single
          com {phone (+55...), message, from?}. SMSGO_REMETENTE opcional, SMSGO_API_URL.
  zenvia  pacote mensal. ZENVIA_API_TOKEN (header X-API-TOKEN), ZENVIA_FROM, ZENVIA_API_URL.

Comum (settings, lidos em tempo de chamada; env de mesmo nome em settings/base.py):
  SMS_DEV_LOG_ONLY     True = apenas loga sem chamar API (dev/testes/staging)
  SMS_MAX_POR_HORA, SMS_MAX_POR_IP_HORA, SMS_MAX_GLOBAL_HORA   quotas anti-abuso por hora

Falha FECHADA: fora de DEBUG/SMS_DEV_LOG_ONLY e sem provedor configurado,
enviar_sms retorna False (nunca "sucesso" so logando). O conteudo da
mensagem (que carrega o codigo OTP) so vai para o log com DEBUG=True.
"""
import ipaddress
import logging
from typing import Optional

import requests
from django.conf import settings
from django.core.cache import cache

from . import telefone as telefones

logger = logging.getLogger(__name__)

# Timeout curto (connect, read): o envio roda dentro do request do OTP e o
# prod tem 1 worker/4 threads — sem retry nem sleep aqui (cliente reenvia).
TIMEOUT = (3, 5)

# TTL (segundos) da janela de rate limit dos contadores de quota = 1 hora
RATE_LIMIT_TTL = 3600

# Token Bearer da SMSGo: vale 48 h; guardado um pouco menos para nunca usar um vencido.
SMSGO_TOKEN_CACHE = 'sms:smsgo:token'
SMSGO_TOKEN_TTL = 47 * 3600


def sms_configurado() -> bool:
    """True se a credencial do provedor escolhido (settings.SMS_PROVEDOR) existe."""
    if settings.SMS_PROVEDOR == 'zenvia':
        return bool(settings.ZENVIA_API_TOKEN and settings.ZENVIA_FROM)
    return bool(settings.SMSGO_API_KEY)


def sms_modo_dev() -> bool:
    """Modo log-only: DEBUG ou settings.SMS_DEV_LOG_ONLY (testes e staging)."""
    return bool(settings.DEBUG or settings.SMS_DEV_LOG_ONLY)


def sms_disponivel() -> bool:
    """Canal utilizavel agora (provedor configurado ou modo dev)."""
    return sms_modo_dev() or sms_configurado()


_mask = telefones.mascarar


def formatar_telefone(telefone: str) -> str:
    """Destino do SMS em E.164 BR sem '+' (utils/telefone.e164). '' se nao for telefone BR
    valido: internacional, curto ou lixo e recusado — evita SMS pumping."""
    return telefones.e164(telefone)


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
    if cache.get(f'sms_rl:tel:{tel_fmt}', 0) >= settings.SMS_MAX_POR_HORA:
        logger.warning('sms_rate_limit_telefone', extra={'telefone_mask': _mask(tel_fmt)})
        return False
    global_atual = cache.get('sms_rl:global', 0)
    teto_global = settings.SMS_MAX_GLOBAL_HORA
    if global_atual >= teto_global:
        logger.warning('sms_rate_limit_global')
        return False
    if global_atual >= teto_global * 0.8 and cache.add('sms_rl:alerta80', 1, RATE_LIMIT_TTL):
        # Alerta 1x/hora: possivel SMS pumping antes de esgotar a quota global.
        logger.error('sms_quota_global_80', extra={'atual': global_atual, 'max': teto_global})
    if ip and cache.get(f'sms_rl:ip:{_chave_ip(ip)}', 0) >= settings.SMS_MAX_POR_IP_HORA:
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
    """Envia SMS pelo provedor escolhido (1 tentativa, timeout curto). True = aceito pelo provedor.

    Modo dev (DEBUG/SMS_DEV_LOG_ONLY): apenas loga e retorna True.
    Sem provedor configurado fora do modo dev: retorna False (falha fechada).
    """
    telefone_fmt = formatar_telefone(telefone)
    if not telefone_fmt:
        logger.warning('sms_telefone_invalido')
        return False

    if sms_modo_dev():
        extra = {'telefone_mask': _mask(telefone_fmt), 'tamanho': len(mensagem)}
        if getattr(settings, 'DEBUG', False) or getattr(settings, 'AMBIENTE_TESTE', False):
            # Preview (com o codigo) so no dev local e no ambiente de testes publicado (log do
            # Railway, so quem tem acesso ao projeto) — nunca em producao nem na suite.
            extra['preview'] = mensagem[:200]
        if getattr(settings, 'DEBUG', False):
            # o formatter de texto do DEBUG nao imprime os extras: no dev local a mensagem vai
            # na propria linha (sem ela nao da para concluir o agendamento sem provedor de SMS)
            logger.info('sms_dev_log: %s', extra['preview'], extra=extra)
        else:
            logger.info('sms_dev_log', extra=extra)
        return True

    if not sms_configurado():
        logger.error('sms_nao_configurado', extra={'provedor': settings.SMS_PROVEDOR})
        return False
    if settings.SMS_PROVEDOR == 'zenvia':
        return _enviar_zenvia(telefone_fmt, mensagem)
    return _enviar_smsgo(telefone_fmt, mensagem)


def _requisicao(metodo: str, url: str, telefone_fmt: str, **kwargs):
    """Uma chamada HTTP ao provedor com o TIMEOUT curto; None se nao houve resposta (ja logado)."""
    try:
        return requests.request(metodo, url, timeout=TIMEOUT, **kwargs)
    except requests.exceptions.Timeout:
        logger.error('sms_timeout', extra={'telefone_mask': _mask(telefone_fmt)})
    except requests.exceptions.RequestException as e:
        logger.error('sms_request_exception', extra={'error': str(e)})
    return None


def _resultado(response, telefone_fmt: str) -> bool:
    if response is None:
        return False
    provedor = settings.SMS_PROVEDOR
    if 200 <= response.status_code < 300:
        logger.info('sms_enviado', extra={'telefone_mask': _mask(telefone_fmt), 'provedor': provedor})
        return True
    logger.error(
        'sms_erro_http',
        extra={'status': response.status_code, 'body': response.text[:200], 'provedor': provedor},
    )
    return False


def _enviar_zenvia(telefone_fmt: str, mensagem: str) -> bool:
    payload = {
        'from': settings.ZENVIA_FROM,
        'to': telefone_fmt,
        'contents': [{'type': 'text', 'text': mensagem}],
    }
    headers = {'X-API-TOKEN': settings.ZENVIA_API_TOKEN, 'Content-Type': 'application/json'}
    resposta = _requisicao('POST', settings.ZENVIA_API_URL, telefone_fmt, json=payload, headers=headers)
    return _resultado(resposta, telefone_fmt)


def _token_smsgo(telefone_fmt: str, *, renovar: bool = False) -> str:
    """Token Bearer da SMSGo (do cache; troca a chave da conta por um novo se faltar ou se `renovar`)."""
    if not renovar:
        token = cache.get(SMSGO_TOKEN_CACHE)
        if token:
            return token
    resposta = _requisicao('GET', f'{settings.SMSGO_API_URL}/v1/auth/token', telefone_fmt,
                           headers={'SMSGo-key': settings.SMSGO_API_KEY, 'Accept': 'application/json'})
    if resposta is None:
        return ''
    try:
        token = str(resposta.json().get('token') or '') if resposta.status_code == 200 else ''
    except (ValueError, AttributeError):
        token = ''
    if not token:
        logger.error('sms_smsgo_autenticacao_falhou', extra={'status': resposta.status_code})
        return ''
    cache.set(SMSGO_TOKEN_CACHE, token, SMSGO_TOKEN_TTL)
    return token


def _enviar_smsgo(telefone_fmt: str, mensagem: str) -> bool:
    payload = {'phone': f'+{telefone_fmt}', 'message': mensagem}
    if settings.SMSGO_REMETENTE:
        payload['from'] = settings.SMSGO_REMETENTE
    url = f'{settings.SMSGO_API_URL}/v1/sms/send/single'
    resposta = None
    # Token vencido ou revogado (401): troca a chave por um novo uma vez. Nao e retry do envio:
    # com 401 a SMSGo nao aceitou a mensagem.
    for renovar in (False, True):
        token = _token_smsgo(telefone_fmt, renovar=renovar)
        if not token:
            return False
        resposta = _requisicao('POST', url, telefone_fmt, json=payload,
                               headers={'Authorization': f'Bearer {token}', 'Accept': 'application/json'})
        if resposta is None or resposta.status_code != 401:
            break
        cache.delete(SMSGO_TOKEN_CACHE)
    return _resultado(resposta, telefone_fmt)


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

"""Mascaramento de PII para logs.

LGPD: dados pessoais (email, telefone, CPF) nao devem aparecer em log
estruturado/Sentry em forma legivel. Helpers abaixo retornam versao
mascarada determinista — preserva prefixo/sufixo p/ debug, oculta corpo.

Uso::

    from .pii import mask_email, mask_telefone, mask_cpf
    logger.warning('booking_falha', extra={'email': mask_email(email)})
"""
from __future__ import annotations

import re
from typing import Optional

_EMAIL_RX = re.compile(r'^([^@]+)@(.+)$')
_DIGITS_RX = re.compile(r'\D+')


def mask_email(value: Optional[str]) -> str:
    """Mascara local-part. `joao.silva@gmail.com` -> `jo***@gmail.com`."""
    if not value:
        return ''
    m = _EMAIL_RX.match(value.strip())
    if not m:
        return '***'
    local, domain = m.group(1), m.group(2)
    if len(local) <= 2:
        return f'{local[0]}***@{domain}'
    return f'{local[:2]}***@{domain}'


def mask_telefone(value: Optional[str]) -> str:
    """Mascara meio do telefone. `17999990000` -> `17****0000`."""
    if not value:
        return ''
    digits = _DIGITS_RX.sub('', value)
    if len(digits) < 6:
        return '***'
    return f'{digits[:2]}****{digits[-4:]}'


def mask_cpf(value: Optional[str]) -> str:
    """Mascara meio do CPF. `12345678900` -> `123*****00`."""
    if not value:
        return ''
    digits = _DIGITS_RX.sub('', value)
    if len(digits) != 11:
        return '***'
    return f'{digits[:3]}*****{digits[-2:]}'


_SENTRY_PII_KEYS = {
    'email', 'telefone', 'cpf', 'phone', 'celular',
    'data_nascimento', 'nome',
    'destinatario', 'from_email', 'to',
}

# Segmentos de URL que carregam token de acesso (link magico): /confirmar/<tok>/,
# /reagendar/<tok>/, /nps/<tok>/, /termo/<tok>/, /pesquisa/<tok>/,
# /anamnese/<tok>/, /lgpd/unsubscribe/<tok>/ e ?token=... (ICS).
_TOKEN_PATH_RX = re.compile(
    r'(/(?:confirmar|reagendar|nps|termo|pesquisa|anamnese|lgpd/unsubscribe|admin-login/recuperar/[^/]+)/)'
    r'(?!obrigado/)[^/?#]+'
)
_TOKEN_QUERY_RX = re.compile(r'((?:^|[?&])token=)[^&#]*', re.IGNORECASE)


def redigir_tokens_url(valor: Optional[str]) -> str:
    """Troca tokens de links magicos por [token] (URL ou query string)."""
    if not valor or not isinstance(valor, str):
        return valor or ''
    valor = _TOKEN_PATH_RX.sub(lambda m: m.group(1) + '[token]', valor)
    return _TOKEN_QUERY_RX.sub(lambda m: m.group(1) + '[token]', valor)


def _mascarar_valor(chave: str, valor: str) -> str:
    chave = chave.lower()
    if 'email' in chave or '@' in valor:
        return mask_email(valor)
    if 'cpf' in chave:
        return mask_cpf(valor)
    if chave == 'nome':
        return f'{valor[:1]}***' if valor else ''
    return mask_telefone(valor)


def sentry_before_send(event: dict, hint: dict) -> dict:
    """Filtra PII de eventos Sentry antes do envio.

    Mascara campos de `extra` cujas keys batam com a lista PII, remove
    completamente o `request.data` (POST body) e redige tokens de links
    magicos na URL / query string do request.
    """
    extra = event.get('extra') or {}
    for key in list(extra.keys()):
        if key.lower() in _SENTRY_PII_KEYS:
            value = extra[key]
            if isinstance(value, str):
                extra[key] = _mascarar_valor(key, value)

    request = event.get('request') or {}
    if 'data' in request:
        request['data'] = '[scrubbed]'
    for campo in ('url', 'query_string'):
        if isinstance(request.get(campo), str):
            request[campo] = redigir_tokens_url(request[campo])

    return event

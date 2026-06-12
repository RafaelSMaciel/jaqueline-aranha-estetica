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
}


def sentry_before_send(event: dict, hint: dict) -> dict:
    """Filtra PII de eventos Sentry antes do envio.

    Mascara campos `extra`/`contexts.custom` cujas keys batam com lista PII.
    Removido completamente do `request.data` (POST body).
    """
    extra = event.get('extra') or {}
    for key in list(extra.keys()):
        if key.lower() in _SENTRY_PII_KEYS:
            value = extra[key]
            if isinstance(value, str):
                if 'email' in key.lower():
                    extra[key] = mask_email(value)
                elif 'cpf' in key.lower():
                    extra[key] = mask_cpf(value)
                else:
                    extra[key] = mask_telefone(value)

    request = event.get('request') or {}
    if 'data' in request:
        request['data'] = '[scrubbed]'

    return event

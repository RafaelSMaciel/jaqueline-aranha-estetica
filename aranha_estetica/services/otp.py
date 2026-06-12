"""Servico OTP: SMS Zenvia exclusivo (sem fallback email).

Regra: OTP de validacao de agendamento e acesso ao portal sempre via SMS.
Se o cliente nao tem telefone valido, a solicitacao falha — e-mail nao e
utilizado como canal de OTP no fluxo principal.
"""
from __future__ import annotations

import logging
from typing import Optional, Tuple, TYPE_CHECKING

from ..models import CodigoOtp
from .notificacao import OTPService

if TYPE_CHECKING:
    from django.http import HttpRequest

logger = logging.getLogger(__name__)

OtpResult = Tuple[bool, str, Optional[str]]


def _client_ip(request: Optional['HttpRequest']) -> Optional[str]:
    """Extrai IP do request via helper canonico (None se request ausente)."""
    if request is None:
        return None
    from ..utils.security import client_ip
    return client_ip(request)


def solicitar_otp(
    email: str,
    *,
    request: Optional['HttpRequest'] = None,
    proposito: str = CodigoOtp.PROPOSITO_AGENDAMENTO,
    telefone: Optional[str] = None,
    canal_preferido: str = CodigoOtp.CANAL_SMS,
) -> OtpResult:
    """Gera codigo OTP e envia via SMS Zenvia (canal exclusivo).

    Args:
        email: identificador do challenge (nao e o canal de envio).
        request: opcional, usado para extrair IP cliente p/ audit log.
        proposito: PROPOSITO_AGENDAMENTO | PROPOSITO_LOGIN.
        telefone: obrigatorio — sem telefone, retorna falha.
        canal_preferido: ignorado (SMS sempre — mantido p/ compat assinatura).

    Returns:
        Tupla (ok, motivo, canal_usado):
            (True, 'ok', 'SMS')              — sucesso
            (False, 'email_invalido', None)  — email mal-formatado
            (False, 'telefone_ausente', None)— sem telefone
            (False, 'aguarde', None)         — rate limit (TTL ainda ativo)
            (False, 'sms_falha', None)       — Zenvia retornou erro

    Raises:
        Nao propaga — todos erros traduzidos em (False, motivo).
    """
    email = (email or '').strip().lower()
    if not email or '@' not in email:
        return False, 'email_invalido', None

    if not telefone:
        logger.warning(
            'otp_telefone_ausente',
            extra={'email_hash': hash(email), 'proposito': proposito},
        )
        return False, 'telefone_ausente', None

    if not CodigoOtp.pode_reenviar(email, proposito=proposito):
        return False, 'aguarde', None

    ip = _client_ip(request)
    codigo, _obj = CodigoOtp.gerar(
        email, ip=ip, proposito=proposito,
        canal=CodigoOtp.CANAL_SMS, telefone=telefone,
    )
    if OTPService.enviar_codigo(telefone, codigo, ip=ip):
        logger.info('otp_sms_enviado', extra={'proposito': proposito})
        return True, 'ok', CodigoOtp.CANAL_SMS

    logger.error('otp_sms_falha', extra={'email_hash': hash(email), 'proposito': proposito})
    return False, 'sms_falha', None


def verificar_otp(
    email: str,
    codigo: str,
    *,
    proposito: str = CodigoOtp.PROPOSITO_AGENDAMENTO,
) -> Tuple[bool, str]:
    """Valida codigo OTP previamente enviado, atomicamente.

    Args:
        email: identificador do challenge usado em solicitar_otp().
        codigo: 6 digitos digitados pelo usuario.
        proposito: PROPOSITO_AGENDAMENTO | PROPOSITO_LOGIN.

    Returns:
        (True, 'ok') ou (False, motivo) onde motivo:
            'dados_ausentes' | 'invalido' | 'expirado' | 'esgotado'
    """
    email = (email or '').strip().lower()
    codigo = (codigo or '').strip()
    if not email or not codigo:
        return False, 'dados_ausentes'
    return CodigoOtp.verificar(email, codigo, proposito=proposito)

"""Envio de codigo OTP (SMS).

Regra corporativa: OTP exclusivamente por SMS (Zenvia), sem fallback e-mail.
Os demais canais vivem em utils/email.py (transacional/marketing),
utils/whatsapp.py (lembrete D-1 + NPS) e tasks.py (jobs) — cada um checa o
consentimento do cliente antes de enviar.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class OTPService:
    """OTP sempre via SMS. Consent implicito: cliente fornece telefone = aceita."""

    @staticmethod
    def enviar_codigo(telefone: str, codigo: str, ip: Optional[str] = None) -> bool:
        from ..utils.sms import enviar_otp_sms
        if not telefone:
            logger.warning('otp_telefone_ausente_envio_abortado')
            return False
        return enviar_otp_sms(telefone, codigo, ip=ip)

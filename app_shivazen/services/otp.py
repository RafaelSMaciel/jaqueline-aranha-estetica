"""Servico OTP: SMS Zenvia exclusivo (sem fallback email).

Regra: OTP de validacao de agendamento e acesso ao portal sempre via SMS.
Se o cliente nao tem telefone valido, a solicitacao falha — e-mail nao e
utilizado como canal de OTP no fluxo principal.
"""
import logging

from ..models import OtpCode
from .notificacao import OTPService

logger = logging.getLogger(__name__)


def _client_ip(request):
    if request is None:
        return None
    from ..utils.security import client_ip
    return client_ip(request)


def solicitar_otp(
    email,
    request=None,
    proposito=OtpCode.PROPOSITO_AGENDAMENTO,
    telefone=None,
    canal_preferido=OtpCode.CANAL_SMS,
):
    """Gera codigo e envia via SMS. Retorna (ok, mensagem, canal_usado).

    Requer telefone valido. Sem telefone -> erro (sem fallback email).
    Rate limit de re-envio por REENVIO_MINIMO_SEG do model (por email).
    """
    email = (email or '').strip().lower()
    if not email or '@' not in email:
        return False, 'email_invalido', None

    if not telefone:
        logger.warning('[OTP] telefone ausente para %s — OTP via SMS requer telefone', email)
        return False, 'telefone_ausente', None

    if not OtpCode.pode_reenviar(email, proposito=proposito):
        return False, 'aguarde', None

    ip = _client_ip(request)
    codigo, _obj = OtpCode.gerar(
        email, ip=ip, proposito=proposito,
        canal=OtpCode.CANAL_SMS, telefone=telefone,
    )
    if OTPService.enviar_codigo(telefone, codigo, ip=ip):
        logger.info('[OTP] SMS enviado (prop=%s)', proposito)
        return True, 'ok', OtpCode.CANAL_SMS

    logger.error('[OTP] falha ao enviar SMS para %s', email)
    return False, 'sms_falha', None


def verificar_otp(email, codigo, proposito=OtpCode.PROPOSITO_AGENDAMENTO):
    """Consome codigo atomicamente. Retorna (ok, motivo)."""
    email = (email or '').strip().lower()
    codigo = (codigo or '').strip()
    if not email or not codigo:
        return False, 'dados_ausentes'
    return OtpCode.verificar(email, codigo, proposito=proposito)

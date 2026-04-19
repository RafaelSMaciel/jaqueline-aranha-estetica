"""Servico OTP: gera, envia (SMS primario / email fallback), valida."""
import logging

from ..models import OtpCode
from ..utils.email import enviar_codigo_otp_email
from ..utils.sms import enviar_otp_sms

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
    """Gera codigo e envia. Retorna (ok, mensagem, canal_usado).

    Estrategia:
      - canal_preferido='SMS' + telefone valido -> SMS. Fallback email em falha.
      - canal_preferido='EMAIL' ou sem telefone -> email.
    Rate limit de re-envio por REENVIO_MINIMO_SEG do model (por email).
    """
    email = (email or '').strip().lower()
    if not email or '@' not in email:
        return False, 'email_invalido', None

    if not OtpCode.pode_reenviar(email, proposito=proposito):
        return False, 'aguarde', None

    ip = _client_ip(request)
    tentar_sms = canal_preferido == OtpCode.CANAL_SMS and bool(telefone)

    if tentar_sms:
        codigo, _obj = OtpCode.gerar(
            email, ip=ip, proposito=proposito,
            canal=OtpCode.CANAL_SMS, telefone=telefone,
        )
        if enviar_otp_sms(telefone, codigo, ip=ip):
            logger.info('[OTP] SMS enviado (prop=%s)', proposito)
            return True, 'ok', OtpCode.CANAL_SMS
        logger.warning('[OTP] SMS falhou, fallback email para %s', email)
        _obj.canal = OtpCode.CANAL_EMAIL
        _obj.save(update_fields=['canal'])
        if enviar_codigo_otp_email(email, codigo):
            return True, 'ok', OtpCode.CANAL_EMAIL
        return False, 'envio_falha', None

    codigo, _obj = OtpCode.gerar(
        email, ip=ip, proposito=proposito, canal=OtpCode.CANAL_EMAIL,
    )
    if enviar_codigo_otp_email(email, codigo):
        logger.info('[OTP] email enviado (prop=%s)', proposito)
        return True, 'ok', OtpCode.CANAL_EMAIL
    logger.warning('[OTP] falha ao enviar email para %s', email)
    return False, 'email_falha', None


def verificar_otp(email, codigo, proposito=OtpCode.PROPOSITO_AGENDAMENTO):
    """Consome codigo atomicamente. Retorna (ok, motivo)."""
    email = (email or '').strip().lower()
    codigo = (codigo or '').strip()
    if not email or not codigo:
        return False, 'dados_ausentes'
    return OtpCode.verificar(email, codigo, proposito=proposito)

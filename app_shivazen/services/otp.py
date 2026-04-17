"""Servico OTP por email — gera, envia, valida."""
import logging

from ..models import OtpCode
from ..utils.email import enviar_codigo_otp_email

logger = logging.getLogger(__name__)


def _client_ip(request):
    if request is None:
        return None
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def solicitar_otp(email, request=None, proposito=OtpCode.PROPOSITO_AGENDAMENTO):
    """Gera codigo, envia por email. Retorna (ok, mensagem).

    Rate limit de re-envio por REENVIO_MINIMO_SEG do model.
    """
    email = (email or '').strip().lower()
    if not email or '@' not in email:
        return False, 'email_invalido'

    if not OtpCode.pode_reenviar(email, proposito=proposito):
        return False, 'aguarde'

    ip = _client_ip(request)
    codigo, _obj = OtpCode.gerar(email, ip=ip, proposito=proposito)
    enviado = enviar_codigo_otp_email(email, codigo)
    if not enviado:
        logger.warning('[OTP] falha ao enviar email para %s', email)
        return False, 'email_falha'
    logger.info('[OTP] gerado para %s (prop=%s)', email, proposito)
    return True, 'ok'


def verificar_otp(email, codigo, proposito=OtpCode.PROPOSITO_AGENDAMENTO):
    """Consome codigo atomicamente. Retorna (ok, motivo)."""
    email = (email or '').strip().lower()
    codigo = (codigo or '').strip()
    if not email or not codigo:
        return False, 'dados_ausentes'
    return OtpCode.verificar(email, codigo, proposito=proposito)

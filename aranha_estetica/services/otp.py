"""Servico OTP: SMS Zenvia exclusivo (sem fallback email).

Regra: OTP de validacao de agendamento e acesso ao portal sempre via SMS e
SEMPRE preso ao TELEFONE que recebe o codigo (chave do desafio =
CodigoOtp.email_para_telefone(digitos)). E-mail digitado pelo usuario nunca
e identidade: quem prova posse do celular so ve/edita o cadastro daquele
celular.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta
from typing import Optional, Tuple, TYPE_CHECKING

from django.core.exceptions import ValidationError
from django.utils import timezone

from ..models import CodigoOtp
from ..utils.sms import enviar_otp_sms, pode_enviar, sms_disponivel

if TYPE_CHECKING:
    from django.http import HttpRequest

logger = logging.getLogger(__name__)

OtpResult = Tuple[bool, str, Optional[str]]

# Sessao: telefone (digitos) verificado por OTP no wizard de agendamento.
SESSAO_AGENDAMENTO = 'otp_agendamento_telefone'
SESSAO_AGENDAMENTO_EXPIRA = 'otp_agendamento_expira'
VALIDADE_VERIFICACAO = timedelta(minutes=30)


def _email_hash(email: str) -> str:
    """Hash estavel p/ correlacao em logs (nao e seguranca, so anonimizacao).

    Usa SHA-256 truncado em vez de hash() builtin, que e randomizado por
    processo (PYTHONHASHSEED) e nao correlaciona entre execucoes.
    """
    return hashlib.sha256(email.encode()).hexdigest()[:12]


def _client_ip(request: Optional['HttpRequest']) -> Optional[str]:
    """Extrai IP do request via helper canonico (None se request ausente)."""
    if request is None:
        return None
    from ..utils.security import client_ip
    return client_ip(request) or None


def normalizar_telefone_br(valor) -> str:
    """Celular/fixo BR canonico (10-11 digitos, DDD valido) ou '' se invalido.

    Remove mascara, DDI 55 (autofill '+55 ...') e zero de discagem a frente.
    """
    from ..validators import normalizar_telefone, validate_telefone_br
    digitos = normalizar_telefone(str(valor or ''))
    if len(digitos) in (12, 13) and digitos.startswith('55'):
        digitos = digitos[2:]
    if len(digitos) in (11, 12) and digitos.startswith('0'):
        digitos = digitos[1:]
    try:
        validate_telefone_br(digitos)
    except ValidationError:
        return ''
    return digitos if len(digitos) in (10, 11) else ''


def eh_celular_br(digitos) -> bool:
    """Celular BR (DDD + 9 + 8 digitos). SMS p/ fixo nunca chega e so gasta quota.

    So o ENVIO de OTP exige celular: normalizar_telefone_br continua aceitando
    fixo (cadastro feito na recepcao, confirmacao do agendamento).
    """
    return bool(re.fullmatch(r'[1-9]{2}9\d{8}', digitos or ''))


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
            (False, 'limite_sms', None)      — quota de SMS (telefone/IP/global)
                                               esgotada: NENHUM codigo novo e gerado
                                               (o ultimo recebido continua valendo)
            (False, 'sms_falha', None)       — canal indisponivel/erro no envio

    Raises:
        Nao propaga — todos erros traduzidos em (False, motivo).
    """
    email = (email or '').strip().lower()
    if not email or '@' not in email:
        return False, 'email_invalido', None

    if not telefone:
        logger.warning(
            'otp_telefone_ausente',
            extra={'email_hash': _email_hash(email), 'proposito': proposito},
        )
        return False, 'telefone_ausente', None

    # Canal fora do ar (sem provedor em prod): nao gera codigo nem consome cooldown.
    if not sms_disponivel():
        logger.error('otp_sms_indisponivel', extra={'proposito': proposito})
        return False, 'sms_falha', None

    if not CodigoOtp.pode_reenviar(email, proposito=proposito):
        return False, 'aguarde', None

    ip = _client_ip(request)
    # Quota checada ANTES de gerar: gerar() invalida o codigo anterior, que a
    # pessoa ainda tem no celular — sem envio, ela ficaria sem codigo valido.
    if not pode_enviar(telefone, ip=ip):
        logger.warning('otp_sms_limite', extra={'email_hash': _email_hash(email), 'proposito': proposito})
        return False, 'limite_sms', None

    codigo, _obj = CodigoOtp.gerar(
        email, ip=ip, proposito=proposito,
        canal=CodigoOtp.CANAL_SMS, telefone=telefone,
    )
    if enviar_otp_sms(telefone, codigo, ip=ip):
        logger.info('otp_sms_enviado', extra={'proposito': proposito})
        return True, 'ok', CodigoOtp.CANAL_SMS

    logger.error('otp_sms_falha', extra={'email_hash': _email_hash(email), 'proposito': proposito})
    return False, 'sms_falha', None


def verificar_otp(
    email: str,
    codigo: str,
    *,
    proposito: str = CodigoOtp.PROPOSITO_AGENDAMENTO,
) -> Tuple[bool, str]:
    """Valida codigo OTP previamente enviado, atomicamente.

    Returns:
        (True, 'ok') ou (False, motivo) onde motivo:
            'dados_ausentes' | 'incorreto:N' | 'expirado' | 'bloqueado'
    """
    email = (email or '').strip().lower()
    codigo = (codigo or '').strip()
    if not email or not codigo:
        return False, 'dados_ausentes'
    return CodigoOtp.verificar(email, codigo, proposito=proposito)


# ─── Desafio preso ao telefone ──────────────────────────────────────────

def solicitar_otp_telefone(
    digitos: str, *, request: Optional['HttpRequest'] = None,
    proposito: str = CodigoOtp.PROPOSITO_AGENDAMENTO,
) -> OtpResult:
    """Envia OTP ao CELULAR; a chave do desafio e o proprio telefone.

    Fixo (10 digitos) -> (False, 'telefone_invalido', None) sem gastar quota.
    """
    if not eh_celular_br(digitos):
        return False, 'telefone_invalido', None
    return solicitar_otp(
        CodigoOtp.email_para_telefone(digitos),
        request=request, proposito=proposito, telefone=digitos,
    )


def verificar_otp_telefone(
    digitos: str, codigo: str, *, proposito: str = CodigoOtp.PROPOSITO_AGENDAMENTO,
) -> Tuple[bool, str]:
    """Consome o OTP do telefone (mesma chave de solicitar_otp_telefone)."""
    if not digitos:
        return False, 'dados_ausentes'
    return verificar_otp(CodigoOtp.email_para_telefone(digitos), codigo, proposito=proposito)


# ─── Sessao do wizard de agendamento ────────────────────────────────────

def registrar_verificacao_agendamento(request, digitos: str) -> None:
    """Marca o telefone como verificado na sessao (30 min). Anti session-fixation."""
    request.session.cycle_key()
    request.session[SESSAO_AGENDAMENTO] = digitos
    request.session[SESSAO_AGENDAMENTO_EXPIRA] = (timezone.now() + VALIDADE_VERIFICACAO).isoformat()


def telefone_verificado_agendamento(request) -> str:
    """Telefone (digitos) verificado e ainda valido na sessao, ou ''."""
    digitos = request.session.get(SESSAO_AGENDAMENTO) or ''
    expira = request.session.get(SESSAO_AGENDAMENTO_EXPIRA)
    if not digitos or not expira:
        return ''
    try:
        exp = datetime.fromisoformat(expira)
    except (TypeError, ValueError):
        return ''
    if timezone.is_naive(exp):
        exp = timezone.make_aware(exp)
    return digitos if exp > timezone.now() else ''


def limpar_verificacao_agendamento(request) -> None:
    request.session.pop(SESSAO_AGENDAMENTO, None)
    request.session.pop(SESSAO_AGENDAMENTO_EXPIRA, None)

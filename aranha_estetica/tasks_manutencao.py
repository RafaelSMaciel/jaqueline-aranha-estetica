"""Jobs de manutencao/retencao (cron HTTP em prod; beat se houver worker).

Politica (docs/specs/regras-negocio-registry.md `retencao.*`): OTP 24h,
texto de notificacao 12 meses, auditoria 5 anos. Prazos em settings.RETENCAO_*.
Registrado no Celery via CELERY_IMPORTS (autodiscover so acha tasks.py).
"""
import io
import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.core.management import call_command
from django.utils import timezone

logger = logging.getLogger(__name__)


def _limpar_sessoes():
    call_command('clearsessions')
    return 'ok'


def _purgar_otp():
    from .models import CodigoOtp
    limite = timezone.now() - timedelta(hours=settings.RETENCAO_OTP_HORAS)
    return CodigoOtp.objects.filter(criado_em__lt=limite).delete()[0]


def _limpar_texto_notificacoes():
    from .models import Notificacao
    limite = timezone.now() - timedelta(days=settings.RETENCAO_NOTIFICACAO_DIAS)
    return (
        Notificacao.objects.filter(criado_em__lt=limite)
        .exclude(mensagem__isnull=True).exclude(mensagem='')
        .update(mensagem='')
    )


def _purgar_auditoria():
    from .models import LogAuditoria
    limite = timezone.now() - timedelta(days=settings.RETENCAO_LOG_AUDITORIA_DIAS)
    return LogAuditoria.objects.filter(criado_em__lt=limite).delete()[0]


def _purgar_logs_axes():
    from axes.handlers.proxy import AxesProxyHandler
    dias = settings.RETENCAO_AXES_LOG_DIAS
    return AxesProxyHandler.reset_logs(age_days=dias) + AxesProxyHandler.reset_failure_logs(age_days=dias)


ETAPAS_HOUSEKEEPING = [
    ('sessoes', _limpar_sessoes),
    ('otp', _purgar_otp),
    ('notificacoes', _limpar_texto_notificacoes),
    ('auditoria', _purgar_auditoria),
    ('axes', _purgar_logs_axes),
]


@shared_task(bind=True, max_retries=0)
def job_housekeeping(self):
    """Limpeza diaria: sessoes expiradas, OTP, texto de notificacoes antigas,
    auditoria alem da retencao e logs do axes. Cada etapa e independente;
    se alguma falhar, as outras rodam e o job termina em erro (cron ve 500)."""
    resumo, falhas = {}, []
    for nome, etapa in ETAPAS_HOUSEKEEPING:
        try:
            resumo[nome] = etapa()
        except Exception as exc:  # noqa: BLE001 — isola a etapa
            logger.exception('housekeeping_etapa_falhou', extra={'etapa': nome})
            resumo[nome] = f'erro: {exc.__class__.__name__}'
            falhas.append(nome)
    logger.info('housekeeping_ok', extra={'resumo': resumo})
    if falhas:
        raise RuntimeError(f'housekeeping falhou em: {", ".join(falhas)}')
    return resumo


@shared_task(bind=True, max_retries=0)
def job_carregar_feriados(self):
    """Garante feriados nacionais do ano atual + proximo (comando idempotente).
    A migration so semeou 2026-2027; sem isso nenhum feriado bloqueia a agenda."""
    saida = io.StringIO()
    call_command('carregar_feriados', stdout=saida)
    linhas = saida.getvalue().strip().splitlines()
    return linhas[-1] if linhas else 'ok'

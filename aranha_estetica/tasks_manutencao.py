"""Jobs de manutencao/retencao (cron HTTP em prod; beat se houver worker).

Politica (docs/specs/regras-negocio-registry.md `retencao.*`): OTP 24h,
texto de notificacao 12 meses, auditoria 5 anos, link publico vencido/revogado
90 dias, trilha historico_alteracao 5 anos (prontuario: 20, nao purga aqui).
Prazos em settings.RETENCAO_*. Registrado no Celery via CELERY_IMPORTS
(autodiscover so acha tasks.py).

Postgres: as etapas de tabelas do app viram UMA etapa "banco" = CALL
sp_purgar_dados_expirados (fonte unica, spec padrao-sql §5.6; cada etapa num
sub-bloco da procedure, falha de uma nao impede as outras); sessoes e axes
continuam Python (tabelas de terceiros). SQLite: as funcoes _purgar_* abaixo.
"""
import io
import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.core.management import call_command
from django.db import connection, models, transaction
from django.db.models import Q
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


def _purgar_links():
    """link_acesso vencido ou revogado ha mais de N dias (sem validade e nao revogado fica)."""
    from .models import LinkAcesso
    limite = timezone.now() - timedelta(days=settings.RETENCAO_LINK_ACESSO_DIAS)
    _total, por_modelo = LinkAcesso.objects.filter(
        Q(revogado_em__lt=limite) | Q(revogado_em__isnull=True, expira_em__lt=limite),
    ).delete()
    return por_modelo.get('aranha_estetica.LinkAcesso', 0)


def _purgar_historico():
    """historico_alteracao alem da retencao (5 anos), exceto prontuario (registro clinico, 20 anos).

    Trilha append-only: o QuerySet do model recusa delete(); a purga usa o delete base e, no
    Postgres, liga a flag local da transacao que o tg_historico_alteracao_imutavel aceita
    (como o sp_purgar_dados_expirados).
    """
    from .models import HistoricoAlteracao
    limite = timezone.now() - timedelta(days=settings.RETENCAO_LOG_AUDITORIA_DIAS)
    qs = HistoricoAlteracao.objects.exclude(tabela='prontuario').filter(criado_em__lt=limite)
    with transaction.atomic():
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('app.expurgo_autorizado', 'on', true)")
        qtd, _por_modelo = models.QuerySet.delete(qs)
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('app.expurgo_autorizado', 'off', true)")
    return qtd


def _purgar_logs_axes():
    from axes.handlers.proxy import AxesProxyHandler
    dias = settings.RETENCAO_AXES_LOG_DIAS
    return AxesProxyHandler.reset_logs(age_days=dias) + AxesProxyHandler.reset_failure_logs(age_days=dias)


ETAPAS_HOUSEKEEPING = [
    ('sessoes', _limpar_sessoes),
    ('otp', _purgar_otp),
    ('notificacoes', _limpar_texto_notificacoes),
    ('auditoria', _purgar_auditoria),
    ('links', _purgar_links),
    ('historico', _purgar_historico),
    ('axes', _purgar_logs_axes),
]
# etapas que o sp_purgar_dados_expirados faz no Postgres (na ordem dos INOUT da procedure)
ETAPAS_DO_BANCO = ('otp', 'notificacoes', 'auditoria', 'links', 'historico')


def _purgar_no_banco():
    """CALL sp_purgar_dados_expirados -> ({etapa: qtd}, [etapas com falha])."""
    from .utils.banco import chamar
    *qtds, falhas = chamar(
        'sp_purgar_dados_expirados',
        settings.RETENCAO_OTP_HORAS, settings.RETENCAO_NOTIFICACAO_DIAS,
        settings.RETENCAO_LOG_AUDITORIA_DIAS, settings.RETENCAO_LINK_ACESSO_DIAS,
        None, None, None, None, None, None,
    )
    falhas = list(falhas or [])
    resumo = {
        nome: (f'erro: {nome}' if nome in falhas else (qtd or 0))
        for nome, qtd in zip(ETAPAS_DO_BANCO, qtds)
    }
    return resumo, falhas


@shared_task(bind=True, max_retries=0)
def job_housekeeping(self):
    """Limpeza diaria: sessoes expiradas, OTP, texto de notificacoes antigas,
    auditoria alem da retencao, links publicos vencidos/revogados, trilha de
    alteracoes alem da retencao e logs do axes. Cada etapa e independente;
    se alguma falhar, as outras rodam e o job termina em erro (cron ve 500).
    No Postgres as 5 etapas do app sao o sp_purgar_dados_expirados."""
    from .utils.banco import usa_logica_pg

    pg = usa_logica_pg()
    resumo, falhas = {}, []
    for nome, etapa in ETAPAS_HOUSEKEEPING:
        if pg and nome in ETAPAS_DO_BANCO:
            if nome != ETAPAS_DO_BANCO[0]:
                continue  # uma chamada so faz as 5
            nome, etapa = 'banco', _purgar_no_banco
        try:
            resultado = etapa()
        except Exception as exc:  # noqa: BLE001 — isola a etapa
            logger.exception('housekeeping_etapa_falhou', extra={'etapa': nome})
            resumo[nome] = f'erro: {exc.__class__.__name__}'
            falhas.append(nome)
            continue
        if nome == 'banco':
            parcial, falhas_banco = resultado
            resumo.update(parcial)
            falhas.extend(falhas_banco)
            for etapa_falha in falhas_banco:
                logger.error('housekeeping_etapa_falhou', extra={'etapa': etapa_falha})
        else:
            resumo[nome] = resultado
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

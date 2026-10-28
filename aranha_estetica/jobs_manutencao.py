"""Jobs de manutencao/retencao (funcoes chamadas pelo cron HTTP e pelo comando rodar_job).

Politica (docs/specs/regras-negocio-registry.md `retencao.*`): OTP 24h,
texto de notificacao 12 meses, auditoria 5 anos, link publico vencido/revogado
90 dias, trilha historico_alteracao 5 anos (prontuario: 20, nao purga aqui).
Prazos em settings.RETENCAO_*.

Postgres: as etapas de tabelas do app viram UMA etapa "banco" = CALL
sp_purgar_dados_expirados (fonte unica, spec padrao-sql §5.6; cada etapa num
sub-bloco da procedure, falha de uma nao impede as outras); sessoes e axes
continuam Python (tabelas de terceiros). SQLite: as funcoes _purgar_* abaixo.
"""
import io
import logging

from django.conf import settings
from django.core.management import call_command

logger = logging.getLogger(__name__)


def _limpar_sessoes():
    call_command('clearsessions')
    return 'ok'


def _purgar_logs_axes():
    from axes.handlers.proxy import AxesProxyHandler
    dias = settings.RETENCAO_AXES_LOG_DIAS
    return AxesProxyHandler.reset_logs(age_days=dias) + AxesProxyHandler.reset_failure_logs(age_days=dias)


# etapas que o sp_purgar_dados_expirados faz (na ordem dos INOUT da procedure)
ETAPAS_DO_BANCO = ('otp', 'notificacoes', 'auditoria', 'links', 'historico')


def _purgar_no_banco():
    """CALL sp_purgar_dados_expirados -> ({etapa: qtd}, [etapas com falha]).

    OTP com mais de RETENCAO_OTP_HORAS, texto de notificacao com mais de
    RETENCAO_NOTIFICACAO_DIAS, log_auditoria e historico_alteracao (menos prontuario) alem de
    RETENCAO_LOG_AUDITORIA_DIAS e link_acesso vencido/revogado ha mais de
    RETENCAO_LINK_ACESSO_DIAS (link sem validade e nao revogado fica: feed ICS). Cada etapa e uma
    subtransacao na procedure: a falha de uma nao impede as outras.
    """
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


ETAPAS_HOUSEKEEPING = [
    ('sessoes', _limpar_sessoes),
    ('banco', _purgar_no_banco),
    ('axes', _purgar_logs_axes),
]


def job_housekeeping():
    """Limpeza diaria: sessoes expiradas, o sp_purgar_dados_expirados (OTP, texto de
    notificacoes, auditoria e trilha alem da retencao, links vencidos/revogados) e logs do axes.
    Cada etapa e independente; se alguma falhar, as outras rodam e o job termina em erro (cron
    ve 500)."""
    resumo, falhas = {}, []
    for nome, etapa in ETAPAS_HOUSEKEEPING:
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


def job_carregar_feriados():
    """Garante feriados nacionais do ano atual + proximo (comando idempotente).
    A migration so semeou 2026-2027; sem isso nenhum feriado bloqueia a agenda."""
    saida = io.StringIO()
    call_command('carregar_feriados', stdout=saida)
    linhas = saida.getvalue().strip().splitlines()
    return linhas[-1] if linhas else 'ok'

"""Jobs periodicos: o registro (JOB_MAP), a agenda (JOB_AGENDA) e a execucao (executar).

Sem Celery: cada job e uma funcao curta que chama o caso de uso em services/ e devolve um resumo.
Quem dispara: o comando ``rodar_jobs_devidos`` (servico Cron do Railway a cada 15 min, le a
JOB_AGENDA), o cron HTTP (views/infra/cron) e, a mao, ``python manage.py rodar_job <job>``.

Regras transversais:
  - "hoje/amanha" sempre no fuso da clinica (utils/datas), nunca data UTC;
  - canal sem credencial fora de DEBUG => nada e marcado como enviado;
  - consentimento da cliente respeitado por canal/finalidade (services/consentimentos);
  - nunca mandar duas vezes: lembrete e NPS tem marca por destinatario (Notificacao); os jobs de
    mensagem sem essa marca (JOBS_UMA_VEZ_POR_DIA) rodam no maximo uma vez por dia (ExecucaoJob);
  - erro propaga: o cron ve 500 e avisa o monitor; nada e reexecutado na hora (falha pontual por
    destinatario e logada e o lote segue).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

import requests
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from . import jobs_manutencao
from .models import ExecucaoJob
from .services import comunicacao, lembretes, marketing, nps
from .services.atendimento import cancelar_pendentes_vencidos
from .services.lgpd import LgpdService
from .services.pacotes import avisar_expirando
from .utils.banco import chamar
from .utils.datas import hoje

logger = logging.getLogger(__name__)


# ═══ Jobs (1 a 3 linhas: a regra mora em services/) ═══════════════════════

def job_emails():
    r = comunicacao.reenviar_pendentes()
    return f"{r['enviados']} e-mails enviados; {r['pendentes']} na fila"


def job_lembrete_diario():
    r = lembretes.enviar_lembretes_d1()
    return f"{r['whatsapp']} lembretes por WhatsApp; {r['email']} por e-mail"


def job_sem_desfecho():
    return f'{lembretes.avisar_sem_desfecho()} profissionais avisadas'


def job_nps_24h():
    return f'{nps.enviar_pesquisas()} NPS enviados'


def job_detrator_alerta():
    return f'{nps.alertar_detratores()} alertas de detrator'


def job_pacote_expirando():
    return f'{avisar_expirando()} avisos enviados'


def job_pacote_expirar():
    """CALL sp_expirar_pacotes(hoje local): o ultimo dia vale (so Postgres)."""
    (expirados,) = chamar('sp_expirar_pacotes', hoje(), None)
    return f'{expirados} pacotes expirados'


def job_aniversario():
    return f'{marketing.felicitar_aniversariantes()} e-mails de aniversario'


def job_limpeza_status():
    return f'{cancelar_pendentes_vencidos()} pendentes cancelados'


def job_lgpd_purgar():
    r = LgpdService.aplicar_retencao()
    return (f"{r['clientes']} clientes anonimizados; {r['fichas']} fichas apagadas; "
            f"{r['esperas']} inscricoes de espera apagadas")


JOB_MAP = {
    'emails': job_emails,
    'lembrete_diario': job_lembrete_diario,
    'sem_desfecho': job_sem_desfecho,
    'nps_24h': job_nps_24h,
    'detrator_alerta': job_detrator_alerta,
    'pacote_expirando': job_pacote_expirando,
    'pacote_expirar': job_pacote_expirar,
    'aniversario': job_aniversario,
    'limpeza_status': job_limpeza_status,
    'lgpd_purgar': job_lgpd_purgar,
    'housekeeping': jobs_manutencao.job_housekeeping,
    'feriados': jobs_manutencao.job_carregar_feriados,
}

# Jobs de mensagem sem marca por destinatario: a 2a execucao no mesmo dia mandaria tudo de novo.
# (lembrete_diario roda 2x por dia e se protege por atendimento; o NPS tambem tem a marca, mas so
# precisa de uma rodada por dia.)
JOBS_UMA_VEZ_POR_DIA = frozenset({'aniversario', 'pacote_expirando', 'nps_24h'})


# ═══ Agenda (horario de Brasilia, sem horario de verao desde 2019) ═══════════

@dataclass(frozen=True)
class Horario:
    """Quando um job roda, como no cron: None = qualquer valor. dia_semana como date.weekday()
    (0 = segunda ... 6 = domingo)."""
    minuto: int | tuple[int, ...]
    hora: int | None = None
    dia_mes: int | None = None
    dia_semana: int | None = None

    def bate(self, momento) -> bool:
        minutos = self.minuto if isinstance(self.minuto, tuple) else (self.minuto,)
        return (
            momento.minute in minutos
            and self.hora in (None, momento.hour)
            and self.dia_mes in (None, momento.day)
            and self.dia_semana in (None, momento.weekday())
        )


JOB_AGENDA: dict[str, tuple[Horario, ...]] = {
    'emails': (Horario(minuto=(0, 15, 30, 45)),),
    'limpeza_status': (Horario(minuto=0),),
    'pacote_expirar': (Horario(minuto=30, hora=0),),
    'pacote_expirando': (Horario(minuto=0, hora=7),),
    'lembrete_diario': (Horario(minuto=0, hora=8), Horario(minuto=0, hora=18)),
    'sem_desfecho': (Horario(minuto=0, hora=8),),
    'aniversario': (Horario(minuto=0, hora=9),),
    'nps_24h': (Horario(minuto=0, hora=10),),
    'detrator_alerta': (Horario(minuto=30, hora=10),),
    'lgpd_purgar': (Horario(minuto=0, hora=3, dia_semana=6),),
    'housekeeping': (Horario(minuto=0, hora=4),),
    'feriados': (Horario(minuto=30, hora=4, dia_mes=1),),
}


def jobs_devidos(inicio, fim) -> list[str]:
    """Jobs com horario no intervalo (inicio, fim] (datetimes; comparados no fuso local, por
    minuto), na ordem da JOB_AGENDA."""
    inicio = timezone.localtime(inicio).replace(second=0, microsecond=0)
    fim = timezone.localtime(fim).replace(second=0, microsecond=0)
    minutos = []
    momento = fim
    while momento > inicio:
        minutos.append(momento)
        momento -= timedelta(minutes=1)
    return [job for job, horarios in JOB_AGENDA.items()
            if any(h.bate(m) for h in horarios for m in minutos)]


# ═══ Execucao ═════════════════════════════════════════════════════════════

class JobJaExecutado(Exception):
    """Job de JOBS_UMA_VEZ_POR_DIA que ja rodou hoje (o cron HTTP responde 409)."""


def _reservar_dia(job_name, *, forcar=False) -> ExecucaoJob | None:
    """A linha do dia nasce ANTES do job (a UNIQUE decide entre duas chamadas simultaneas)."""
    if job_name not in JOBS_UMA_VEZ_POR_DIA:
        return None
    try:
        with transaction.atomic():
            return ExecucaoJob.objects.create(job=job_name, dia=hoje())
    except IntegrityError as exc:
        if not forcar:
            raise JobJaExecutado(f'{job_name} ja rodou hoje') from exc
    return ExecucaoJob.objects.get(job=job_name, dia=hoje())


def executar(job_name, *, ip=None, origem=None, forcar=False):
    """Roda o job uma vez dentro do Ator dele (trilhas com origem cron:<job>, sem autor).

    Job de JOBS_UMA_VEZ_POR_DIA que ja rodou hoje levanta JobJaExecutado (``forcar`` passa por
    cima: decisao da equipe depois de uma falha). Erro do job propaga: quem chama decide (HTTP 500
    / exit code do comando); a execucao do dia fica registrada com o erro."""
    from .ator import Ator, atuando

    execucao = _reservar_dia(job_name, forcar=forcar)
    with atuando(Ator(ip=ip, origem=origem or f'cron:{job_name}')):
        try:
            resultado = JOB_MAP[job_name]()
        except Exception as exc:
            if execucao is not None:
                ExecucaoJob.objects.filter(pk=execucao.pk).update(resultado=f'erro: {type(exc).__name__}')
            raise
    if execucao is not None:
        ExecucaoJob.objects.filter(pk=execucao.pk).update(
            concluido_em=timezone.now(), resultado=str(resultado or 'ok')[:500])
    return resultado


def avisar_monitor(job_name: str, ok: bool) -> None:
    """Heartbeat do job no monitor externo (CRON_MONITOR_URL): <url>/<job> ou <url>/<job>/fail.
    Best-effort: monitor fora do ar nunca muda o resultado do job (o agendador segue vendo o
    sucesso/erro de verdade)."""
    base = (getattr(settings, 'CRON_MONITOR_URL', '') or '').rstrip('/')
    if not base:
        return
    url = f'{base}/{job_name}' + ('' if ok else '/fail')
    try:
        requests.post(url, timeout=5)
    except requests.RequestException as exc:
        logger.warning('cron_monitor_falhou', extra={'job': job_name, 'erro': type(exc).__name__})

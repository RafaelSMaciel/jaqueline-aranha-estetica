"""Endpoint HTTP p/ cron externo — o agendador dos jobs (jobs.py; sem Celery nem worker).

Uso (cron-job.org, servico Cron do Railway etc.):
    curl -fsS -X POST -H "X-Cron-Token: $CRON_TOKEN" https://<dominio>/cron/run/<job>/

Autenticacao SO pelo header X-Cron-Token (nunca ?token= na URL: vaza em log), comparado em tempo
constante; 30 chamadas/h por IP (429 depois: token adivinhado nao vira rajada de mensagens).
Resposta no contrato de utils/respostas: 200 ok quando o job termina bem; 500 codigo=job_falhou
(o cron externo precisa enxergar o erro p/ alertar) so com o TIPO da excecao e uma referencia
(`ref`, a mesma do log): o agendador guarda o corpo, e a mensagem pode ter dado pessoal
(DETAIL do Postgres, destinatario do provedor); 403 token; 404 job.

Monitor (CRON_MONITOR_URL, healthchecks.io ou compativel): cada execucao avisa
<url>/<job> (ok) ou <url>/<job>/fail. Job que para de rodar — cron externo apagado, token
trocado, site fora — vira alerta pela falta do aviso.

Horarios sugeridos (BRT):
    emails a cada 15 min · pacote_expirar 00:30 · pacote_expirando 07:00 ·
    lembrete_diario 08:00 · aniversario 09:00 · nps_24h 10:00 · detrator_alerta 10:30 ·
    limpeza_status de hora em hora · lgpd_purgar dom 03:00 · housekeeping 04:00 · feriados dia 1 04:30
Fora do HTTP (shell do servidor, dev): ``python manage.py rodar_job <job>``.
"""
import logging
import secrets

import requests
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from aranha_estetica.ator import ip_valido
from aranha_estetica.jobs import JOB_MAP, executar
from aranha_estetica.utils.respostas import json_erro, json_ok
from aranha_estetica.utils.seguranca import client_ip, segredo_confere

logger = logging.getLogger(__name__)


def _avisar_monitor(job_name: str, ok: bool) -> None:
    """Heartbeat do job no monitor externo. Best-effort: monitor fora do ar nunca muda a
    resposta do job (o cron externo segue vendo o 200/500 de verdade)."""
    base = (getattr(settings, 'CRON_MONITOR_URL', '') or '').rstrip('/')
    if not base:
        return
    url = f'{base}/{job_name}' + ('' if ok else '/fail')
    try:
        requests.post(url, timeout=5)
    except requests.RequestException as exc:
        logger.warning('cron_monitor_falhou', extra={'job': job_name, 'erro': type(exc).__name__})


# Folga para a agenda (emails a cada 15 min + jobs diarios) e freio p/ quem tenta adivinhar o token
LIMITE_CHAMADAS = '30/h'


@csrf_exempt
@require_POST
@ratelimit(key='ip', rate=LIMITE_CHAMADAS, method='POST', block=True)
def run_job(request, job_name):
    if not segredo_confere(settings.CRON_TOKEN, request.headers.get('X-Cron-Token', '')):
        logger.warning('cron_token_invalido', extra={'job': job_name, 'ip': client_ip(request)})
        return json_erro('token_invalido', 'Header X-Cron-Token ausente ou invalido.', status=403)

    if job_name not in JOB_MAP:
        return json_erro('job_desconhecido', 'Job inexistente.', status=404, disponiveis=sorted(JOB_MAP))

    # Trilhas (LogAuditoria e historico_alteracao): o que o job mudar sai como origem
    # 'cron:<job>', sem autor, com o IP do agendador. Roda UMA vez (sem retry na hora: jobs
    # de mensagem nao sao todos idempotentes; o proximo run pega o que ficou).
    try:
        resultado = executar(job_name, ip=ip_valido(client_ip(request)))
    except Exception as exc:  # noqa: BLE001 — o cron externo precisa ver o 500
        ref = secrets.token_hex(4)
        logger.exception('cron_job_falhou', extra={'job': job_name, 'ref': ref})
        _avisar_monitor(job_name, ok=False)
        return json_erro('job_falhou', type(exc).__name__, status=500, job=job_name, ref=ref)

    _avisar_monitor(job_name, ok=True)
    return json_ok(job=job_name, resultado=str(resultado) if resultado else 'ok')

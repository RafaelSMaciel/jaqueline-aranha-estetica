"""Endpoint HTTP p/ cron externo — o agendador dos jobs (jobs.py; sem Celery nem worker).

Uso (cron-job.org, servico Cron do Railway etc.):
    curl -fsS -X POST -H "X-Cron-Token: $CRON_TOKEN" https://<dominio>/cron/run/<job>/

Autenticacao SO pelo header X-Cron-Token (nunca ?token= na URL: vaza em log).
Resposta: 200 {"ok": true} quando o job termina bem; 500 {"ok": false} quando
falha (o cron externo precisa enxergar o erro p/ alertar); 409 quando um job de mensagem que so roda
1x por dia (jobs.JOBS_UMA_VEZ_POR_DIA) ja rodou hoje; 403 token; 404 job.

Monitor (CRON_MONITOR_URL, healthchecks.io ou compativel): cada execucao avisa
<url>/<job> (ok) ou <url>/<job>/fail (jobs.avisar_monitor, o mesmo do rodar_jobs_devidos). Job que
para de rodar — cron externo apagado, token trocado, site fora — vira alerta pela falta do aviso.

Horarios: jobs.JOB_AGENDA (fonte unica; o servico Cron do Railway roda ``rodar_jobs_devidos`` a cada
15 min e ele chama os jobs da hora). Fora do HTTP: ``python manage.py rodar_job <job>``.
"""
import hmac
import logging
import os

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from aranha_estetica.ator import ip_valido
from aranha_estetica.jobs import JOB_MAP, JobJaExecutado, avisar_monitor, executar
from aranha_estetica.utils.seguranca import client_ip

logger = logging.getLogger(__name__)


def _token_ok(token_req: str) -> bool:
    token_env = os.environ.get('CRON_TOKEN', '')
    if not token_env or not token_req:
        return False
    # bytes: compare_digest(str, str) levanta TypeError com nao-ASCII (-> 500)
    return hmac.compare_digest(
        token_env.encode('utf-8'),
        token_req.encode('utf-8', 'surrogateescape'),
    )


@csrf_exempt
@require_POST
def run_job(request, job_name):
    if not _token_ok(request.headers.get('X-Cron-Token', '')):
        logger.warning('Cron: token invalido (%s) IP=%s', job_name, client_ip(request))
        return JsonResponse({'error': 'forbidden'}, status=403)

    if job_name not in JOB_MAP:
        return JsonResponse({'error': 'unknown job', 'available': sorted(JOB_MAP)}, status=404)

    # Trilhas (LogAuditoria e historico_alteracao): o que o job mudar sai como origem
    # 'cron:<job>', sem autor, com o IP do agendador. Roda UMA vez (sem retry na hora: jobs
    # de mensagem nao sao todos idempotentes; o proximo run pega o que ficou).
    try:
        resultado = executar(job_name, ip=ip_valido(client_ip(request)))
    except JobJaExecutado:
        return JsonResponse({'ok': False, 'job': job_name, 'error': 'ja rodou hoje'}, status=409)
    except Exception as e:  # noqa: BLE001 — o cron externo precisa ver o 500
        logger.exception('Cron job %s falhou', job_name)
        avisar_monitor(job_name, ok=False)
        return JsonResponse({'ok': False, 'job': job_name, 'error': str(e)}, status=500)

    avisar_monitor(job_name, ok=True)
    return JsonResponse({'ok': True, 'job': job_name, 'result': str(resultado) if resultado else 'ok'})

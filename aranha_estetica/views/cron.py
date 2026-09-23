"""Endpoint HTTP p/ cron externo (substitui o Celery Beat; prod roda sem worker).

Uso (cron-job.org, servico Cron do Railway etc.):
    curl -fsS -X POST -H "X-Cron-Token: $CRON_TOKEN" https://<dominio>/cron/run/<job>/

Autenticacao SO pelo header X-Cron-Token (nunca ?token= na URL: vaza em log).
Resposta: 200 {"ok": true} quando o job termina bem; 500 {"ok": false} quando
falha (o cron externo precisa enxergar o erro p/ alertar); 403 token; 404 job.

Horarios sugeridos (BRT), espelhando CELERY_BEAT_SCHEDULE:
    pacote_expirar 00:30 · pacote_expirando 07:00 · lembrete_diario 08:00 ·
    aniversario 09:00 · nps_24h 10:00 · detrator_alerta 10:30 ·
    limpeza_status 23:00 · lgpd_purgar dom 03:00 · housekeeping 04:00 ·
    feriados dia 1 04:30
"""
import hmac
import logging
import os

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from aranha_estetica import tasks, tasks_manutencao
from aranha_estetica.utils.security import client_ip

logger = logging.getLogger(__name__)


JOB_MAP = {
    'lembrete_diario': tasks.job_enviar_lembrete_dia_seguinte,
    'nps_24h': tasks.job_pesquisa_satisfacao_24h,
    'detrator_alerta': tasks.job_alerta_detrator_nps,
    'pacote_expirando': tasks.job_verificar_pacotes_expirando,
    'pacote_expirar': tasks.job_expirar_pacotes,
    'aniversario': tasks.job_aniversario_clientes,
    'limpeza_status': tasks.job_limpeza_status_atendimentos,
    'lgpd_purgar': tasks.job_lgpd_purgar_inativos,
    'housekeeping': tasks_manutencao.job_housekeeping,
    'feriados': tasks_manutencao.job_carregar_feriados,
}

# Jobs opcionais de tasks.py: entram no mapa so se existirem (evita ImportError
# quando ainda nao foram criados).
for _nome, _attr in (
    ('limpeza_dados', 'job_limpeza_dados'),
    ('otp_purgar', 'job_purgar_otp'),
    ('promocao_lote', 'job_promocao_lote'),
):
    _job = getattr(tasks, _attr, None)
    if _job is not None:
        JOB_MAP[_nome] = _job


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

    job = JOB_MAP.get(job_name)
    if not job:
        return JsonResponse({'error': 'unknown job', 'available': sorted(JOB_MAP)}, status=404)

    try:
        # retries=max_retries: roda UMA vez (eager reexecutaria o lote na hora a
        # cada self.retry, reenviando mensagens de jobs sem idempotencia)
        result = job.apply(retries=job.max_retries or 0)
    except Exception as e:  # testes/dev: EAGER_PROPAGATES=True levanta aqui
        logger.exception('Cron job %s falhou', job_name)
        return JsonResponse({'ok': False, 'job': job_name, 'error': str(e)}, status=500)

    if result.failed():
        logger.error('Cron job %s falhou: %r', job_name, result.result)
        return JsonResponse({'ok': False, 'job': job_name, 'error': str(result.result)}, status=500)

    return JsonResponse({
        'ok': True,
        'job': job_name,
        'result': str(result.result) if result.result else 'ok',
    })

"""Endpoint HTTP pra cron externo (substitui Celery Beat em free tier)."""
import hmac
import logging
import os

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from app_shivazen import tasks

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
}


@csrf_exempt
@require_POST
def run_job(request, job_name):
    token_env = os.environ.get('CRON_TOKEN', '')
    token_req = request.headers.get('X-Cron-Token', '')
    if not token_env or not hmac.compare_digest(token_env, token_req):
        logger.warning('Cron: token invalido (%s) IP=%s', job_name, request.META.get('REMOTE_ADDR'))
        return JsonResponse({'error': 'forbidden'}, status=403)

    job = JOB_MAP.get(job_name)
    if not job:
        return JsonResponse({'error': 'unknown job', 'available': list(JOB_MAP)}, status=404)

    try:
        result = job.apply()
        return JsonResponse({
            'ok': True,
            'job': job_name,
            'result': str(result.result) if result.result else 'ok',
        })
    except Exception as e:
        logger.exception('Cron job %s falhou', job_name)
        return JsonResponse({'ok': False, 'job': job_name, 'error': str(e)}, status=500)

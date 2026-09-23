"""Healthcheck endpoints: liveness (processo vivo) + readiness (deps ok)."""
import logging
import os

from django.conf import settings
from django.core.cache import cache
from django.db import DatabaseError, connection
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

logger = logging.getLogger(__name__)


def _manifest_vite_ok() -> bool:
    """False se o build do front nao gerou o manifest do Vite (fora do dev_mode).

    Sem ele todo template com {% vite_asset %} da 500; o healthcheck precisa
    reprovar o deploy em vez de promover uma imagem quebrada.
    """
    cfg = getattr(settings, 'DJANGO_VITE', {}).get('default', {})
    if cfg.get('dev_mode', False):
        return True
    manifest = cfg.get('manifest_path')
    return bool(manifest) and os.path.exists(manifest)


def _pode_pingar_celery(request) -> bool:
    if not getattr(settings, 'CELERY_WORKER_ENABLED', False):  # so existe em prod.py
        return False
    from .cron import _token_ok
    return _token_ok(request.headers.get('X-Cron-Token', ''))


def _ping_celery() -> bool:
    """Ping no worker com a conexao ao broker limitada (1 tentativa, ~1 s)."""
    from celery import current_app
    with current_app.connection_for_write() as conn:
        conn.ensure_connection(max_retries=1, interval_start=0, interval_step=0, timeout=1)
        return bool(current_app.control.ping(timeout=1, connection=conn))


@require_GET
def healthcheck(request):
    """Readiness: 200 se DB+cache ok; 503 se qualquer dependencia falha.

    Mantem path /health/ para compatibilidade com Railway/Docker.
    """
    db_ok = False
    cache_ok = False
    celery_ok = None  # None = nao checado por padrao
    front_ok = _manifest_vite_ok()

    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
        db_ok = True
    except DatabaseError as e:
        logger.error('Healthcheck: banco indisponivel — %s', e)

    try:
        cache.set('_health_probe', '1', 5)
        cache_ok = cache.get('_health_probe') == '1'
    except Exception as e:
        logger.error('Healthcheck: cache indisponivel — %s', e)

    # Celery ping opcional (?celery=1) SO com worker ligado e header X-Cron-Token:
    # o endpoint e publico e, sem worker (prod hoje), o broker nao existe — cada
    # ping anonimo prendia uma das 4 threads do gunicorn por ~6 s (retries do kombu).
    if request.GET.get('celery') == '1' and _pode_pingar_celery(request):
        try:
            celery_ok = _ping_celery()
        except Exception as e:
            logger.error('Healthcheck: celery ping falhou — %s', e)
            celery_ok = False

    ok = db_ok and cache_ok and front_ok and (celery_ok is not False)
    payload = {
        'status': 'ok' if ok else 'degraded',
        'db': db_ok,
        'cache': cache_ok,
        'front': front_ok,
        'timestamp': timezone.now().isoformat(),
    }
    if celery_ok is not None:
        payload['celery'] = celery_ok
    return JsonResponse(payload, status=200 if ok else 503)


@require_GET
def liveness(request):
    """Liveness: processo vivo, sem checar banco/cache (healthcheck do Railway).

    Unica checagem: o bundle do front existe (senao o site inteiro da 500 e o
    deploy nao pode ser promovido). Arquivo local, custo desprezivel.
    """
    if not _manifest_vite_ok():
        logger.error('Healthcheck: manifest do Vite ausente — rode o build do front')
        return JsonResponse(
            {'status': 'front_build_ausente', 'timestamp': timezone.now().isoformat()},
            status=503,
        )
    return JsonResponse({'status': 'alive', 'timestamp': timezone.now().isoformat()})

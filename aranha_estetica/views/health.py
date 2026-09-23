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

    # Celery ping opcional (so verifica se query string ?celery=1)
    if request.GET.get('celery') == '1':
        try:
            from celery import current_app
            replies = current_app.control.ping(timeout=1)
            celery_ok = bool(replies)
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

"""Healthcheck endpoints: liveness (processo vivo) + readiness (deps ok)."""
import logging

from django.core.cache import cache
from django.db import DatabaseError, connection
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

logger = logging.getLogger(__name__)


@require_GET
def healthcheck(request):
    """Readiness: 200 se DB+cache ok; 503 se qualquer dependencia falha.

    Mantem path /health/ para compatibilidade com Railway/Docker.
    """
    db_ok = False
    cache_ok = False
    celery_ok = None  # None = nao checado por padrao

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

    ok = db_ok and cache_ok and (celery_ok is not False)
    payload = {
        'status': 'ok' if ok else 'degraded',
        'db': db_ok,
        'cache': cache_ok,
        'timestamp': timezone.now().isoformat(),
    }
    if celery_ok is not None:
        payload['celery'] = celery_ok
    return JsonResponse(payload, status=200 if ok else 503)


@require_GET
def liveness(request):
    """Liveness: processo vivo, sem checar dependencias. Sempre 200."""
    return JsonResponse({'status': 'alive', 'timestamp': timezone.now().isoformat()})

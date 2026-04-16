"""Healthcheck endpoint para monitoramento."""
import logging

from django.db import connection
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

logger = logging.getLogger(__name__)


@require_GET
def healthcheck(request):
    """Retorna status da aplicacao e conectividade com o banco.

    200 = tudo ok
    503 = banco indisponivel
    """
    db_ok = False
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
        db_ok = True
    except Exception as e:
        logger.error('Healthcheck: banco indisponivel — %s', e)

    status_code = 200 if db_ok else 503
    return JsonResponse(
        {
            'status': 'ok' if db_ok else 'degraded',
            'db': db_ok,
            'timestamp': timezone.now().isoformat(),
        },
        status=status_code,
    )

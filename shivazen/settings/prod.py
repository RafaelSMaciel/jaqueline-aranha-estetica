"""Settings de producao."""
import os

from .base import *  # noqa: F401,F403

DEBUG = False

# HTTPS obrigatorio em producao
SECURE_SSL_REDIRECT = os.environ.get('USE_HTTPS', 'True') == 'True'
# Healthchecks do Railway usam HTTP na rede interna; excluir do redirect
SECURE_REDIRECT_EXEMPT = [r'^healthz/$', r'^health/$']
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# HSTS — 1 ano
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Session mais curta em prod
SESSION_COOKIE_AGE = int(os.environ.get('SESSION_COOKIE_AGE', 1800))  # 30 min default
SESSION_EXPIRE_AT_BROWSER_CLOSE = True

# Trust proxy headers do Railway
USE_X_FORWARDED_HOST = True
USE_X_FORWARDED_PORT = True

# DB connection health checks (Django 4.1+)
DATABASES['default']['CONN_HEALTH_CHECKS'] = True
DATABASES['default']['CONN_MAX_AGE'] = 600

# Logging mais restrito em prod
LOGGING['root']['level'] = 'WARNING'
LOGGING['loggers']['django']['level'] = 'WARNING'

# Prod sem Redis = risco (cache/session/rate-limit cross-worker inconsistente)
if not os.environ.get('REDIS_URL'):
    import warnings
    warnings.warn(
        'REDIS_URL ausente em producao — cache/session/rate-limit degradados. '
        'Configure Redis para correcao entre workers.',
        RuntimeWarning,
    )

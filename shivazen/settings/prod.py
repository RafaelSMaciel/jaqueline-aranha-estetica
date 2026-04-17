"""Settings de producao."""
import os

from .base import *  # noqa: F401,F403

DEBUG = False

# HTTPS obrigatorio em producao
SECURE_SSL_REDIRECT = os.environ.get('USE_HTTPS', 'True') == 'True'
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# HSTS — 1 ano
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Session mais curta em prod
SESSION_COOKIE_AGE = int(os.environ.get('SESSION_COOKIE_AGE', 1800))  # 30 min default

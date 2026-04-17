"""Entry-point do package de settings.

Escolha do modulo via env var `DJANGO_ENV` (dev|prod). Fallback para `dev`
em ambiente local e `prod` quando Railway e detectado.
"""
import os

_env = os.environ.get('DJANGO_ENV', '').lower().strip()
if not _env:
    _env = 'prod' if os.environ.get('RAILWAY_ENVIRONMENT_NAME') else 'dev'

if _env == 'prod':
    from .prod import *  # noqa: F401,F403
else:
    from .dev import *  # noqa: F401,F403

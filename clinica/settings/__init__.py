"""Entry-point do package de settings.

Escolha do modulo via env var `DJANGO_ENV` (dev|prod). Fallback para `dev`
em ambiente local e `prod` quando Railway e detectado.

Qualquer outro valor ('production', 'staging'...) derruba o boot: antes caia
em silencio no dev.py (DEBUG=True, e-mail no console, SMS/WhatsApp "fingindo"
envio, checks de prod mudos) mesmo no Railway. Falhar no preDeploy mantem o
deploy anterior no ar.
"""
import os

_env = os.environ.get('DJANGO_ENV', '').lower().strip()
if not _env:
    _env = 'prod' if os.environ.get('RAILWAY_ENVIRONMENT_NAME') else 'dev'

if _env not in ('dev', 'prod'):
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured(
        f"DJANGO_ENV={os.environ.get('DJANGO_ENV')!r} invalido: use 'dev' ou 'prod' "
        '(ou deixe vazio: prod no Railway, dev fora dele).'
    )

if _env == 'prod':
    from .prod import *  # noqa: F401,F403
else:
    from .dev import *  # noqa: F401,F403

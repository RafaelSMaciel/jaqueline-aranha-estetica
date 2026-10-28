"""Entry-point do package de settings.

Escolha do modulo via env var `DJANGO_ENV` (dev|prod|staging|test). Fallback para `dev`
em ambiente local e `prod` quando Railway e detectado. `staging` = ambiente de testes
publicado (Railway "dev"): producao + travas de envio (settings/staging.py). `dev` no
Railway derruba o boot: publicaria DEBUG=True (traceback, cookies sem Secure, IP do proxy).

`manage.py test` (o runner unico da suite) sempre usa `test` — unica deteccao de "estou em
teste": nem um DJANGO_ENV=prod herdado leva a suite p/ o banco de producao.

Qualquer outro valor ('production', 'staging'...) derruba o boot: antes caia
em silencio no dev.py (DEBUG=True, e-mail no console, SMS/WhatsApp "fingindo"
envio, checks de prod mudos) mesmo no Railway. Falhar no preDeploy mantem o
deploy anterior no ar.
"""
import os
import sys

_env = os.environ.get('DJANGO_ENV', '').lower().strip()
if sys.argv[1:2] == ['test']:
    _env = 'test'
elif not _env:
    _env = 'prod' if os.environ.get('RAILWAY_ENVIRONMENT_NAME') else 'dev'

if _env not in ('dev', 'prod', 'staging', 'test'):
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured(
        f"DJANGO_ENV={os.environ.get('DJANGO_ENV')!r} invalido: use 'dev', 'prod', 'staging' ou 'test' "
        '(ou deixe vazio: prod no Railway, dev fora dele).'
    )
if _env == 'dev' and os.environ.get('RAILWAY_ENVIRONMENT_NAME'):
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured(
        'DJANGO_ENV=dev no Railway publicaria o settings de desenvolvimento (DEBUG=True): '
        'use prod (producao) ou staging (ambiente de testes).'
    )

if _env == 'prod':
    from .prod import *  # noqa: F401,F403
elif _env == 'staging':
    from .staging import *  # noqa: F401,F403
elif _env == 'test':
    from .test import *  # noqa: F401,F403
else:
    from .dev import *  # noqa: F401,F403

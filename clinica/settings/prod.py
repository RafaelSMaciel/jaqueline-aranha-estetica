"""Settings de producao."""
# ruff: noqa: E402, F405  (star import intencional)
import os

# Prod nunca herda DEBUG da env: base.py deriva dele dev_mode do Vite, loaders
# cacheados, cookie seguro do 2FA, axes verbose, ssl do banco e formato de log.
# Reatribuir so DEBUG depois do import deixava esses derivados incoerentes.
os.environ['DEBUG'] = 'False'

from .base import *  # noqa: F401,F403

DEBUG = False
DJANGO_VITE['default']['dev_mode'] = False  # reforco: sempre le o manifest do build

# HTTPS obrigatorio em producao
SECURE_SSL_REDIRECT = os.environ.get('USE_HTTPS', 'True') == 'True'
# Healthchecks do Railway usam HTTP na rede interna; excluir do redirect
SECURE_REDIRECT_EXEMPT = [r'^healthz/$', r'^health/$']
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Healthcheck do Railway chega com Host 'healthcheck.railway.app'
if 'healthcheck.railway.app' not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append('healthcheck.railway.app')

# HSTS — 1 ano
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Sessao da equipe: 8h deslizantes (renova a cada request; expira apos 8h
# parada ou ao fechar o navegador). 30 min fixos deslogavam no meio do atendimento.
SESSION_COOKIE_AGE = int(os.environ.get('SESSION_COOKIE_AGE', 8 * 3600))
SESSION_SAVE_EVERY_REQUEST = True
SESSION_EXPIRE_AT_BROWSER_CLOSE = True

# Trust proxy headers do Railway
USE_X_FORWARDED_HOST = True
USE_X_FORWARDED_PORT = True

# IP do cliente: o edge do Railway envia o IP real em X-Real-IP (REMOTE_ADDR e o
# proxy, compartilhado por todos). CLIENT_IP_HEADER='' desliga (ex.: sem proxy).
CLIENT_IP_HEADER = os.environ.get('CLIENT_IP_HEADER', 'HTTP_X_REAL_IP')

# DB connection health checks (Django 4.1+)
DATABASES['default']['CONN_HEALTH_CHECKS'] = True
DATABASES['default']['CONN_MAX_AGE'] = 600

# Estaticos: whitenoise com hash + gzip/brotli pre-gerados e cache imutavel.
# O collectstatic roda no build da imagem (Dockerfile) em BASE_DIR/staticfiles;
# a env STATIC_ROOT legada (/tmp) e ignorada p/ runtime achar o mesmo diretorio.
STORAGES['staticfiles'] = {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'}
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')

# E-mail: sem EMAIL_BACKEND explicito nao ha provedor -> dummy (nada sai, nada
# vai p/ o stdout; o console imprimia links de reset/token nos logs). O system
# check aranha.W003 avisa, e utils/email trata como "nao configurado".
EMAIL_BACKEND = os.environ.get('EMAIL_BACKEND') or 'django.core.mail.backends.dummy.EmailBackend'

# SMS: sem ZENVIA_API_TOKEN/ZENVIA_FROM o envio falha fechado (nunca "finge" sucesso)
SMS_EXIGIR_PROVEDOR = True

# Logging mais restrito em prod
LOGGING['root']['level'] = 'WARNING'
LOGGING['loggers']['django']['level'] = 'WARNING'

# Celery: sem worker (padrao no Railway) as tasks rodam eager (sincronas) e o
# cron externo chama os jobs via HTTP. REDIS_URL sozinho NAO liga o worker
# (antes desligava o eager e todo .delay() tentava um broker inexistente).
CELERY_WORKER_ENABLED = os.environ.get('CELERY_WORKER_ENABLED', '').strip().lower() == 'true'
if not CELERY_WORKER_ENABLED:
    CELERY_TASK_ALWAYS_EAGER = True
    CELERY_TASK_EAGER_PROPAGATES = False
    # Em eager, retry/autoretry reexecuta NA HORA dentro do request (sem countdown):
    # 4 tentativas SMTP no POST do agendamento. Sem retries sincronos.
    CELERY_TASK_ANNOTATIONS = {'*': {'max_retries': 0}}

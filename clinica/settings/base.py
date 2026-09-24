"""Settings base — compartilhados entre dev e prod."""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


# Base dir aponta para a raiz do projeto (2 niveis acima deste arquivo)
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Carrega .env da raiz do projeto
load_dotenv(BASE_DIR / '.env')


# ─── SENTRY (opcional) ───────────────────────────────────────────────
try:
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration

    _sentry_dsn = os.environ.get('SENTRY_DSN')
    if _sentry_dsn:
        from aranha_estetica.utils.pii import sentry_before_send
        sentry_sdk.init(
            dsn=_sentry_dsn,
            integrations=[DjangoIntegration()],
            traces_sample_rate=float(os.environ.get('SENTRY_TRACES_SAMPLE_RATE', '0.2')),
            send_default_pii=False,
            before_send=sentry_before_send,
        )
except ImportError:
    pass


# ─── SECURITY ────────────────────────────────────────────────────────
_secret_key = os.environ.get('DJANGO_SECRET_KEY')
DEBUG = os.environ.get('DEBUG', '').lower() in ('true', '1', 'yes', 'on')
if not _secret_key and (
    os.environ.get('RAILWAY_ENVIRONMENT_NAME') or not DEBUG
):
    raise RuntimeError(
        'DJANGO_SECRET_KEY nao definida — exigida fora de DEBUG=True ou em ambiente Railway.'
    )
SECRET_KEY = _secret_key or 'django-insecure-dev-only-key-do-not-use-in-production'

ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get('ALLOWED_HOSTS', '127.0.0.1,localhost').split(',')
    if h.strip()
]
RAILWAY_DOMAIN = os.environ.get('RAILWAY_PUBLIC_DOMAIN', '')
if RAILWAY_DOMAIN:
    ALLOWED_HOSTS.append(RAILWAY_DOMAIN)

# URL publica absoluta (links de e-mail/WhatsApp/JSON-LD). Fonte unica: sempre
# ler settings.SITE_URL (nunca os.environ direto). Ordem: env SITE_URL ->
# dominio publico do Railway -> localhost (dev).
SITE_URL = (
    os.environ.get('SITE_URL')
    or (f'https://{RAILWAY_DOMAIN}' if RAILWAY_DOMAIN else 'http://127.0.0.1:8000')
).rstrip('/')


# ─── APPS ────────────────────────────────────────────────────────────
INSTALLED_APPS = [
    'aranha_estetica',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    # staticfiles com ignore de static/src (fonte do Vite) no collectstatic
    'clinica.apps.StaticFilesSemFonteConfig',
    'django.contrib.sitemaps',
    'django_otp',
    'django_otp.plugins.otp_totp',
    'django_otp.plugins.otp_static',
    'two_factor',
    'axes',
    'rest_framework',
    'drf_spectacular',
    'django_vite',
    'django_cotton',
]

# DRF
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.SessionAuthentication',
    ],
    # Fechado por padrao: a API e so p/ staff. Cobre a raiz do router
    # (/api/v1/ listava os endpoints p/ PROFISSIONAL) e viewsets futuros que
    # esquecam permission_classes; os atuais declaram IsStaff.
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAdminUser',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 20,
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '60/hour',
        'user': '1000/hour',
    },
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
}

SPECTACULAR_SETTINGS = {
    'TITLE': 'Jaqueline Aranha Estética — API',
    'DESCRIPTION': 'API REST da plataforma de clinica estetica',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
    'COMPONENT_SPLIT_REQUEST': True,
    # Versao fixa (o default e @latest: um release novo do CDN mudaria a UI em
    # prod sem deploy). Atualizar junto: DIST e FAVICON.
    'SWAGGER_UI_DIST': 'https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.32.15',
    'SWAGGER_UI_FAVICON_HREF': 'https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.32.15/favicon-32x32.png',
}

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.middleware.gzip.GZipMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    # Sem LocaleMiddleware: site so pt-BR (sem traducoes en/es; Accept-Language
    # 'en' trocava formato de numero/data e mensagens do Django).
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django_otp.middleware.OTPMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'aranha_estetica.middleware.ContentSecurityPolicyMiddleware',
    'aranha_estetica.middleware.SecurityHeadersMiddleware',
    'aranha_estetica.middleware.Enforce2FAMiddleware',
    'axes.middleware.AxesMiddleware',
    # @ratelimit(block=True) -> pagina 429 amigavel (RATELIMIT_VIEW) em vez de 403 cru
    'django_ratelimit.middleware.RatelimitMiddleware',
]

# Trusted proxies: atras de Cloudflare/Railway, respeitar X-Forwarded-For
USE_X_FORWARDED_HOST = os.environ.get('USE_X_FORWARDED_HOST', 'True') == 'True'
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# IP do cliente (utils/security.client_ip): header escrito pelo proxy de borda.
# Vazio em dev/testes (REMOTE_ADDR; header seria forjavel sem proxy na frente).
# prod.py liga 'HTTP_X_REAL_IP' (Railway). Rate-limit e axes usam a mesma funcao.
CLIENT_IP_HEADER = os.environ.get('CLIENT_IP_HEADER', '')
RATELIMIT_IP_META_KEY = 'aranha_estetica.utils.security.client_ip'
AXES_CLIENT_IP_CALLABLE = 'aranha_estetica.utils.security.client_ip'

AUTHENTICATION_BACKENDS = [
    'axes.backends.AxesStandaloneBackend',
    'django.contrib.auth.backends.ModelBackend',
]

# django-axes config
AXES_FAILURE_LIMIT = int(os.environ.get('AXES_FAILURE_LIMIT', 5))
AXES_COOLOFF_TIME = float(os.environ.get('AXES_COOLOFF_TIME_HOURS', '1'))
AXES_LOCKOUT_PARAMETERS = ['ip_address', 'username']
AXES_RESET_ON_SUCCESS = True
# Pagina pt-BR (status 429) em vez do texto cru em ingles do axes
AXES_LOCKOUT_TEMPLATE = 'axes_bloqueio.html'
# Verbose so em dev (em prod gera logs excessivos via Sentry)
AXES_VERBOSE = DEBUG

ROOT_URLCONF = 'clinica.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        # APP_DIRS removido: incompativel com loaders explicitos (django-cotton exige loader proprio)
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'aranha_estetica.context_processors.clinica_globals',
                'aranha_estetica.context_processors.csp_nonce',
                'aranha_estetica.context_processors.tema_atual',
            ],
            # cotton exige loaders explicitos. cached.Loader SO em prod —
            # em DEBUG ele trava o hot-reload de template (edicao nao reflete sem restart).
            'loaders': (
                [(
                    'django.template.loaders.cached.Loader', [
                        'django_cotton.cotton_loader.Loader',
                        'django.template.loaders.filesystem.Loader',
                        'django.template.loaders.app_directories.Loader',
                    ],
                )]
                if not DEBUG else [
                    'django_cotton.cotton_loader.Loader',
                    'django.template.loaders.filesystem.Loader',
                    'django.template.loaders.app_directories.Loader',
                ]
            ),
            'builtins': ['django_cotton.templatetags.cotton'],
        },
    },
]

WSGI_APPLICATION = 'clinica.wsgi.application'


# ─── DATABASE ────────────────────────────────────────────────────────
import dj_database_url  # noqa: E402

DATABASE_URL = os.environ.get('DATABASE_URL')

if DATABASE_URL:
    # dj_database_url.config() ja le DATABASE_URL do ambiente; nao precisa repassa-lo.
    # ssl_require fora de DEBUG: Postgres gerenciado (Railway) deve exigir SSL.
    DATABASES = {
        'default': dj_database_url.config(
            conn_max_age=600,
            conn_health_checks=True,
            ssl_require=not DEBUG,
        ),
    }
else:
    _dev_engine = os.environ.get('DB_ENGINE', 'django.db.backends.sqlite3')
    if _dev_engine == 'django.db.backends.sqlite3':
        DATABASES = {
            'default': {
                'ENGINE': 'django.db.backends.sqlite3',
                'NAME': BASE_DIR / 'db_dev.sqlite3',
            }
        }
    else:
        DATABASES = {
            'default': {
                'ENGINE': _dev_engine,
                'NAME': os.environ.get('DB_NAME', 'aranha_estetica_dev'),
                'USER': os.environ.get('DB_USER', 'postgres'),
                'PASSWORD': os.environ.get('DB_PASSWORD', ''),
                'HOST': os.environ.get('DB_HOST', 'localhost'),
                'PORT': os.environ.get('DB_PORT', '5432'),
            }
        }

# Test DB — SQLite (rapido) por padrao. TEST_DATABASE_URL=postgres://... roda a
# suite no Postgres (CI): DDL/CHECK/EXCLUDE/trigger das migrations so existem la.
if 'test' in sys.argv or 'test_coverage' in sys.argv:
    _test_db_url = os.environ.get('TEST_DATABASE_URL', '').strip()
    if _test_db_url:
        DATABASES['default'] = dj_database_url.parse(_test_db_url)
    else:
        DATABASES['default'] = {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db_test.sqlite3',
        }


# ─── AUTH ────────────────────────────────────────────────────────────
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
     'OPTIONS': {'min_length': 10}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

AUTH_USER_MODEL = 'aranha_estetica.Usuario'
LOGIN_URL = '/admin-login/'
LOGIN_REDIRECT_URL = '/painel/'

# 2FA (django-two-factor-auth)
TWO_FACTOR_PATCH_ADMIN = False  # admin patched manualmente via clinica.urls
TWO_FACTOR_REMEMBER_COOKIE_AGE = int(os.environ.get('TWO_FACTOR_REMEMBER_COOKIE_AGE', 30 * 24 * 3600))
TWO_FACTOR_REMEMBER_COOKIE_SECURE = not DEBUG
TWO_FACTOR_REMEMBER_COOKIE_HTTPONLY = True
TWO_FACTOR_REMEMBER_COOKIE_SAMESITE = 'Lax'
LOGOUT_REDIRECT_URL = '/'

# 2FA obrigatorio p/ ADMIN (utils/dois_fatores). Sem a env o codigo decide em
# runtime: ligado fora de DEBUG. ADMIN_2FA_OBRIGATORIO=false e valvula de
# emergencia (system check aranha.W008 avisa enquanto estiver desligado).
_2fa_obrigatorio = os.environ.get('ADMIN_2FA_OBRIGATORIO', '').strip().lower()
if _2fa_obrigatorio:
    ADMIN_2FA_OBRIGATORIO = _2fa_obrigatorio in ('true', '1', 'yes', 'on')
# 2FA obrigatorio tambem p/ PROFISSIONAL (le prontuario/alertas de saude, art. 11).
# Opt-in por env (default desligado); utils/dois_fatores decide em runtime.
PROFISSIONAL_2FA_OBRIGATORIO = (
    os.environ.get('PROFISSIONAL_2FA_OBRIGATORIO', '').strip().lower() in ('true', '1', 'yes', 'on')
)
# Django admin do django_otp: nunca mostrar semente TOTP/QR/codigos de backup
# de outro usuario (config/qrcode da 403). Reset de 2FA = `manage.py setup_2fa --force`.
OTP_ADMIN_HIDE_SENSITIVE_DATA = True

# frame-ancestors das paginas embutiveis (middleware CSP). Vazio = 'https:'.
# Ex.: 'https://linktr.ee https://www.instagram.com'
EMBED_FRAME_ANCESTORS = os.environ.get('EMBED_FRAME_ANCESTORS', '').strip()


# ─── I18N ────────────────────────────────────────────────────────────
# Site so em pt-BR (sem LocaleMiddleware; USE_I18N mantem as traducoes pt-BR
# do proprio Django — mensagens de validacao, datas, numeros).
LANGUAGE_CODE = 'pt-br'
TIME_ZONE = 'America/Sao_Paulo'
USE_I18N = True
USE_TZ = True

LANGUAGES = [
    ('pt-br', 'Português'),
]


# ─── STATIC / MEDIA ──────────────────────────────────────────────────
STATIC_URL = '/static/'
# Sem STATICFILES_DIRS: aranha_estetica/static ja e achado pelo AppDirectoriesFinder
# (duplicava tudo no collectstatic). static/src (fonte do Vite) fica fora da
# coleta via clinica.apps.StaticFilesSemFonteConfig.
STATIC_ROOT = os.environ.get('STATIC_ROOT') or os.path.join(BASE_DIR, 'staticfiles')
MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

# Django 5.1+ ignora STATICFILES_STORAGE/DEFAULT_FILE_STORAGE: so STORAGES vale.
# Aqui (dev/testes) storage simples, sem manifest; prod.py liga o whitenoise
# CompressedManifest (hash + gzip/brotli + cache longo), preenchido no build.
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}

# django-storages opcional (S3 / R2 / GCS) - ativa via AWS_STORAGE_BUCKET_NAME
_S3_BUCKET = os.environ.get('AWS_STORAGE_BUCKET_NAME')
if _S3_BUCKET:
    try:
        import storages  # noqa: F401
        STORAGES['default'] = {'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage'}
        AWS_STORAGE_BUCKET_NAME = _S3_BUCKET
        AWS_ACCESS_KEY_ID = os.environ.get('AWS_ACCESS_KEY_ID')
        AWS_SECRET_ACCESS_KEY = os.environ.get('AWS_SECRET_ACCESS_KEY')
        AWS_S3_REGION_NAME = os.environ.get('AWS_S3_REGION_NAME', 'us-east-1')
        AWS_S3_ENDPOINT_URL = os.environ.get('AWS_S3_ENDPOINT_URL')  # R2/MinIO
        AWS_S3_CUSTOM_DOMAIN = os.environ.get('AWS_S3_CUSTOM_DOMAIN')
        AWS_DEFAULT_ACL = None
        AWS_S3_FILE_OVERWRITE = False
        AWS_QUERYSTRING_AUTH = False
    except ImportError:
        pass

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# ─── DJANGO-VITE ─────────────────────────────────────────────────────
# manifest.json fica em aranha_estetica/static/dist/ (confirmado empiricamente com Vite 6)
DJANGO_VITE = {
    'default': {
        'dev_mode': DEBUG,
        # Vite usa base '/static/dist/' (vite.config.js); o prefixo 'dist' alinha
        # as URLs de asset (dev e prod) ao subdir onde o bundle e servido/buildado.
        'static_url_prefix': 'dist',
        'manifest_path': BASE_DIR / 'aranha_estetica' / 'static' / 'dist' / 'manifest.json',
    },
}

# Em testes, forcar dev_mode p/ que vite_asset nao precise do manifest.json no disco.
# Em dev_mode, as tags emitem URLs apontando p/ dev server (sem ler manifest.json).
if 'test' in sys.argv or 'test_coverage' in sys.argv:
    DJANGO_VITE['default']['dev_mode'] = True


# ─── SECURITY BASE ───────────────────────────────────────────────────
X_FRAME_OPTIONS = 'DENY'
SECURE_CONTENT_TYPE_NOSNIFF = True
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SAMESITE = 'Lax'
# 8h jornada (configuravel) — antes era 1h, ruim p/ profissionais que ficam logados o dia
SESSION_COOKIE_AGE = int(os.environ.get('SESSION_COOKIE_AGE', 8 * 3600))
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
# False evita gravar sessao em DB/Redis a cada request (overhead em alta carga)
SESSION_SAVE_EVERY_REQUEST = False
SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'
SECURE_CROSS_ORIGIN_OPENER_POLICY = 'same-origin'

RATELIMIT_USE_CACHE = 'default'
RATELIMIT_FAIL_OPEN = False
RATELIMIT_VIEW = 'aranha_estetica.views.public.limite_excedido'

FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FIELDS = 200

CSRF_TRUSTED_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        'CSRF_TRUSTED_ORIGINS', 'http://127.0.0.1:8000,http://localhost:8000',
    ).split(',')
    if o.strip()
]
if RAILWAY_DOMAIN:
    CSRF_TRUSTED_ORIGINS.append(f'https://{RAILWAY_DOMAIN}')


# ─── EMAIL ───────────────────────────────────────────────────────────
EMAIL_BACKEND = os.environ.get('EMAIL_BACKEND') or 'django.core.mail.backends.console.EmailBackend'
EMAIL_HOST = os.environ.get('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', 587))
EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', 'True') == 'True'
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
# Remetente: endereco de um DOMINIO VERIFICADO no provedor (SPF/DKIM), ex.:
# 'Jaqueline Aranha Estetica <contato@seudominio.com.br>'. O default e so
# placeholder: provedor HTTP recusa remetente de dominio nao verificado.
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'noreply@clinica.com.br')
# Sem timeout o socket SMTP pendura a thread do gunicorn (Celery eager = no request)
EMAIL_TIMEOUT = int(os.environ.get('EMAIL_TIMEOUT') or 10)

# Provedor HTTP (django-anymail): o plano Hobby do Railway bloqueia SMTP de saida.
# EMAIL_BACKEND=anymail.backends.<esp>.EmailBackend + a chave do ESP na env; so as
# chaves presentes entram em ANYMAIL (check aranha.W011 avisa se faltar a do
# backend escolhido). Ex.: anymail.backends.resend.EmailBackend + RESEND_API_KEY.
ANYMAIL_CHAVES_ENV = (
    'RESEND_API_KEY', 'BREVO_API_KEY', 'SENDGRID_API_KEY',
    'MAILGUN_API_KEY', 'MAILGUN_SENDER_DOMAIN', 'POSTMARK_SERVER_TOKEN',
)
if EMAIL_BACKEND.startswith('anymail.'):
    INSTALLED_APPS.append('anymail')
    ANYMAIL = {
        chave: valor for chave in ANYMAIL_CHAVES_ENV
        if (valor := (os.environ.get(chave) or '').strip())
    }
    # Default do anymail e 30 s: mesmo teto do SMTP (request/Celery eager)
    ANYMAIL['REQUESTS_TIMEOUT'] = EMAIL_TIMEOUT

# SMS (utils/sms): SMS_DEV_LOG_ONLY=true so loga (dev/testes; nunca o codigo).
# Fora disso, sem ZENVIA_API_TOKEN/ZENVIA_FROM o envio falha fechado (False).
SMS_DEV_LOG_ONLY = os.environ.get('SMS_DEV_LOG_ONLY', '').strip().lower() == 'true'

# Password reset: token TTL 1h (default Django = 3 dias). Reduz janela de ataque.
PASSWORD_RESET_TIMEOUT = int(os.environ.get('PASSWORD_RESET_TIMEOUT_SECONDS', 3600))


# ─── LOGGING ─────────────────────────────────────────────────────────
_LOG_FORMATTER = 'verbose'
if not DEBUG:
    try:
        import pythonjsonlogger  # noqa: F401
        _LOG_FORMATTER = 'json'
    except ImportError:
        pass

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {process:d} {thread:d} {message}',
            'style': '{',
        },
        # So referenciado quando not DEBUG (_LOG_FORMATTER vira 'json'); em DEBUG
        # o handler usa 'verbose', entao este formatter nunca e instanciado.
        'json': {
            '()': 'pythonjsonlogger.jsonlogger.JsonFormatter',
            'format': '%(asctime)s %(name)s %(levelname)s %(message)s',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': _LOG_FORMATTER,
        },
    },
    'root': {'handlers': ['console'], 'level': 'WARNING'},
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': os.environ.get('DJANGO_LOG_LEVEL', 'INFO'),
            'propagate': False,
        },
        'aranha_estetica': {
            'handlers': ['console'],
            'level': 'DEBUG' if DEBUG else 'INFO',
            'propagate': False,
        },
    },
}


# ─── CELERY + CACHE ──────────────────────────────────────────────────
# REDIS_URL serve cache/sessao; broker/result do Celery usam CELERY_* ou caem nele.
# Worker Celery so e usado com CELERY_WORKER_ENABLED=true (prod.py); sem isso
# as tasks rodam eager e os jobs periodicos vem do cron HTTP (views/cron.py).
REDIS_URL = os.environ.get('REDIS_URL', '')
CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL') or REDIS_URL or 'redis://localhost:6379/0'
CELERY_RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND') or REDIS_URL or 'redis://localhost:6379/0'
CELERY_ACCEPT_CONTENT = ['application/json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE
# Jobs de manutencao ficam fora de tasks.py (autodiscover so acha tasks.py)
CELERY_IMPORTS = ('aranha_estetica.tasks_manutencao',)

if REDIS_URL:
    CACHES = {
        'default': {
            'BACKEND': 'django_redis.cache.RedisCache',
            'LOCATION': REDIS_URL,
            'OPTIONS': {'CLIENT_CLASS': 'django_redis.client.DefaultClient'},
        }
    }
    SESSION_ENGINE = 'django.contrib.sessions.backends.cache'
    SESSION_CACHE_ALIAS = 'default'
else:
    # LocMemCache e por-processo: ok com 1 worker gunicorn (threads compartilham).
    # Com mais workers, rate-limit/axes/lock de slot/quota de SMS deixam de ser
    # compartilhados — ai sim precisa de REDIS_URL (so cache; worker Celery e
    # outra coisa: CELERY_WORKER_ENABLED=true).
    try:
        _web_workers = int(os.environ.get('WEB_CONCURRENCY', '1') or 1)
    except ValueError:
        _web_workers = 1
    if not DEBUG and _web_workers > 1:
        import warnings

        warnings.warn(
            f'WEB_CONCURRENCY={_web_workers} sem REDIS_URL: LocMemCache e por processo — '
            'rate-limit/axes/lock de slot nao sao compartilhados. Use 1 worker ou defina REDIS_URL.',
            RuntimeWarning,
            stacklevel=2,
        )
    CACHES = {
        'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'},
    }
    SESSION_ENGINE = 'django.contrib.sessions.backends.db'


# Agendamento Celery Beat
from celery.schedules import crontab  # noqa: E402

CELERY_BEAT_SCHEDULE = {
    'lembretes-diarios-08h': {
        'task': 'aranha_estetica.tasks.job_enviar_lembrete_dia_seguinte',
        'schedule': crontab(hour=8, minute=0),
    },
    'envio-pesquisa-nps-diaria': {
        'task': 'aranha_estetica.tasks.job_pesquisa_satisfacao_24h',
        'schedule': crontab(hour=10, minute=0),
    },
    'alerta-detrator-nps': {
        'task': 'aranha_estetica.tasks.job_alerta_detrator_nps',
        'schedule': crontab(hour=10, minute=30),
    },
    'verificar-pacotes-expirando': {
        'task': 'aranha_estetica.tasks.job_verificar_pacotes_expirando',
        'schedule': crontab(hour=7, minute=0),
    },
    'expirar-pacotes': {
        'task': 'aranha_estetica.tasks.job_expirar_pacotes',
        'schedule': crontab(hour=0, minute=30),
    },
    'limpeza-status-atendimentos': {
        'task': 'aranha_estetica.tasks.job_limpeza_status_atendimentos',
        'schedule': crontab(hour=23, minute=0),
    },
    'aniversario-clientes': {
        'task': 'aranha_estetica.tasks.job_aniversario_clientes',
        'schedule': crontab(hour=9, minute=0),
    },
    'lgpd-purgar-inativos': {
        'task': 'aranha_estetica.tasks.job_lgpd_purgar_inativos',
        'schedule': crontab(hour=3, minute=0, day_of_week=0),  # Domingo 3h
    },
    'housekeeping-diario': {
        'task': 'aranha_estetica.tasks_manutencao.job_housekeeping',
        'schedule': crontab(hour=4, minute=0),
    },
    'carregar-feriados-mensal': {
        'task': 'aranha_estetica.tasks_manutencao.job_carregar_feriados',
        'schedule': crontab(hour=4, minute=30, day_of_month=1),
    },
}


# ─── RETENCAO (job_housekeeping) ─────────────────────────────────────
# docs/specs/regras-negocio-registry.md: otp 24h · notif 12m · auditoria 5a
RETENCAO_OTP_HORAS = int(os.environ.get('RETENCAO_OTP_HORAS', '24'))
RETENCAO_NOTIFICACAO_DIAS = int(os.environ.get('RETENCAO_NOTIFICACAO_DIAS', '365'))
RETENCAO_LOG_AUDITORIA_DIAS = int(os.environ.get('RETENCAO_LOG_AUDITORIA_DIAS', str(365 * 5)))
RETENCAO_AXES_LOG_DIAS = int(os.environ.get('RETENCAO_AXES_LOG_DIAS', '90'))

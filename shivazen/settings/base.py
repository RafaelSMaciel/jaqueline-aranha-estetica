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
        sentry_sdk.init(
            dsn=_sentry_dsn,
            integrations=[DjangoIntegration()],
            traces_sample_rate=float(os.environ.get('SENTRY_TRACES_SAMPLE_RATE', '0.2')),
            send_default_pii=False,
        )
except ImportError:
    pass


# ─── SECURITY ────────────────────────────────────────────────────────
_secret_key = os.environ.get('DJANGO_SECRET_KEY')
if not _secret_key and os.environ.get('RAILWAY_ENVIRONMENT_NAME'):
    raise RuntimeError('DJANGO_SECRET_KEY nao definida em producao!')
SECRET_KEY = _secret_key or 'django-insecure-dev-only-key-do-not-use-in-production'

DEBUG = os.environ.get('DEBUG', 'False') == 'True'

ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', '127.0.0.1,localhost').split(',')
RAILWAY_DOMAIN = os.environ.get('RAILWAY_PUBLIC_DOMAIN', '')
if RAILWAY_DOMAIN:
    ALLOWED_HOSTS.append(RAILWAY_DOMAIN)


# ─── APPS ────────────────────────────────────────────────────────────
INSTALLED_APPS = [
    'app_shivazen',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.sitemaps',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'app_shivazen.middleware.ContentSecurityPolicyMiddleware',
    'app_shivazen.middleware.SecurityHeadersMiddleware',
]

ROOT_URLCONF = 'shivazen.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'app_shivazen.context_processors.shivazen_globals',
                'app_shivazen.context_processors.csp_nonce',
            ],
        },
    },
]

WSGI_APPLICATION = 'shivazen.wsgi.application'


# ─── DATABASE ────────────────────────────────────────────────────────
import dj_database_url  # noqa: E402

DATABASE_URL = os.environ.get('DATABASE_URL')

if DATABASE_URL:
    DATABASES = {
        'default': dj_database_url.config(default=DATABASE_URL, conn_max_age=600),
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
                'NAME': os.environ.get('DB_NAME', 'shivazen_dev'),
                'USER': os.environ.get('DB_USER', 'postgres'),
                'PASSWORD': os.environ.get('DB_PASSWORD', ''),
                'HOST': os.environ.get('DB_HOST', 'localhost'),
                'PORT': os.environ.get('DB_PORT', '5432'),
            }
        }

# Test DB — SQLite sempre
if 'test' in sys.argv or 'test_coverage' in sys.argv:
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

AUTH_USER_MODEL = 'app_shivazen.Usuario'
LOGIN_URL = '/admin-login/'
LOGIN_REDIRECT_URL = '/painel/'
LOGOUT_REDIRECT_URL = '/'


# ─── I18N ────────────────────────────────────────────────────────────
LANGUAGE_CODE = 'pt-br'
TIME_ZONE = 'America/Sao_Paulo'
USE_I18N = True
USE_TZ = True


# ─── STATIC / MEDIA ──────────────────────────────────────────────────
STATIC_URL = '/static/'
STATICFILES_DIRS = [os.path.join(BASE_DIR, 'app_shivazen/static')]
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# ─── SECURITY BASE ───────────────────────────────────────────────────
SECURE_BROWSER_XSS_FILTER = True
X_FRAME_OPTIONS = 'DENY'
SECURE_CONTENT_TYPE_NOSNIFF = True
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_AGE = 3600
SESSION_SAVE_EVERY_REQUEST = True
SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'
SECURE_CROSS_ORIGIN_OPENER_POLICY = 'same-origin'

RATELIMIT_USE_CACHE = 'default'
RATELIMIT_FAIL_OPEN = False

FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FIELDS = 200

CSRF_TRUSTED_ORIGINS = os.environ.get(
    'CSRF_TRUSTED_ORIGINS', 'http://127.0.0.1:8000,http://localhost:8000',
).split(',')
if RAILWAY_DOMAIN:
    CSRF_TRUSTED_ORIGINS.append(f'https://{RAILWAY_DOMAIN}')


# ─── EMAIL ───────────────────────────────────────────────────────────
EMAIL_BACKEND = os.environ.get('EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend')
EMAIL_HOST = os.environ.get('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', 587))
EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', 'True') == 'True'
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'noreply@clinica.com.br')


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
        'json': {
            '()': 'pythonjsonlogger.jsonlogger.JsonFormatter',
            'format': '%(asctime)s %(name)s %(levelname)s %(message)s',
        } if not DEBUG else {
            'format': '{levelname} {asctime} {module} {message}',
            'style': '{',
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
        'app_shivazen': {
            'handlers': ['console'],
            'level': 'DEBUG' if DEBUG else 'INFO',
            'propagate': False,
        },
    },
}


# ─── CELERY + CACHE ──────────────────────────────────────────────────
CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL', 'redis://localhost:6379/0')
CELERY_RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0')
CELERY_ACCEPT_CONTENT = ['application/json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE

REDIS_URL = os.environ.get('REDIS_URL', '')
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
    CACHES = {
        'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'},
    }
    SESSION_ENGINE = 'django.contrib.sessions.backends.db'


# Agendamento Celery Beat
from celery.schedules import crontab  # noqa: E402

CELERY_BEAT_SCHEDULE = {
    'lembretes-diarios-08h': {
        'task': 'app_shivazen.tasks.job_enviar_lembrete_dia_seguinte',
        'schedule': crontab(hour=8, minute=0),
    },
    'envio-pesquisa-nps-diaria': {
        'task': 'app_shivazen.tasks.job_pesquisa_satisfacao_24h',
        'schedule': crontab(hour=10, minute=0),
    },
    'alerta-detrator-nps': {
        'task': 'app_shivazen.tasks.job_alerta_detrator_nps',
        'schedule': crontab(hour=10, minute=30),
    },
    'verificar-pacotes-expirando': {
        'task': 'app_shivazen.tasks.job_verificar_pacotes_expirando',
        'schedule': crontab(hour=7, minute=0),
    },
    'expirar-pacotes': {
        'task': 'app_shivazen.tasks.job_expirar_pacotes',
        'schedule': crontab(hour=0, minute=30),
    },
    'limpeza-status-atendimentos': {
        'task': 'app_shivazen.tasks.job_limpeza_status_atendimentos',
        'schedule': crontab(hour=23, minute=0),
    },
    'aniversario-clientes': {
        'task': 'app_shivazen.tasks.job_aniversario_clientes',
        'schedule': crontab(hour=9, minute=0),
    },
}

# shivazen/settings.py
import os
from pathlib import Path
from dotenv import load_dotenv

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env file
load_dotenv(BASE_DIR / '.env')

# --- Configuração Sentry (opcional) ---
try:
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration

    sentry_dsn = os.environ.get('SENTRY_DSN')
    if sentry_dsn:
        sentry_sdk.init(
            dsn=sentry_dsn,
            integrations=[DjangoIntegration()],
            traces_sample_rate=float(os.environ.get('SENTRY_TRACES_SAMPLE_RATE', '0.2')),
            send_default_pii=False,  # SEGURANÇA: Nunca enviar dados pessoais ao Sentry
        )
except ImportError:
    pass  # sentry_sdk não instalado — ignorar em desenvolvimento local

# --- Configuração de Segurança (Variáveis de Ambiente) ---
# Em produção (Railway), crie variáveis de ambiente para estes valores.
# O valor depois da vírgula é um 'default' para desenvolvimento local.

# SEGURANCA: Em producao, DJANGO_SECRET_KEY deve ser definida como variavel de ambiente.
_secret_key = os.environ.get('DJANGO_SECRET_KEY')
if not _secret_key and os.environ.get('RAILWAY_ENVIRONMENT_NAME'):
    raise RuntimeError('DJANGO_SECRET_KEY nao definida em producao!')
SECRET_KEY = _secret_key or 'django-insecure-dev-only-key-do-not-use-in-production'

# SEGURANCA: DEBUG padrao False. Em dev local, defina DEBUG=True no .env
DEBUG = os.environ.get('DEBUG', 'False') == 'True'

# Em produção, defina ALLOWED_HOSTS=seu-dominio.com,www.seu-dominio.com
# Railway define RAILWAY_PUBLIC_DOMAIN automaticamente
ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', '127.0.0.1,localhost').split(',')
RAILWAY_DOMAIN = os.environ.get('RAILWAY_PUBLIC_DOMAIN', '')
if RAILWAY_DOMAIN:
    ALLOWED_HOSTS.append(RAILWAY_DOMAIN)
# SEGURANCA: wildcard .railway.app removido — apenas o dominio especifico eh adicionado acima.


# Application definition

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


# --- Banco de Dados ---
# Railway fornece DATABASE_URL automaticamente ao adicionar PostgreSQL
import dj_database_url

DATABASE_URL = os.environ.get('DATABASE_URL')

if DATABASE_URL:
    DATABASES = {
        'default': dj_database_url.config(default=DATABASE_URL, conn_max_age=600)
    }
else:
    # Dev local — SQLite por padrao, PostgreSQL se DB_ENGINE definida no .env
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

# --- Test Database (SQLite para testes locais) ---
import sys
if 'test' in sys.argv or 'test_coverage' in sys.argv:
    DATABASES['default'] = {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db_test.sqlite3',
    }


# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# Internationalization
LANGUAGE_CODE = 'pt-br'
TIME_ZONE = 'America/Sao_Paulo'
USE_I18N = True
USE_TZ = True


# --- Configurações de Arquivos Estáticos (WhiteNoise) ---
STATIC_URL = '/static/'
STATICFILES_DIRS = [os.path.join(BASE_DIR, 'app_shivazen/static')]

# Padrão WhiteNoise 
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles') 

MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

# Adicionado: Armazenamento otimizado do WhiteNoise
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# --- Adicionado: Apontando para o novo Modelo de Usuário ---
AUTH_USER_MODEL = 'app_shivazen.Usuario'

# --- URLs de Autenticação (Admin Only) ---
LOGIN_URL = '/admin-login/'
LOGIN_REDIRECT_URL = '/painel/'
LOGOUT_REDIRECT_URL = '/'

# --- Configurações de Segurança ---
SECURE_BROWSER_XSS_FILTER = True
X_FRAME_OPTIONS = 'DENY'
SECURE_CONTENT_TYPE_NOSNIFF = True
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True

# Proteção contra CSRF e Session Hijacking
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_AGE = 3600  # Sessão expira em 1 hora
SESSION_SAVE_EVERY_REQUEST = True  # Renova sessão a cada request

# Headers de segurança adicionais
SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'
SECURE_CROSS_ORIGIN_OPENER_POLICY = 'same-origin'

# django-ratelimit: usar cache padrão e falhar silenciosamente se o cache estiver indisponível
RATELIMIT_USE_CACHE = 'default'
RATELIMIT_FAIL_OPEN = False  # SEGURANCA: se cache falhar, bloquear requests (fail-closed)

# Limites de upload (protecao contra upload abuse)
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024  # 5MB
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024   # 5MB
DATA_UPLOAD_MAX_NUMBER_FIELDS = 200

# CSRF Trusted Origins (adicionar domínio de produção)
CSRF_TRUSTED_ORIGINS = os.environ.get(
    'CSRF_TRUSTED_ORIGINS', 'http://127.0.0.1:8000,http://localhost:8000'
).split(',')
if RAILWAY_DOMAIN:
    CSRF_TRUSTED_ORIGINS.append(f'https://{RAILWAY_DOMAIN}')

# Em produção (HTTPS), ative a variável USE_HTTPS=True
USE_HTTPS = os.environ.get('USE_HTTPS', 'False') == 'True'
if USE_HTTPS:
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')


# --- Configuração de Email (para recuperação de senha) ---
EMAIL_BACKEND = os.environ.get('EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend')
EMAIL_HOST = os.environ.get('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', 587))
EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', 'True') == 'True'
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'noreply@clinica.com.br')

# --- Logging Config ---
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
    'root': {
        'handlers': ['console'],
        'level': 'WARNING',
    },
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


# --- Configurações do Celery e Redis ---
# URL do Redis para filas do Celery
CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL', 'redis://localhost:6379/0')
CELERY_RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0')
CELERY_ACCEPT_CONTENT = ['application/json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'

# --- Configurações de Cache e Sessão ---
# Em produção (com Redis), usar cache Redis para sessões.
# Em desenvolvimento local, usar sessões baseadas no banco de dados.
REDIS_URL = os.environ.get('REDIS_URL', '')

if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": REDIS_URL,
            "OPTIONS": {
                "CLIENT_CLASS": "django_redis.client.DefaultClient",
            }
        }
    }
    SESSION_ENGINE = "django.contrib.sessions.backends.cache"
    SESSION_CACHE_ALIAS = "default"
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        }
    }
    SESSION_ENGINE = "django.contrib.sessions.backends.db"

CELERY_TIMEZONE = TIME_ZONE

# Configuracao agendamento do Celery Beat (tarefas recorrentes)
from celery.schedules import crontab
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
        'schedule': crontab(hour=9, minute=0),  # Todo dia as 09:00
    },
}
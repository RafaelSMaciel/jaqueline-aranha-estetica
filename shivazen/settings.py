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

# ATENÇÃO: Troque o valor padrão da SECRET_KEY por um novo se desejar
SECRET_KEY = os.environ.get(
    'DJANGO_SECRET_KEY', 
    'django-insecure-change-me'
)

# Em produção, defina a variável de ambiente DEBUG=False
DEBUG = os.environ.get('DEBUG', 'False') == 'True'

# Em produção, defina ALLOWED_HOSTS=seu-dominio.com,www.seu-dominio.com
# O 'default' listado é voltado para testes locais apenas.
ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', '127.0.0.1,localhost').split(',')


# Application definition

INSTALLED_APPS = [
    'app_shivazen',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
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
            ],
        },
    },
]

WSGI_APPLICATION = 'shivazen.wsgi.application'


# --- Banco de Dados com Variáveis de Ambiente ---
DB_ENGINE = os.environ.get('DB_ENGINE', 'django.db.backends.sqlite3')

if DB_ENGINE == 'django.db.backends.sqlite3':
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.environ.get('DB_NAME', 'shivazen_prod'),
            'USER': os.environ.get('DB_USER', 'postgres'),
            'PASSWORD': os.environ.get('DB_PASSWORD', ''), 
            'HOST': os.environ.get('DB_HOST', 'localhost'),
            'PORT': os.environ.get('DB_PORT', '5432'),
            'OPTIONS': {
                'options': '-c search_path=shivazen_prod,shivazen_app'
            }
        }
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

# CSRF Trusted Origins (adicionar domínio de produção)
CSRF_TRUSTED_ORIGINS = os.environ.get(
    'CSRF_TRUSTED_ORIGINS', 'http://127.0.0.1:8000,http://localhost:8000'
).split(',')

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
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'noreply@shivazen.com.br')

# --- Configurações do JAZZMIN (Mantidas mas não usadas se app removido) ---
# Vou manter as configs do Jazzmin caso o usuário queira reativar no futuro, 
# mas como o app foi removido, elas serão ignoradas pelo Django.

# --- Logging Config ---
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {process:d} {thread:d} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
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
    },
}


JAZZMIN_SETTINGS = {
    "site_title": "Shiva Zen Admin",
    "site_header": "Shiva Zen",
    "site_brand": "Shiva Zen Admin",
    "login_logo": None,
    "site_logo": None,
    "login_logo_dark": None,
    "site_logo_classes": "img-circle",
    "site_icon": None,
    "welcome_sign": "Bem-vindo à Administração da Shiva Zen",
    "copyright": "Shiva Zen © 2024",
    "search_model": ["app_shivazen.Cliente", "app_shivazen.Profissional", "app_shivazen.Atendimento"],

    # Links no topo
    "topmenu_links": [
        {"name": "Dashboard", "url": "shivazen:adminDashboard", "icon": "fas fa-tachometer-alt"},
        {"name": "Voltar ao Site",  "url": "shivazen:inicio", "new_window": True, "icon": "fas fa-home"},
    ],

    "navigation": [
        {"name": "PRINCIPAL", "icon": "fas fa-tachometer-alt"},
        {"name": "Dashboard", "url": "shivazen:adminDashboard", "icon": "fas fa-chart-line"},
        {"name": "Agenda", "icon": "fas fa-calendar-alt", "models": [
            {"model": "app_shivazen.atendimento", "label": "Ver Agendamentos", "icon": "fas fa-calendar-check"},
            {"model": "app_shivazen.disponibilidadeprofissional", "label": "Disponibilidades", "icon": "fas fa-clock"},
            {"model": "app_shivazen.bloqueioagenda", "label": "Bloqueios de Agenda", "icon": "fas fa-calendar-times"},
        ]},
        {"name": "Cadastros", "icon": "fas fa-edit", "models": [
            {"model": "app_shivazen.cliente", "label": "Clientes", "icon": "fas fa-address-book"},
            {"model": "app_shivazen.profissional", "label": "Profissionais", "icon": "fas fa-user-md"},
            {"model": "app_shivazen.procedimento", "label": "Procedimentos", "icon": "fas fa-spa"},
            {"model": "app_shivazen.preco", "label": "Tabela de Preços", "icon": "fas fa-dollar-sign"},
        ]},
        {"name": "Configurações", "icon": "fas fa-cogs", "models": [
            {"name": "Perguntas do Prontuário", "model": "app_shivazen.prontuariopergunta", "icon": "fas fa-question-circle"},
            {"label": "Administração do Site", "icon": "fas fa-tools", "models": [
                {"name": "Usuários (Sistema)", "model": "app_shivazen.usuario", "icon": "fas fa-user-shield"},
                {"name": "Perfis de Acesso", "model": "app_shivazen.perfil", "icon": "fas fa-id-card"},
                {"name": "Funcionalidades", "model": "app_shivazen.funcionalidade", "icon": "fas fa-key"},
                {"name": "Logs de Auditoria", "model": "app_shivazen.logauditoria", "icon": "fas fa-history"},
            ]},
        ]},
    ],
   
    "icons": {
        "auth": "fas fa-users-cog",
        "auth.user": "fas fa-user",
        "auth.Group": "fas fa-users",
        "app_shivazen.Usuario": "fas fa-user-shield",
        "app_shivazen.Perfil": "fas fa-id-card",
        "app_shivazen.Funcionalidade": "fas fa-key",
        "app_shivazen.Cliente": "fas fa-address-book",
        "app_shivazen.Profissional": "fas fa-user-md",
        "app_shivazen.Procedimento": "fas fa-spa",
        "app_shivazen.Preco": "fas fa-dollar-sign",
        "app_shivazen.Atendimento": "fas fa-calendar-check",
        "app_shivazen.DisponibilidadeProfissional": "fas fa-clock",
        "app_shivazen.BloqueioAgenda": "fas fa-calendar-times",
        "app_shivazen.Prontuario": "fas fa-file-medical",
        "app_shivazen.ProntuarioPergunta": "fas fa-question-circle",
        "app_shivazen.ProntuarioResposta": "fas fa-check-circle",
        "app_shivazen.TermoConsentimento": "fas fa-file-signature",
        "app_shivazen.Notificacao": "fas fa-bell",
        "app_shivazen.LogAuditoria": "fas fa-history",
    },
    
    "default_icon_parents": "fas fa-chevron-circle-right",
    "default_icon_children": "fas fa-circle",
    
    "related_modal_active": True,
    "custom_css": None,
    "custom_js": None,
    "use_google_fonts_cdn": True,
    "show_ui_builder": False,
    "changeform_format": "horizontal_tabs",
    "changeform_format_overrides": {
        "app_shivazen.usuario": "collapsible",
        "app_shivazen.atendimento": "collapsible",
    },
    "show_sidebar": True,
    "navigation_expanded": True,
    "hide_apps": [],
    "hide_models": [],
    "order_with_respect_to": ["app_shivazen"],
}

JAZZMIN_UI_TWEAKS = {
    "theme": "lux",
    "dark_theme": "darkly",
    "brand_colour": "#E48A2A",
    "accent": "accent-primary",
    "navbar": "navbar-dark",
    "navbar_fixed": True,
    "sidebar_fixed": True,
    "sidebar": "sidebar-dark-primary",
    "sidebar_nav_child_indent": True,
    "sidebar_nav_compact_style": False,
    "sidebar_nav_legacy_style": False,
    "sidebar_nav_flat_style": False,
    "actions_sticky_top": True,
    "theme_switcher": True,
    "navbar_small_text": False,
    "footer_small_text": False,
    "body_small_text": False,
    "brand_small_text": False,
    "button_classes": {
        "primary": "btn-primary",
        "secondary": "btn-secondary",
        "info": "btn-info",
        "danger": "btn-danger",
        "success": "btn-success"
    }
}

# --- Configurações do Celery e Redis ---
# URL do Redis para filas do Celery
CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL', 'redis://localhost:6379/0')
CELERY_RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0')
CELERY_ACCEPT_CONTENT = ['application/json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'

# --- Configurações de Cache (Redis) ---
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": os.environ.get('REDIS_URL', CELERY_BROKER_URL),
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
        }
    }
}
# Sessões vão usar o cache para ficarem mais rápidas
SESSION_ENGINE = "django.contrib.sessions.backends.cache"
SESSION_CACHE_ALIAS = "default"

CELERY_TIMEZONE = TIME_ZONE

# Configuração agendamento do Celery Beat (tarefas recorrentes)
from celery.schedules import crontab
CELERY_BEAT_SCHEDULE = {
    'lembretes-diarios-08h': {
        'task': 'app_shivazen.tasks.job_enviar_lembrete_dia_seguinte',
        'schedule': crontab(hour=8, minute=0), # Roda todo dia as 08:00
    },
    'envio-pesquisa-nps-diaria': {
        'task': 'app_shivazen.tasks.job_pesquisa_satisfacao_24h',
        'schedule': crontab(hour=10, minute=0), # Roda todo dia as 10:00
    },
}
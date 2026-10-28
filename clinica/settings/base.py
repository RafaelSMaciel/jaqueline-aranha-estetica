"""Settings base — compartilhados entre dev e prod.

Fonte unica da configuracao por ambiente: so os settings leem os.environ (com parse e validacao) e o
codigo le settings.X na hora da chamada — override_settings funciona nos testes e um valor invalido
derruba o boot com o nome da variavel, em vez de virar 500 no meio de um fluxo.
"""
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv


# Base dir aponta para a raiz do projeto (2 niveis acima deste arquivo)
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Carrega .env da raiz do projeto
load_dotenv(BASE_DIR / '.env')


def _env_texto(nome: str, padrao: str = '') -> str:
    return (os.environ.get(nome) or padrao).strip()


def _env_int(nome: str, padrao: int) -> int:
    valor = _env_texto(nome)
    if not valor:
        return padrao
    try:
        return int(valor)
    except ValueError as exc:
        raise ImproperlyConfigured(f'{nome}={valor!r} nao e um numero inteiro.') from exc


def _env_lista(nome: str) -> list[str]:
    return [item.strip() for item in _env_texto(nome).split(',') if item.strip()]


# ─── SENTRY (opcional) ───────────────────────────────────────────────
# Filtros de PII, dado de saude e tokens (erros, transacoes, breadcrumbs): utils/pii.opcoes_sentry
try:
    import sentry_sdk

    _sentry_dsn = os.environ.get('SENTRY_DSN')
    if _sentry_dsn:
        from aranha_estetica.utils.pii import opcoes_sentry
        sentry_sdk.init(**opcoes_sentry(_sentry_dsn))
except ImportError:
    pass
# Politica de privacidade lista o Sentry como operador so quando ele esta ligado
SENTRY_ATIVO = bool(os.environ.get('SENTRY_DSN'))


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
    'django_vite',
    'django_cotton',
]


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
    # autor/IP/origem do request nas GUCs app.* (trilha historico_alteracao, so PG):
    # precisa do request.user (depois do OTP); define no inicio e limpa no fim
    'aranha_estetica.middleware.ContextoBancoMiddleware',
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

# IP do cliente (utils/seguranca.client_ip): header escrito pelo proxy de borda.
# Vazio em dev/testes (REMOTE_ADDR; header seria forjavel sem proxy na frente).
# prod.py liga 'HTTP_X_REAL_IP' (Railway). Rate-limit e axes usam a mesma funcao.
CLIENT_IP_HEADER = os.environ.get('CLIENT_IP_HEADER', '')
RATELIMIT_IP_META_KEY = 'aranha_estetica.utils.seguranca.client_ip'
AXES_CLIENT_IP_CALLABLE = 'aranha_estetica.utils.seguranca.client_ip'

AUTHENTICATION_BACKENDS = [
    'axes.backends.AxesStandaloneBackend',
    'django.contrib.auth.backends.ModelBackend',
]

# django-axes config
AXES_FAILURE_LIMIT = int(os.environ.get('AXES_FAILURE_LIMIT', 5))
AXES_COOLOFF_TIME = float(os.environ.get('AXES_COOLOFF_TIME_HOURS', '1'))
# Bloqueio por IP+usuario e por IP, nunca por usuario sozinho: com o 2FA obrigatorio o ganho contra
# adivinhacao de senha e pequeno, e 5 senhas erradas de qualquer IP trancavam a dona fora do painel
# (DoS barato com o e-mail publico). O freio distribuido por usuario e o @ratelimit curto do login.
AXES_LOCKOUT_PARAMETERS = [['ip_address', 'username'], 'ip_address']
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
            'builtins': [
                'django_cotton.templatetags.cotton', 'aranha_estetica.templatetags.formatos',
                'aranha_estetica.templatetags.site_tags',
            ],
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


def opcoes_postgres(db: dict) -> dict:
    """Conexao Postgres com nome e teto por statement (sem efeito em outro banco).

    - application_name (DB_APPLICATION_NAME, padrao 'shivazen'): quem e quem no pg_stat_activity
      e no log do Postgres (log_line_prefix com %a) — conexao presa, query lenta, web x cron.
    - statement_timeout (DB_STATEMENT_TIMEOUT_MS, padrao 30000; 0 desliga): query fora de controle
      cai sozinha em vez de segurar o worker e os locks — abaixo do --timeout do gunicorn (120 s).
      O migrate_atomico do pre-deploy roda sem teto (SET LOCAL statement_timeout = 0).
    """
    if 'postgresql' not in db.get('ENGINE', ''):
        return db
    opcoes = db.setdefault('OPTIONS', {})
    opcoes.setdefault('application_name', (os.environ.get('DB_APPLICATION_NAME') or 'shivazen')[:63])
    teto = int(os.environ.get('DB_STATEMENT_TIMEOUT_MS') or 30000)
    if teto > 0 and 'statement_timeout' not in opcoes.get('options', ''):
        opcoes['options'] = f"{opcoes.get('options', '')} -c statement_timeout={teto}".strip()
    return db


opcoes_postgres(DATABASES['default'])

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
# 2FA obrigatorio tambem p/ PROFISSIONAL (le prontuario/alertas de saude, art. 11). Sem a
# env: ligado fora de DEBUG (prod), como o do ADMIN. PROFISSIONAL_2FA_OBRIGATORIO=false e
# valvula de emergencia (system check aranha.W012 avisa enquanto estiver desligado).
_2fa_profissional = os.environ.get('PROFISSIONAL_2FA_OBRIGATORIO', '').strip().lower()
PROFISSIONAL_2FA_OBRIGATORIO = (
    _2fa_profissional in ('true', '1', 'yes', 'on') if _2fa_profissional else not DEBUG
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
RATELIMIT_VIEW = 'aranha_estetica.views.publico.site.limite_excedido'

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
# Sem timeout o socket SMTP pendura a thread do gunicorn (o envio roda no request/cron)
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
    # Default do anymail e 30 s: mesmo teto do SMTP (envio no request/cron)
    ANYMAIL['REQUESTS_TIMEOUT'] = EMAIL_TIMEOUT

# SMS (utils/sms): SMS_DEV_LOG_ONLY=true so loga (dev/testes; nunca o codigo).
# Fora disso, sem ZENVIA_API_TOKEN/ZENVIA_FROM o envio falha fechado (False).
SMS_DEV_LOG_ONLY = os.environ.get('SMS_DEV_LOG_ONLY', '').strip().lower() == 'true'

# Ambiente de testes publicado (settings/staging.py liga): travas de envio, noindex, seed --demo.
AMBIENTE_TESTE = False
# So estes e-mails/celulares recebem mensagem no ambiente de testes (utils/seguranca.destino_permitido)
DESTINATARIOS_TESTE = [d.strip() for d in os.environ.get('DESTINATARIOS_TESTE', '').split(',') if d.strip()]

# Password reset: token TTL 1h (default Django = 3 dias). Reduz janela de ataque.
PASSWORD_RESET_TIMEOUT = int(os.environ.get('PASSWORD_RESET_TIMEOUT_SECONDS', 3600))


# ─── INTEGRACOES E CANAIS (lidos so aqui; o codigo le settings.X na hora da chamada) ───
# OTP (models/sistema.CodigoOtp). O CHECK ck_codigo_otp_qtd_tentativas_0_10 do banco limita as
# tentativas a 10: OTP_MAX_TENTATIVAS fora de 1..10 e erro de config (aranha.E004), nao 500 no 11o erro.
OTP_TTL_SEGUNDOS = _env_int('OTP_TTL_SEGUNDOS', 600)
OTP_MAX_TENTATIVAS = _env_int('OTP_MAX_TENTATIVAS', 5)
OTP_REENVIO_MINIMO_SEG = _env_int('OTP_REENVIO_MINIMO_SEG', 60)

# SMS pela Zenvia (utils/sms): sem token + remetente o envio falha fechado. Quotas por hora.
ZENVIA_API_TOKEN = _env_texto('ZENVIA_API_TOKEN')
ZENVIA_FROM = _env_texto('ZENVIA_FROM')
ZENVIA_API_URL = _env_texto('ZENVIA_API_URL', 'https://api.zenvia.com/v2/channels/sms/messages')
SMS_MAX_POR_HORA = _env_int('SMS_MAX_POR_HORA', 3)
SMS_MAX_POR_IP_HORA = _env_int('SMS_MAX_POR_IP_HORA', 10)
SMS_MAX_GLOBAL_HORA = _env_int('SMS_MAX_GLOBAL_HORA', 60)
# Webhook de status da Zenvia (views/infra/webhooks): segredo no header e/ou allowlist de IP/CIDR
ZENVIA_WEBHOOK_SECRET = _env_texto('ZENVIA_WEBHOOK_SECRET')
ZENVIA_ALLOWED_IPS = _env_lista('ZENVIA_ALLOWED_IPS')

# WhatsApp Cloud API da Meta (utils/whatsapp): sem token + phone id o envio falha fechado.
WHATSAPP_TOKEN = _env_texto('WHATSAPP_TOKEN')
# WHATSAPP_PHONE_NUMBER_ID: alias aceito (nome do .env.example antigo)
WHATSAPP_PHONE_ID = _env_texto('WHATSAPP_PHONE_ID') or _env_texto('WHATSAPP_PHONE_NUMBER_ID')
WHATSAPP_API_URL = _env_texto('WHATSAPP_API_URL')
# Versoes antigas da Graph API sao desligadas pela Meta ~2 anos apos o lancamento
WHATSAPP_API_VERSION = _env_texto('WHATSAPP_API_VERSION', 'v23.0')
# Nomes dos templates aprovados na Meta
WHATSAPP_TEMPLATE_D1 = _env_texto('WHATSAPP_TEMPLATE_D1', 'confirmacao_d1')
WHATSAPP_TEMPLATE_NPS = _env_texto('WHATSAPP_TEMPLATE_NPS', 'nps_pos_atendimento')
WHATSAPP_TEMPLATE_LISTA_ESPERA = _env_texto('WHATSAPP_TEMPLATE_LISTA_ESPERA', 'lista_espera_vaga')
# Webhook (views/infra/webhooks): APP_SECRET assina o corpo (X-Hub-Signature-256); VERIFY_TOKEN e o
# segredo do handshake GET. Definido, o VERIFY_TOKEN precisa de 32+ caracteres (aranha.E003).
WHATSAPP_APP_SECRET = _env_texto('WHATSAPP_APP_SECRET')
WHATSAPP_VERIFY_TOKEN = _env_texto('WHATSAPP_VERIFY_TOKEN')

# Captcha Cloudflare Turnstile antes do envio de SMS (utils/captcha)
TURNSTILE_SITE_KEY = _env_texto('TURNSTILE_SITE_KEY')
TURNSTILE_SECRET_KEY = _env_texto('TURNSTILE_SECRET_KEY')

# Web Push VAPID (services/push). Contato do claim: WEBPUSH_VAPID_CLAIMS_EMAIL > ADMIN_EMAIL >
# CLINIC_EMAIL da marca; sem nenhum o push nao sai (log webpush_sem_contato).
WEBPUSH_VAPID_PUBLIC_KEY = _env_texto('WEBPUSH_VAPID_PUBLIC_KEY')
WEBPUSH_VAPID_PRIVATE_KEY = _env_texto('WEBPUSH_VAPID_PRIVATE_KEY')
WEBPUSH_VAPID_CLAIMS_EMAIL = _env_texto('WEBPUSH_VAPID_CLAIMS_EMAIL')

# Cron HTTP (views/infra/cron): header X-Cron-Token. Definido, precisa de 32+ caracteres (aranha.E003).
CRON_TOKEN = _env_texto('CRON_TOKEN')

# E-mail do administrador: conta criada no pre-deploy (bootstrap_admin) e contato padrao da clinica
ADMIN_EMAIL = _env_texto('ADMIN_EMAIL')

# Marca/contatos por env: fallback da tela Branding (utils/branding.get_branding aplica os defaults).
# Mesmas chaves de utils/branding.DEFAULTS (tests/infra/test_deploy confere).
MARCA_ENV = {
    chave: _env_texto(chave)
    for chave in (
        'CLINIC_NAME', 'CLINIC_SUBTITLE', 'CLINIC_EMAIL', 'CLINIC_PHONE', 'CLINIC_ADDRESS', 'CLINIC_HOURS',
        'WHATSAPP_NUMERO', 'INSTAGRAM_URL', 'THEME_COLOR',
        'CLINIC_RAZAO_SOCIAL', 'CLINIC_CNPJ', 'ENCARREGADO_NOME', 'ENCARREGADO_EMAIL',
    )
}


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
            '()': 'pythonjsonlogger.json.JsonFormatter',
            'format': '%(asctime)s %(name)s %(levelname)s %(message)s',
        },
    },
    # Token de link magico, e-mail, CPF e telefone redigidos na mensagem, no traceback e nos extras
    # (o django.request loga o caminho /confirmar/<token>/ em todo 4xx/5xx)
    'filters': {
        'redigir': {'()': 'clinica.logging_filtros.RedigirLog'},
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': _LOG_FORMATTER,
            'filters': ['redigir'],
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


# ─── CACHE ───────────────────────────────────────────────────────────
# REDIS_URL = cache/sessao compartilhados entre workers do gunicorn. Sem fila de tarefas: os
# jobs sao funcoes chamadas pelo cron HTTP (views/infra/cron.py) e o e-mail tem fila no banco
# (services/comunicacao).
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
    # LocMemCache e por-processo: ok com 1 worker gunicorn (threads compartilham).
    # Com mais workers, rate-limit/axes/lock de slot/quota de SMS deixam de ser
    # compartilhados — ai sim precisa de REDIS_URL.
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


# ─── CIFRAGEM EM REPOUSO (utils/cifragem; prontuario, fichas, token OAuth) ───
# Chaves Fernet (base64 de 32 bytes) separadas por virgula: a 1a cifra, todas decifram (rotacao:
# nova na frente + `manage.py rotacionar_chave_cifragem` + tirar a antiga). Gerar:
# `manage.py gerar_chave_cifragem`. Perder a chave = perder o prontuario: copia offline com a dona.
# Sem a env: dev/testes derivam da SECRET_KEY; prod (CIFRAGEM_EXIGE_CHAVE) recusa (aranha.E001).
CHAVES_CIFRAGEM = [c.strip() for c in os.environ.get('CHAVES_CIFRAGEM', '').split(',') if c.strip()]
CIFRAGEM_EXIGE_CHAVE = False
# HMAC dos codigos OTP com chave propria (vazar/rotacionar a SECRET_KEY nao mexe nos OTP e
# vice-versa). Vazia: usa a SECRET_KEY, como antes.
OTP_HMAC_KEY = os.environ.get('OTP_HMAC_KEY', '').strip()


# Monitor dos jobs do cron (healthchecks.io: https://hc-ping.com/<ping key>): views/infra/cron avisa
# <url>/<job> a cada execucao ok e <url>/<job>/fail na falha. Vazio = sem monitor.
CRON_MONITOR_URL = os.environ.get('CRON_MONITOR_URL', '').strip()


# ─── RETENCAO (job_housekeeping) ─────────────────────────────────────
# docs/specs/regras-negocio-registry.md: otp 24h · notif 12m · auditoria 5a
RETENCAO_OTP_HORAS = int(os.environ.get('RETENCAO_OTP_HORAS', '24'))
RETENCAO_NOTIFICACAO_DIAS = int(os.environ.get('RETENCAO_NOTIFICACAO_DIAS', '365'))
RETENCAO_LOG_AUDITORIA_DIAS = int(os.environ.get('RETENCAO_LOG_AUDITORIA_DIAS', str(365 * 5)))
RETENCAO_AXES_LOG_DIAS = int(os.environ.get('RETENCAO_AXES_LOG_DIAS', '90'))
# link_acesso vencido/revogado ha mais de N dias e apagado (sp_purgar_dados_expirados);
# historico_alteracao segue RETENCAO_LOG_AUDITORIA_DIAS (prontuario: 20 anos, nao purga)
RETENCAO_LINK_ACESSO_DIAS = int(os.environ.get('RETENCAO_LINK_ACESSO_DIAS', '90'))

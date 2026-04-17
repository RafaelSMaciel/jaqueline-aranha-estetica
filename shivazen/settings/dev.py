"""Settings de desenvolvimento local."""
from .base import *  # noqa: F401,F403

DEBUG = True

# Em dev local, liberar toasts e depuracao
INTERNAL_IPS = ['127.0.0.1', 'localhost']

# Em dev nao forcar HTTPS nem cookies seguros
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

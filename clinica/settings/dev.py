"""Settings de desenvolvimento local."""
# ruff: noqa: E402, F405  (star import + conditional imports intencionais)
from .base import *  # noqa: F401,F403

DEBUG = True

# Em dev local, liberar toasts e depuracao
INTERNAL_IPS = ['127.0.0.1', 'localhost']

# Em dev nao forcar HTTPS nem cookies seguros
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

# django-debug-toolbar (so no dev interativo; a suite usa settings/test.py)
try:
    import debug_toolbar  # noqa: F401
    INSTALLED_APPS += ['debug_toolbar']
    # Insert after GZipMiddleware to satisfy debug_toolbar.W003
    _gz = 'django.middleware.gzip.GZipMiddleware'
    _idx = MIDDLEWARE.index(_gz) + 1 if _gz in MIDDLEWARE else 0
    MIDDLEWARE = MIDDLEWARE[:_idx] + ['debug_toolbar.middleware.DebugToolbarMiddleware'] + MIDDLEWARE[_idx:]
    DEBUG_TOOLBAR_CONFIG = {'SHOW_TOOLBAR_CALLBACK': lambda r: DEBUG}
except ImportError:
    pass

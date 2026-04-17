"""Settings de desenvolvimento local."""
from .base import *  # noqa: F401,F403

DEBUG = True

# Em dev local, liberar toasts e depuracao
INTERNAL_IPS = ['127.0.0.1', 'localhost']

# Em dev nao forcar HTTPS nem cookies seguros
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

# django-debug-toolbar (so em dev, opcional, nunca em testes)
import sys as _sys
if 'test' not in _sys.argv:
    try:
        import debug_toolbar  # noqa: F401
        INSTALLED_APPS += ['debug_toolbar']
        MIDDLEWARE = ['debug_toolbar.middleware.DebugToolbarMiddleware'] + MIDDLEWARE
        DEBUG_TOOLBAR_CONFIG = {'SHOW_TOOLBAR_CALLBACK': lambda r: DEBUG}
    except ImportError:
        pass

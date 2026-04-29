# clinica/urls.py
from django.conf import settings
from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.urls import path, include
from django.views.generic import TemplateView

from two_factor.urls import urlpatterns as tf_urlpatterns

from aranha_estetica.sitemaps import StaticViewSitemap, ProcedimentoSitemap

# admin.site.__class__ -> AdminSiteOTPRequired aplicado em aranha_estetica.apps.ready()

sitemaps = {
    'static': StaticViewSitemap,
    'procedimentos': ProcedimentoSitemap,
}

urlpatterns = [
    path('django-admin-sv/', admin.site.urls),
    # 2FA: setup TOTP, login com OTP, backup tokens
    path('', include(tf_urlpatterns)),
    path('sitemap.xml', sitemap, {'sitemaps': sitemaps}, name='django.contrib.sitemaps.views.sitemap'),
    path('robots.txt', TemplateView.as_view(template_name='robots.txt', content_type='text/plain')),
    path('', include('aranha_estetica.urls')),
]

if settings.DEBUG and 'debug_toolbar' in getattr(settings, 'INSTALLED_APPS', []):
    try:
        import debug_toolbar
        urlpatterns = [path('__debug__/', include(debug_toolbar.urls))] + urlpatterns
    except ImportError:
        pass

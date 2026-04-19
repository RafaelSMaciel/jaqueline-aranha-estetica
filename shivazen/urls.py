# shivazen/urls.py
from django.conf import settings
from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.urls import path, include
from django.views.generic import TemplateView

from app_shivazen.sitemaps import StaticViewSitemap, ProcedimentoSitemap

sitemaps = {
    'static': StaticViewSitemap,
    'procedimentos': ProcedimentoSitemap,
}

urlpatterns = [
    path('django-admin-sv/', admin.site.urls),
    path('sitemap.xml', sitemap, {'sitemaps': sitemaps}, name='django.contrib.sitemaps.views.sitemap'),
    path('robots.txt', TemplateView.as_view(template_name='robots.txt', content_type='text/plain')),
    path('', include('app_shivazen.urls')),
]

if settings.DEBUG and 'debug_toolbar' in getattr(settings, 'INSTALLED_APPS', []):
    try:
        import debug_toolbar
        urlpatterns = [path('__debug__/', include(debug_toolbar.urls))] + urlpatterns
    except ImportError:
        pass
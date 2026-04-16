# shivazen/urls.py
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
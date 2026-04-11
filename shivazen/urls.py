# shivazen/urls.py
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('django-admin-sv/', admin.site.urls),
    path('', include('app_shivazen.urls')),
]
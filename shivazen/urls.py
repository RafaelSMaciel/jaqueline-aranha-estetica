# shivazen/urls.py
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('', include('app_shivazen.urls')),
]
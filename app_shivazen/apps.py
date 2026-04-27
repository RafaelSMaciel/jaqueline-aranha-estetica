from django.apps import AppConfig


class AppShivazenConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'app_shivazen'

    def ready(self):
        import app_shivazen.signals
        # Forca 2FA no Django Admin (substitui classe do site)
        from django.contrib import admin
        from two_factor.admin import AdminSiteOTPRequired
        admin.site.__class__ = AdminSiteOTPRequired

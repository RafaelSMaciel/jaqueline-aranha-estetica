from django.apps import AppConfig


class AranhaEsteticaConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'aranha_estetica'

    def ready(self):
        import aranha_estetica.signals
        # Forca 2FA no Django Admin (substitui classe do site)
        from django.contrib import admin
        from two_factor.admin import AdminSiteOTPRequired
        admin.site.__class__ = AdminSiteOTPRequired

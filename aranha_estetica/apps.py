from django.apps import AppConfig


class AranhaEsteticaConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'aranha_estetica'
    # PRESERVA label antigo p/ migrar sem precisar tocar django_migrations,
    # content_type ou auth_permission. DB tables ja sao sem prefixo (atendimento,
    # cliente, etc.), entao label so afeta tabelas de meta-Django.
    label = 'app_shivazen'

    def ready(self):
        import aranha_estetica.signals
        # Forca 2FA no Django Admin (substitui classe do site)
        from django.contrib import admin
        from two_factor.admin import AdminSiteOTPRequired
        admin.site.__class__ = AdminSiteOTPRequired

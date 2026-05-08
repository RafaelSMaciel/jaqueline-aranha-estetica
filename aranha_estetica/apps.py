from django.apps import AppConfig


class AranhaEsteticaConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'aranha_estetica'

    def ready(self):
        # Forca 2FA no Django Admin (substitui classe do site)
        from django.contrib import admin
        from two_factor.admin import AdminSiteOTPRequired
        admin.site.__class__ = AdminSiteOTPRequired

        # Registra signals legados
        from . import signals  # noqa: F401

        # Registra handlers do EventBus de dominio
        from .domain import handlers  # noqa: F401

        # Registra services com handlers EventBus (F-RET, F-CSB, comissao, lista espera)
        from .services import retorno_service  # noqa: F401
        from .services import fidelidade_service  # noqa: F401
        from .services import comissao_service  # noqa: F401
        from .services import lista_espera_service  # noqa: F401

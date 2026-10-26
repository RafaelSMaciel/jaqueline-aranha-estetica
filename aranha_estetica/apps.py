from django.apps import AppConfig


class AranhaEsteticaConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'aranha_estetica'

    def ready(self):
        # Collation pt_br no SQLite (dev/testes): o estado tem db_collation='pt_br' (0054)
        from django.db import connections
        from django.db.backends.signals import connection_created

        from .db.collation import registrar_collation_sqlite
        connection_created.connect(registrar_collation_sqlite, dispatch_uid='aranha_estetica_pt_br_sqlite')
        for conexao in connections.all(initialized_only=True):  # aberta antes do ready()
            if conexao.connection is not None:
                registrar_collation_sqlite(None, conexao)

        # Trilha historico_alteracao (so PG): conexao nova nasce sem autor/IP/origem
        # (GUCs app.*, utils/banco) — o middleware/cron/Celery definem e limpam.
        from .utils.banco import limpar_contexto_conexao_nova
        connection_created.connect(limpar_contexto_conexao_nova, dispatch_uid='aranha_estetica_contexto_banco')
        from . import sinais_celery  # noqa: F401  task_prerun/task_postrun (worker real)

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

        # System checks de config de producao (SITE_URL, SMS, e-mail, WhatsApp, cron)
        from . import checks  # noqa: F401

from django.apps import AppConfig


class AppShivazenConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'app_shivazen'

    def ready(self):
        import app_shivazen.signals

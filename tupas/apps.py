from django.apps import AppConfig


class TupasConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'tupas'

    def ready(self):
        from . import signals  # noqa: F401 — connect ActivityLog auth signals

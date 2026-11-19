from django.apps import AppConfig


class TupasConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'tupas'

    def ready(self):
        from . import signals  # noqa: F401 — connect ActivityLog auth signals
        self._ensure_superuser()

    def _ensure_superuser(self):
        import os
        username = os.environ.get('DJANGO_SUPERUSER_USERNAME', '').strip()
        password = os.environ.get('DJANGO_SUPERUSER_PASSWORD', '').strip()
        email = os.environ.get('DJANGO_SUPERUSER_EMAIL', '').strip()
        if not (username and password):
            return
        try:
            from django.contrib.auth import get_user_model
            User = get_user_model()
            if User.objects.filter(username=username).exists():
                return
            User.objects.create_superuser(username, email, password)
        except Exception:
            # DB may not be ready yet (e.g. during build/migrations); ignore.
            pass

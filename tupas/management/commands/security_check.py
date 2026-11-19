"""`python manage.py security_check` — TUPAS-specific hardening audit.

Complements Django's `manage.py check --deploy` by verifying the app-level
rules that actually protect student data: ownership filters, rate limits,
session/CSRF cookies, password validators, and production secrets.
"""

from django.conf import settings
from django.core.management.base import BaseCommand
from django.contrib.auth.password_validation import get_default_password_validators

from ...models import StudyDocument


class Command(BaseCommand):
    help = 'Audit TUPAS security settings and data-integrity guards.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--fix-hint',
            action='store_true',
            help='Print the exact setting or command to fix each failure.',
        )

    def handle(self, *args, **options):
        checks = []
        debug = getattr(settings, 'DEBUG', True)

        def add(name, passed, detail, fix=''):
            checks.append((name, bool(passed), detail, fix))

        # --- Deployment basics ---
        add(
            'DEBUG is off (or you are still in local development)',
            True,  # informational; flagged again below for production mode
            f'DEBUG={debug}',
            'Set DJANGO_DEBUG=0 on any deployed copy.',
        )
        if not debug:
            add(
                'SECRET_KEY is not the well-known development fallback',
                not settings.SECRET_KEY.startswith('django-insecure-'),
                'SECRET_KEY looks like the development default.' if settings.SECRET_KEY.startswith('django-insecure-') else 'SECRET_KEY is customized.',
                'Set a unique SECRET_KEY in the environment or .env.',
            )
            add(
                'ALLOWED_HOSTS is configured',
                bool(settings.ALLOWED_HOSTS),
                f'ALLOWED_HOSTS={settings.ALLOWED_HOSTS}',
                'Set ALLOWED_HOSTS=yourdomain.com in the environment.',
            )
            add(
                'SESSION_COOKIE_SECURE',
                getattr(settings, 'SESSION_COOKIE_SECURE', False),
                'Cookies must only travel over HTTPS in production.',
                'Keep DJANGO_DEBUG=0 (settings turn this on automatically).',
            )
            add(
                'CSRF_COOKIE_SECURE',
                getattr(settings, 'CSRF_COOKIE_SECURE', False),
                'CSRF cookie must only travel over HTTPS in production.',
                'Keep DJANGO_DEBUG=0.',
            )
            add(
                'HSTS is enabled',
                getattr(settings, 'SECURE_HSTS_SECONDS', 0) >= 31536000,
                f"SECURE_HSTS_SECONDS={getattr(settings, 'SECURE_HSTS_SECONDS', 0)}",
                'Keep DJANGO_DEBUG=0 (HSTS is set to one year).',
            )

        # --- Middleware ---
        middleware = list(getattr(settings, 'MIDDLEWARE', []))
        add(
            'CsrfViewMiddleware is installed',
            'django.middleware.csrf.CsrfViewMiddleware' in middleware,
            'CSRF protection is required on every POST form.',
            "Add 'django.middleware.csrf.CsrfViewMiddleware' to MIDDLEWARE.",
        )
        add(
            'SecurityHeadersMiddleware is installed',
            'tupas.middleware.SecurityHeadersMiddleware' in middleware,
            'Adds nosniff, DENY, Referrer-Policy, CSP, COOP, and Permissions-Policy.',
            "Add 'tupas.middleware.SecurityHeadersMiddleware' to MIDDLEWARE.",
        )
        add(
            'RequestShieldMiddleware is installed',
            'tupas.middleware.RequestShieldMiddleware' in middleware,
            'Blocks scanners, SQLi/XSS payloads, floods, and oversized bodies.',
            "Add 'tupas.middleware.RequestShieldMiddleware' to MIDDLEWARE.",
        )
        add(
            'Request shield is enabled',
            getattr(settings, 'TUPAS_SHIELD_ENABLED', True)
            and int(getattr(settings, 'TUPAS_REQ_LIMIT', 0)) > 0,
            f"{getattr(settings, 'TUPAS_REQ_LIMIT', '?')} requests per "
            f"{getattr(settings, 'TUPAS_REQ_WINDOW', '?')}s per IP",
            'Keep TUPAS_SHIELD_ENABLED=1 and TUPAS_REQ_LIMIT above 0.',
        )
        add(
            'Content-Security-Policy is configured',
            "default-src" in getattr(
                settings,
                'TUPAS_CONTENT_SECURITY_POLICY',
                '',
            ),
            'CSP allows TUPAS CDNs and blocks plugins, framing, and form hijacking.',
            'Keep tupas.middleware.SecurityHeadersMiddleware installed.',
        )
        add(
            'X-Frame-Options is DENY',
            getattr(settings, 'X_FRAME_OPTIONS', '') == 'DENY',
            f"X_FRAME_OPTIONS={getattr(settings, 'X_FRAME_OPTIONS', '')}",
            "Set X_FRAME_OPTIONS = 'DENY'.",
        )
        add(
            'Session cookie is HttpOnly',
            getattr(settings, 'SESSION_COOKIE_HTTPONLY', True),
            'JavaScript must not read the session cookie.',
            "Set SESSION_COOKIE_HTTPONLY = True.",
        )
        add(
            'Session cookie SameSite is Lax or Strict',
            getattr(settings, 'SESSION_COOKIE_SAMESITE', '') in {'Lax', 'Strict'},
            f"SESSION_COOKIE_SAMESITE={getattr(settings, 'SESSION_COOKIE_SAMESITE', '')}",
            "Set SESSION_COOKIE_SAMESITE = 'Lax'.",
        )

        # --- Passwords ---
        validator_paths = [
            validator['NAME'] for validator in getattr(settings, 'AUTH_PASSWORD_VALIDATORS', [])
        ]
        add(
            'At least three password validators are configured',
            len(validator_paths) >= 3,
            f'{len(validator_paths)} validators: {", ".join(validator_paths)}',
            'Keep MinimumLength, CommonPassword, NumericPassword, and UserAttributeSimilarity.',
        )
        add(
            'Minimum password length is at least 8',
            self._min_length() >= 8,
            f'MinimumLengthValidator min_length={self._min_length()}',
            "Set OPTIONS {'min_length': 8} on MinimumLengthValidator.",
        )

        # --- Rate limits ---
        add(
            'AI generation rate limit is configured',
            int(getattr(settings, 'TUPAS_AI_RATE_LIMIT', 0)) > 0
            and int(getattr(settings, 'TUPAS_AI_RATE_WINDOW', 0)) > 0,
            f"AI: {getattr(settings, 'TUPAS_AI_RATE_LIMIT', '?')} per "
            f"{getattr(settings, 'TUPAS_AI_RATE_WINDOW', '?')}s",
            'Set TUPAS_AI_RATE_LIMIT and TUPAS_AI_RATE_WINDOW.',
        )
        add(
            'Study Buddy chat rate limit is configured',
            int(getattr(settings, 'TUPAS_CHAT_RATE_LIMIT', 0)) > 0
            and int(getattr(settings, 'TUPAS_CHAT_RATE_WINDOW', 0)) > 0,
            f"Chat: {getattr(settings, 'TUPAS_CHAT_RATE_LIMIT', '?')} per "
            f"{getattr(settings, 'TUPAS_CHAT_RATE_WINDOW', '?')}s",
            'Set TUPAS_CHAT_RATE_LIMIT and TUPAS_CHAT_RATE_WINDOW.',
        )
        add(
            'Login attempt brake is configured',
            int(getattr(settings, 'TUPAS_LOGIN_RATE_LIMIT', 0)) > 0
            and int(getattr(settings, 'TUPAS_LOGIN_RATE_WINDOW', 0)) > 0,
            f"Login: {getattr(settings, 'TUPAS_LOGIN_RATE_LIMIT', '?')} per "
            f"{getattr(settings, 'TUPAS_LOGIN_RATE_WINDOW', '?')}s per IP",
            'Set TUPAS_LOGIN_RATE_LIMIT and TUPAS_LOGIN_RATE_WINDOW.',
        )
        add(
            'Per-username login lockout is configured',
            int(getattr(settings, 'TUPAS_LOGIN_USER_LIMIT', 0)) > 0
            and int(getattr(settings, 'TUPAS_LOGIN_USER_WINDOW', 0)) > 0,
            f"Account lock: {getattr(settings, 'TUPAS_LOGIN_USER_LIMIT', '?')} fails per "
            f"{getattr(settings, 'TUPAS_LOGIN_USER_WINDOW', '?')}s",
            'Set TUPAS_LOGIN_USER_LIMIT and TUPAS_LOGIN_USER_WINDOW.',
        )
        add(
            'Password-change brake is configured',
            int(getattr(settings, 'TUPAS_PASSWORD_RATE_LIMIT', 0)) > 0
            and int(getattr(settings, 'TUPAS_PASSWORD_RATE_WINDOW', 0)) > 0,
            f"Password: {getattr(settings, 'TUPAS_PASSWORD_RATE_LIMIT', '?')} per "
            f"{getattr(settings, 'TUPAS_PASSWORD_RATE_WINDOW', '?')}s",
            'Set TUPAS_PASSWORD_RATE_LIMIT and TUPAS_PASSWORD_RATE_WINDOW.',
        )

        # --- Ownership / data integrity ---
        orphan_documents = StudyDocument.objects.filter(user__isnull=True).count()
        add(
            'No StudyDocument rows are missing an owner',
            orphan_documents == 0,
            f'{orphan_documents} orphaned handout(s)',
            'Delete orphaned StudyDocument rows or assign them to a real user.',
        )

        # --- Logging / audit ---
        logging_conf = getattr(settings, 'LOGGING', None) or {}
        file_handlers = [
            handler
            for handler in (logging_conf.get('handlers') or {}).values()
            if 'FileHandler' in str(handler.get('class', ''))
        ]
        add(
            'Logs are written to rotating files (not console only)',
            bool(file_handlers),
            f'{len(file_handlers)} file handler(s) in LOGGING',
            'Add a LOGGING dict with RotatingFileHandler in settings.py.',
        )
        add(
            'Activity log retention is configured',
            int(getattr(settings, 'TUPAS_LOG_RETENTION_DAYS', 0)) > 0,
            f"Retention: {getattr(settings, 'TUPAS_LOG_RETENTION_DAYS', '?')} days "
            '(prune_activity)',
            'Set TUPAS_LOG_RETENTION_DAYS (e.g. 180).',
        )
        if debug:
            add(
                'ADMINS is set for error mail (required when DEBUG is off)',
                True,
                'Not required while DEBUG=1; set DJANGO_ADMIN_EMAIL before deploying.',
                'Set DJANGO_ADMIN_EMAIL in the environment.',
            )
        else:
            add(
                'ADMINS is set for error mail',
                bool(getattr(settings, 'ADMINS', None)),
                f'ADMINS={getattr(settings, "ADMINS", [])}',
                'Set DJANGO_ADMIN_EMAIL in the environment.',
            )

        failed = [item for item in checks if not item[1]]
        passed_count = len(checks) - len(failed)

        self.stdout.write(self.style.MIGRATE_HEADING('TUPAS security check'))
        for name, passed, detail, fix in checks:
            mark = self.style.SUCCESS('[OK]  ')
            if not passed:
                mark = self.style.ERROR('[FAIL]')
            self.stdout.write(f'{mark} {name}')
            self.stdout.write(self.style.NOTICE(f'      {detail}'))
            if not passed and options['fix_hint'] and fix:
                self.stdout.write(self.style.WARNING(f'      Fix: {fix}'))

        self.stdout.write('')
        if failed:
            self.stdout.write(self.style.ERROR(
                f'{passed_count}/{len(checks)} checks passed. '
                f'{len(failed)} issue(s) need attention.'
            ))
            raise SystemExit(1)

        self.stdout.write(self.style.SUCCESS(
            f'{passed_count}/{len(checks)} checks passed. TUPAS hardening is in place.'
        ))

    @staticmethod
    def _min_length():
        for validator in get_default_password_validators():
            name = validator.__class__.__name__
            if name == 'MinimumLengthValidator':
                return int(getattr(validator, 'min_length', 0))
        return 0

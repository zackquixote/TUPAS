"""Django middleware for TUPAS.

``SecurityHeadersMiddleware`` adds hardening headers on every response.
``RequestShieldMiddleware`` (implemented in ``tupas.services.shield``) is
re-exported here so ``MIDDLEWARE`` can point at ``tupas.middleware``.
``RequestIDMiddleware``, ``AccessLogMiddleware`` and ``AuditDeniedMiddleware``
make the runtime log and the ActivityLog trail joinable.
"""

import logging
import time
import uuid

from django.conf import settings

from .models import ActivityLog
from .services.activity import record_activity
from .services.shield import (
    RequestShieldMiddleware,
)

logger_access = logging.getLogger('tupas.access')


class SecurityHeadersMiddleware:
    """Hardening headers on every response, plus no-store for signed-in HTML."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        response.headers.setdefault('X-Content-Type-Options', 'nosniff')
        response.headers.setdefault('X-Frame-Options', 'DENY')
        response.headers.setdefault('Referrer-Policy', 'same-origin')
        response.headers.setdefault(
            'Permissions-Policy',
            'camera=(), microphone=(), geolocation=(), interest-cohort=()',
        )
        response.headers.setdefault(
            'Content-Security-Policy',
            getattr(
                settings,
                'TUPAS_CONTENT_SECURITY_POLICY',
                "default-src 'self'; frame-ancestors 'none'; object-src 'none';",
            ),
        )
        response.headers.setdefault('Cross-Origin-Opener-Policy', 'same-origin')
        response.headers.setdefault('Cross-Origin-Resource-Policy', 'same-origin')

        # Never let a shared/proxy/browser cache keep a signed-in page.
        if getattr(request, 'user', None) is not None and request.user.is_authenticated:
            if request.path.startswith('/static/') or request.path.startswith('/media/'):
                pass
            else:
                response.headers['Cache-Control'] = (
                    'no-store, no-cache, must-revalidate, private'
                )
                existing_vary = response.headers.get('Vary', '')
                if 'Cookie' not in existing_vary:
                    response.headers['Vary'] = (
                        f'{existing_vary}, Cookie' if existing_vary else 'Cookie'
                    )

        if not getattr(settings, 'DEBUG', True):
            response.headers.setdefault(
                'Strict-Transport-Security',
                f'max-age={getattr(settings, "SECURE_HSTS_SECONDS", 31536000)}; includeSubDomains',
            )
        return response


class RequestIDMiddleware:
    """Give every request a short id, echo it, and put it on the request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        rid = (request.headers.get('X-Request-ID') or '').strip()
        if not rid or len(rid) > 64:
            rid = uuid.uuid4().hex
        request.request_id = rid[:64]
        response = self.get_response(request)
        response['X-Request-ID'] = request.request_id
        return response


class AccessLogMiddleware:
    """One line per page/API request, with user and request id.

    Static files, the service worker, offline fallback, and health checks are
    skipped so the access log stays readable. This is the join key to
    ``ActivityLog.request_id``.
    """

    SKIP_PREFIXES = (
        '/static/',
        '/media/',
        '/sw.js',
        '/health',
        '/favicon',
        '/offline/',
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        started = time.monotonic()
        response = self.get_response(request)
        path = request.path or ''
        if any(path.startswith(prefix) for prefix in self.SKIP_PREFIXES):
            return response
        duration_ms = int((time.monotonic() - started) * 1000)
        user = getattr(request, 'user', None)
        username = getattr(user, 'username', None) or 'anonymous'
        logger_access.info(
            '%s %s %s %sms user=%s rid=%s ip=%s',
            request.method,
            path,
            response.status_code,
            duration_ms,
            username,
            getattr(request, 'request_id', '-') or '-',
            request.META.get('REMOTE_ADDR', '-') or '-',
        )
        return response


class AuditDeniedMiddleware:
    """Record 403s (CSRF, PermissionDenied) that are not shield blocks.

    Must sit outside ``CsrfViewMiddleware`` and ``RequestShieldMiddleware``
    so it sees those responses. Shield already writes its own rows.
    """

    SKIP_PREFIXES = (
        '/static/',
        '/media/',
        '/sw.js',
        '/health',
        '/favicon',
        '/offline/',
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        path = request.path or ''
        if response.status_code != 403:
            return response
        if any(path.startswith(prefix) for prefix in self.SKIP_PREFIXES):
            return response
        if response.headers.get('X-TUPAS-Shield'):
            return response
        user = getattr(request, 'user', None)
        record_activity(
            user if getattr(user, 'is_authenticated', False) else None,
            ActivityLog.Action.DENIED,
            f'{request.method} {path}'.strip(),
            request,
        )
        return response

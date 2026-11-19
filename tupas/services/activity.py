"""Write one ActivityLog row. Never raises to the caller."""

import logging

from django.conf import settings

from ..models import ActivityLog

logger = logging.getLogger('tupas.audit')


def resolve_client_ip(request):
    """Client IP for audit/rate limits.

    ``REMOTE_ADDR`` is the default. ``X-Forwarded-For`` is honoured only when
    the immediate peer is listed in ``TUPAS_TRUSTED_PROXIES`` — otherwise the
    header is attacker-controlled.
    """
    if request is None:
        return None
    remote = request.META.get('REMOTE_ADDR') or None
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR')
    if not forwarded or not remote:
        return remote
    trusted = set(getattr(settings, 'TUPAS_TRUSTED_PROXIES', ()) or ())
    if remote not in trusted:
        return remote
    for hop in forwarded.split(','):
        ip = hop.strip()
        if ip and ip not in trusted:
            return ip
    return remote


def mask_identifier(value):
    """Human-readable mask for a login identifier (often an email)."""
    value = (value or '').strip()
    if not value:
        return ''
    if '@' in value:
        local, _, domain = value.partition('@')
        if len(local) <= 2:
            shown = (local[:1] or '*') + '*'
        else:
            shown = local[:2] + '*' * (len(local) - 2)
        return f'{shown}@{domain}'
    if len(value) <= 2:
        return (value[:1] or '*') + '*'
    return value[0] + '*' * (len(value) - 2) + value[-1]


def _request_id(request):
    if request is None:
        return ''
    rid = getattr(request, 'request_id', '') or request.headers.get('X-Request-ID', '')
    return str(rid)[:64]


def record_activity(user, action, detail='', request=None):
    """Append an activity row; failures are logged, never raised."""
    try:
        actor = None
        if user is not None and getattr(user, 'is_authenticated', False):
            actor = user
        username = actor.username if actor is not None else ''
        user_pk = actor.pk if actor is not None else None

        ip = resolve_client_ip(request)
        agent = ''
        if request is not None:
            agent = (request.META.get('HTTP_USER_AGENT') or '')[:255]

        return ActivityLog.objects.create(
            user=actor,
            username_snapshot=username,
            user_id_snapshot=user_pk,
            request_id=_request_id(request),
            action=action,
            detail=(detail or '')[:255],
            ip_address=ip,
            user_agent=agent,
        )
    except Exception:
        logger.warning(
            'ActivityLog write failed action=%s detail=%.80s',
            action, detail, exc_info=True,
        )
        return None

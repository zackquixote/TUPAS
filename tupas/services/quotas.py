"""Per-user rate limiting backed by Django's cache."""

from django.conf import settings
from django.core.cache import cache


def _positive_int(value, default):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, parsed)


def consume_rate(key, limit, window):
    """Consume one slot and return ``(allowed, remaining)``."""
    limit = max(1, int(limit))
    window = max(1, int(window))
    if cache.add(key, 1, timeout=window):
        return True, limit - 1
    try:
        used = cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=window)
        return True, limit - 1
    return used <= limit, max(0, limit - used)


def ai_generation_quota(user):
    """Consume one AI generation slot and report whether the user may proceed."""
    limit = _positive_int(
        getattr(settings, 'TUPAS_AI_RATE_LIMIT', 10), 10,
    )
    window = _positive_int(
        getattr(settings, 'TUPAS_AI_RATE_WINDOW', 900), 900,
    )
    return consume_rate(f'tupas:ai-generation:{user.pk}', limit, window)


def ai_quota_message():
    limit = _positive_int(
        getattr(settings, 'TUPAS_AI_RATE_LIMIT', 10), 10,
    )
    window = _positive_int(
        getattr(settings, 'TUPAS_AI_RATE_WINDOW', 900), 900,
    )
    minutes = max(1, window // 60)
    return (
        f'You have used your AI generation allowance ({limit} per {minutes} minutes). '
        'Take a short break and try again shortly.'
    )


def chat_message_quota(user):
    """Consume one Study Buddy chat/edit slot."""
    limit = _positive_int(
        getattr(settings, 'TUPAS_CHAT_RATE_LIMIT', 30), 30,
    )
    window = _positive_int(
        getattr(settings, 'TUPAS_CHAT_RATE_WINDOW', 300), 300,
    )
    return consume_rate(f'tupas:chat:{user.pk}', limit, window)


def chat_quota_message():
    limit = _positive_int(
        getattr(settings, 'TUPAS_CHAT_RATE_LIMIT', 30), 30,
    )
    window = _positive_int(
        getattr(settings, 'TUPAS_CHAT_RATE_WINDOW', 300), 300,
    )
    minutes = max(1, window // 60)
    return (
        f'You have used your Study Buddy allowance ({limit} messages per '
        f'{minutes} minutes). Take a short break and try again shortly.'
    )


def password_change_quota(user):
    """Consume one password-change slot (brute-force / abuse brake)."""
    limit = _positive_int(
        getattr(settings, 'TUPAS_PASSWORD_RATE_LIMIT', 5), 5,
    )
    window = _positive_int(
        getattr(settings, 'TUPAS_PASSWORD_RATE_WINDOW', 900), 900,
    )
    return consume_rate(f'tupas:password:{user.pk}', limit, window)


def password_quota_message():
    limit = _positive_int(
        getattr(settings, 'TUPAS_PASSWORD_RATE_LIMIT', 5), 5,
    )
    window = _positive_int(
        getattr(settings, 'TUPAS_PASSWORD_RATE_WINDOW', 900), 900,
    )
    minutes = max(1, window // 60)
    return (
        f'Too many password changes in a short time ({limit} per {minutes} minutes). '
        'Please wait a few minutes and try again.'
    )


def login_attempt_quota(request):
    """Consume one login attempt for this client IP."""
    limit = _positive_int(
        getattr(settings, 'TUPAS_LOGIN_RATE_LIMIT', 10), 10,
    )
    window = _positive_int(
        getattr(settings, 'TUPAS_LOGIN_RATE_WINDOW', 300), 300,
    )
    ip = request.META.get('REMOTE_ADDR') or 'unknown'
    return consume_rate(f'tupas:login:{ip}', limit, window)


def username_login_allowed(username):
    """True unless this account is in a failed-login lockout window."""
    limit = _positive_int(
        getattr(settings, 'TUPAS_LOGIN_USER_LIMIT', 10), 10,
    )
    key = f'tupas:login-user:{(username or "").strip().lower()}'
    failures = int(cache.get(key, 0) or 0)
    return failures < limit


def record_username_failure(username):
    """Count one failed credential attempt for this username."""
    name = (username or '').strip().lower()
    if not name:
        return
    key = f'tupas:login-user:{name}'
    window = _positive_int(
        getattr(settings, 'TUPAS_LOGIN_USER_WINDOW', 900), 900,
    )
    if cache.add(key, 1, timeout=window):
        return
    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=window)


def clear_username_failures(username):
    """Reset the failed-attempt counter after a successful login."""
    cache.delete(f'tupas:login-user:{(username or "").strip().lower()}')


def login_quota_message():
    limit = _positive_int(
        getattr(settings, 'TUPAS_LOGIN_RATE_LIMIT', 10), 10,
    )
    window = _positive_int(
        getattr(settings, 'TUPAS_LOGIN_RATE_WINDOW', 300), 300,
    )
    minutes = max(1, window // 60)
    return (
        f'Too many login attempts from this device ({limit} per {minutes} minutes). '
        'Please wait a few minutes and try again.'
    )

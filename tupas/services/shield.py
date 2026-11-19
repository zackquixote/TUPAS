"""Request shield: injection filter, scanner block, flood control, IP bans.

This is defence-in-depth on top of Django's ORM (which already parameterises
SQL), CSRF middleware, and the existing per-feature rate limits. Patterns are
high-confidence so a student searching for “select from where” in a reviewer
is not blocked.

Never log matching POST values — they may contain passwords or private notes.
"""

import logging
import re
from urllib.parse import unquote

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse

from ..models import ActivityLog
from .activity import record_activity, resolve_client_ip
from .quotas import consume_rate

logger = logging.getLogger('tupas.shield')

# Paths that never count against the per-IP request throttle.
EXEMPT_PREFIXES = (
    '/static/',
    '/media/',
    '/sw.js',
    '/health/',
    '/offline/',
    '/favicon',
)

# Exact paths that never count against the request throttle.
EXEMPT_EXACT = {'/health', '/health/'}

# Obvious scanner/probe targets. Django admin (`/admin/`) is not in this list.
PROBE_SUFFIXES = (
    '/.env',
    '/.env.local',
    '/.env.production',
    '/.git/config',
    '/.git/HEAD',
    '/.aws/credentials',
    '/.ssh/id_rsa',
    '/.DS_Store',
    '/web.config',
    '/wp-admin/',
    '/wp-login.php',
    '/wp-content/',
    '/xmlrpc.php',
    '/phpmyadmin/',
    '/pma/',
    '/adminer.php',
    '/vendor/phpunit/phpunit/phpunit.php',
    '/cgi-bin/',
    '/actuator/env',
    '/actuator/heapdump',
    '/solr/',
    '/jenkins/',
)

PROBE_EXACT = {
    '/.env',
    '/.git',
    '/wp-login.php',
    '/xmlrpc.php',
    '/phpinfo.php',
    '/info.php',
    '/adminer.php',
    '/composer.json',
    '/package.json.bak',
}

# OpenAI-compatible discovery (vLLM / LiteLLM default port is 8000).
PROBE_PREFIXES = ('/v1/',)

# File extensions that have no business on this Django app.
PROBE_EXT = ('.php', '.asp', '.aspx', '.jsp', '.cgi', '.pl', '.bak', '.sql', '.rar', '.7z')

# High-confidence injection / XSS / traversal. Applied to path + GET, and to
# form POST values except passwords, notes, and CSRF tokens.
_INJECTION_RE = re.compile(
    r"(\bunion\s+(all\s+)?select\b"
    r"|\bdrop\s+(table|database|schema)\b"
    r"|\binsert\s+into\b.{0,40}\bvalues\b"
    r"|\bdelete\s+from\b"
    r"|\btruncate\s+table\b"
    r"|\bexec\s*(\(|\s+master\.)"
    r"|\bxp_cmdshell\b"
    r"|\bwaitfor\s+delay\b"
    r"|\bbenchmark\s*\("
    r"|\bsleep\s*\(\s*\d"
    r"|\bpg_sleep\s*\("
    r"|\binformation_schema\b"
    r"|\bload_file\s*\("
    r"|\binto\s+(out|dump)file\b"
    r"|\bor\b\s+1\s*=\s*1\b"
    r"|\band\b\s+1\s*=\s*1\b"
    r"|'\s*(or|and)\s+'"
    r"|;\s*--"
    r"|^\s*--"
    r"|<script\b"
    r"|javascript\s*:"
    r"|\bonerror\s*="
    r"|\bonload\s*="
    r"|\bdocument\.cookie\b"
    r"|\.\./"
    r"|\.\.\\"
    r"|[\x00])",
    re.I | re.S,
)

# User agents used by well-known attack tools. Normal browsers never send these.
BLOCKED_UA_RE = re.compile(
    r"\b(sqlmap|nikto|nmap|masscan|zgrab|hydra|dirbuster|gobuster|wfuzz|ffuf|"
    r"acunetix|netsparker|w3af|havij|openvas|nessus|metasploit)\b",
    re.I,
)

# Form fields we never feed to the injection filter.
SKIP_POST_KEYS = {
    'csrfmiddlewaretoken',
    'password',
    'password1',
    'password2',
    'old_password',
    'new_password1',
    'new_password2',
    'notes',
    'pasted_notes',
    'extracted_text',
    'prompt_text',
    'explanation',
    'message',
}

MAX_PATH_SCAN = 2048
MAX_VALUE_SCAN = 4096


def shield_enabled():
    return bool(getattr(settings, 'TUPAS_SHIELD_ENABLED', True))


def client_ip(request):
    """REMOTE_ADDR, unless the peer is a configured trusted proxy."""
    return resolve_client_ip(request) or 'unknown'


def _variants(value):
    """Original value plus a single URL-decode, so %27%20OR%201%3D1 is seen."""
    if not value:
        return
    text = str(value)
    if len(text) > MAX_VALUE_SCAN:
        text = text[:MAX_VALUE_SCAN]
    yield text
    try:
        decoded = unquote(text)
    except Exception:
        return
    if decoded and decoded != text:
        yield decoded


def match_injection(value):
    """Return a short rule name when the value looks like an attack, else None."""
    for candidate in _variants(value):
        found = _INJECTION_RE.search(candidate)
        if found:
            snippet = found.group(0)[:40]
            return f'injection:{snippet}'
    return None


def match_probe_path(path):
    """Return a rule name for scanner paths, else None."""
    if not path:
        return None
    lowered = path.lower()
    if lowered in PROBE_EXACT or lowered.rstrip('/') in {p.rstrip('/') for p in PROBE_EXACT}:
        return f'probe:{lowered}'
    for prefix in PROBE_PREFIXES:
        if lowered.startswith(prefix):
            return f'probe:{prefix}'
    for suffix in PROBE_SUFFIXES:
        if lowered.endswith(suffix) or lowered.startswith(suffix):
            return f'probe:{suffix}'
    for ext in PROBE_EXT:
        if lowered.endswith(ext):
            return f'probe-ext:{ext}'
    if lowered.count('../') or '%2e%2e' in lowered or '..%2f' in lowered:
        return 'probe:traversal'
    return None


def match_bad_user_agent(user_agent):
    if not user_agent:
        return None
    found = BLOCKED_UA_RE.search(str(user_agent)[:300])
    return f'ua:{found.group(0)}' if found else None


def is_banned(ip):
    return cache.get(f'tupas:shield:ban:{ip}') is not None


def ban_remaining(ip):
    """Best-effort Retry-After; cache backends may not expose remaining TTL."""
    if not is_banned(ip):
        return 0
    return int(getattr(settings, 'TUPAS_SHIELD_BAN_SECONDS', 900))


def record_shield_hit(ip):
    """Count WAF trips; ban the IP after N hits inside the ban window."""
    after = max(1, int(getattr(settings, 'TUPAS_SHIELD_BAN_AFTER', 6)))
    window = max(1, int(getattr(settings, 'TUPAS_SHIELD_BAN_SECONDS', 900)))
    key = f'tupas:shield:hits:{ip}'
    if cache.add(key, 1, timeout=window):
        count = 1
    else:
        try:
            count = cache.incr(key)
        except ValueError:
            cache.set(key, 1, timeout=window)
            count = 1
    if count >= after:
        cache.set(
            f'tupas:shield:ban:{ip}',
            count,
            timeout=int(getattr(settings, 'TUPAS_SHIELD_BAN_SECONDS', 900)),
        )
        logger.warning('Shield banned %s after %s hits', ip, count)
    return count


def request_throttle_retry_after(request):
    """Return Retry-After seconds when this IP is over the request limit, else 0."""
    limit = int(getattr(settings, 'TUPAS_REQ_LIMIT', 0))
    window = int(getattr(settings, 'TUPAS_REQ_WINDOW', 60))
    if limit <= 0 or window <= 0:
        return 0
    path = request.path or '/'
    if path in EXEMPT_EXACT or any(path.startswith(prefix) for prefix in EXEMPT_PREFIXES):
        return 0
    ip = client_ip(request)
    allowed, _remaining = consume_rate(f'tupas:shield:req:{ip}', limit, window)
    return 0 if allowed else max(1, window)


def _iter_form_values(request):
    """Yield (key, value) for GET and safe POST fields only."""
    for key, values in request.GET.lists():
        for value in values:
            yield key, value
    content_type = request.META.get('CONTENT_TYPE', '')
    if request.method not in {'POST', 'PUT', 'PATCH'}:
        return
    if 'application/x-www-form-urlencoded' not in content_type and 'multipart/form-data' not in content_type:
        return
    try:
        post = request.POST
    except Exception:
        return
    for key, values in post.lists():
        if key.lower() in SKIP_POST_KEYS:
            continue
        for value in values:
            yield key, value
    for key, files in request.FILES.lists():
        for uploaded in files:
            yield key, getattr(uploaded, 'name', '')


def inspect_request(request):
    """Return (rule, status) when the request should be rejected, else (None, 0)."""
    if not shield_enabled():
        return None, 0

    path = request.path or '/'
    rule = match_probe_path(path)
    if rule:
        return rule, 404

    rule = match_bad_user_agent(request.META.get('HTTP_USER_AGENT'))
    if rule:
        return rule, 403

    # Path itself (already URL-decoded by Django) plus raw query string.
    if len(path) > MAX_PATH_SCAN or match_injection(path):
        return 'path:injection', 400
    query = request.META.get('QUERY_STRING', '')
    if query and match_injection(query[:MAX_VALUE_SCAN]):
        return 'query:injection', 400

    max_body = int(getattr(settings, 'TUPAS_MAX_BODY_BYTES', 0))
    if max_body > 0:
        raw_length = request.META.get('CONTENT_LENGTH') or 0
        try:
            length = int(raw_length)
        except (TypeError, ValueError):
            length = 0
        if length > max_body:
            return 'body:too-large', 413

    for key, value in _iter_form_values(request):
        if not value:
            continue
        rule = match_injection(value)
        if rule:
            return f'param:{key}:{rule}', 400

    return None, 0


def blocked_response(status, rule, retry_after=0):
    messages = {
        400: 'That request was blocked as unsafe.',
        403: 'Your client was blocked.',
        404: 'Not found.',
        413: 'That request body is too large.',
        429: 'Too many requests. Please slow down and try again shortly.',
    }
    headers = {}
    if status == 429:
        headers['Retry-After'] = str(max(1, retry_after or 60))
    response = HttpResponse(messages.get(status, 'Blocked.'), status=status, headers=headers)
    response['X-TUPAS-Shield'] = '1'
    return response


class RequestShieldMiddleware:
    """Early rejection of scanners, injection payloads, and volume floods.

    Runs before sessions/auth so a flood cannot reach the database. Responses
    still pass through SecurityHeadersMiddleware (this class sits inside it).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not shield_enabled():
            return self.get_response(request)

        ip = client_ip(request)

        if is_banned(ip):
            retry = ban_remaining(ip) or int(getattr(settings, 'TUPAS_SHIELD_BAN_SECONDS', 900))
            logger.info('Shield dropped banned ip=%s path=%s', ip, request.path)
            record_activity(
                None,
                ActivityLog.Action.SHIELD_BLOCK,
                f'ban ip={ip} path={request.path[:120]}',
                request,
            )
            return blocked_response(429, 'ban', retry_after=retry)

        retry_after = request_throttle_retry_after(request)
        if retry_after:
            logger.info('Shield 429 ip=%s path=%s', ip, request.path)
            record_activity(
                None,
                ActivityLog.Action.RATE_LIMITED,
                f'shield throttle ip={ip} path={request.path[:120]}',
                request,
            )
            return blocked_response(429, 'throttle', retry_after=retry_after)

        rule, status = inspect_request(request)
        if rule:
            logger.warning(
                'Shield block ip=%s method=%s path=%.200s rule=%s status=%s',
                ip, request.method, request.path, rule, status,
            )
            record_shield_hit(ip)
            record_activity(
                None,
                ActivityLog.Action.SHIELD_BLOCK,
                f'rule={rule} status={status} path={request.path[:120]}',
                request,
            )
            return blocked_response(status, rule)

        return self.get_response(request)

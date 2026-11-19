"""Per-deck study session counters kept in the visitor's session."""

from datetime import datetime

from django.utils import timezone

from ..models import ItemProgress

SESSION_PREFIX = 'tupas:study-session'


def _key(deck):
    return f'{SESSION_PREFIX}:{deck.pk}'


def start_session(request, deck):
    """Return the active session for a deck, creating it on first use."""
    data = request.session.get(_key(deck))
    if not data:
        data = {
            'started_at': timezone.now().isoformat(),
            'reviewed': 0,
            'mastered': 0,
            'needs_review': 0,
            'weak_new': 0,
        }
        request.session[_key(deck)] = data
    return data


def record_mark(request, deck, status, previous_status=None):
    """Count one marked card, including newly discovered weak spots."""
    data = dict(start_session(request, deck))
    data['reviewed'] = int(data.get('reviewed', 0)) + 1

    if status == ItemProgress.MasteryStatus.MASTERED:
        data['mastered'] = int(data.get('mastered', 0)) + 1
    elif status == ItemProgress.MasteryStatus.NEEDS_REVIEW:
        data['needs_review'] = int(data.get('needs_review', 0)) + 1
        if previous_status != ItemProgress.MasteryStatus.NEEDS_REVIEW:
            data['weak_new'] = int(data.get('weak_new', 0)) + 1

    request.session[_key(deck)] = data
    return data


def _duration_label(seconds):
    seconds = max(0, int(seconds))
    minutes, seconds = divmod(seconds, 60)
    if minutes >= 60:
        hours, minutes = divmod(minutes, 60)
        return f'{hours}h {minutes}m'
    if minutes:
        return f'{minutes}m {seconds}s'
    return f'{seconds}s'


def summarize(request, deck, completed=False):
    """Build the recap values shown when a study session is finished."""
    data = request.session.get(_key(deck))
    if not data:
        return None

    try:
        started = datetime.fromisoformat(data['started_at'])
    except (KeyError, TypeError, ValueError):
        started = timezone.now()
    if timezone.is_naive(started):
        started = timezone.make_aware(started, timezone.get_current_timezone())

    reviewed = int(data.get('reviewed', 0))
    mastered = int(data.get('mastered', 0))
    return {
        'reviewed': reviewed,
        'mastered': mastered,
        'needs_review': int(data.get('needs_review', 0)),
        'weak_new': int(data.get('weak_new', 0)),
        'got_it_percent': round((mastered / reviewed) * 100) if reviewed else 0,
        'duration_label': _duration_label(
            (timezone.now() - started).total_seconds()
        ),
        'completed': completed,
    }

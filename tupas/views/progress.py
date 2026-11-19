"""Mastery progress API used by the study-mode buttons."""

import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from ..models import ItemProgress, ReviewItem
from ..services import sessions
from ..services.gamification import award_xp, XP_AGAIN, XP_GOOD, XP_HARD
from ..services.rbac import student_only
from ..services.spaced_repetition import schedule_next_review
from ..services.streaks import record_study_day


def _session_complete(user, item, summary):
    """A session is complete once every card of this mode has been marked."""
    if not summary:
        return False
    mode_total = item.deck.items.filter(item_type=item.item_type).count()
    marked = ItemProgress.objects.filter(
        user=user,
        review_item__deck=item.deck,
        review_item__item_type=item.item_type,
    ).count()
    return summary['reviewed'] > 0 and marked >= mode_total


def _read_payload(request):
    """Accept form POST or a JSON body."""
    data = dict(request.POST.items())
    if data.get('status') or data.get('confidence'):
        return data
    try:
        payload = json.loads(request.body or '{}')
    except (TypeError, ValueError, json.JSONDecodeError):
        return data
    if isinstance(payload, dict):
        data.update(payload)
    return data


@student_only
@require_POST
def mark_progress(request, item_id):
    item = get_object_or_404(ReviewItem.objects.select_related('deck'), pk=item_id)
    data = _read_payload(request)

    confidence = (data.get('confidence') or '').strip().upper()
    status = (data.get('status') or '').strip().upper()

    # Map legacy 2-level status onto the 3-level scale.
    if not confidence:
        if status == 'MASTERED':
            confidence = ItemProgress.Confidence.GOOD
        elif status == 'NEEDS_REVIEW':
            confidence = ItemProgress.Confidence.HARD

    valid_confidence = {choice[0] for choice in ItemProgress.Confidence.choices}
    if confidence not in valid_confidence:
        return JsonResponse({'ok': False, 'error': 'Invalid confidence or mastery status.'}, status=400)

    if confidence == ItemProgress.Confidence.GOOD:
        status = ItemProgress.MasteryStatus.MASTERED
        xp_amount = XP_GOOD
    elif confidence == ItemProgress.Confidence.AGAIN:
        status = ItemProgress.MasteryStatus.NEEDS_REVIEW
        xp_amount = XP_AGAIN
    else:
        status = ItemProgress.MasteryStatus.NEEDS_REVIEW
        xp_amount = XP_HARD

    previous = ItemProgress.objects.filter(
        user=request.user,
        review_item=item,
    ).only('mastery_status').first()
    previous_status = previous.mastery_status if previous else None

    progress, _ = ItemProgress.objects.update_or_create(
        user=request.user,
        review_item=item,
        defaults={
            'mastery_status': status,
            'confidence_level': confidence,
        },
    )
    schedule_next_review(progress, status, confidence=confidence)
    record_study_day(request.user)
    award_xp(request.user, xp_amount)

    sessions.record_mark(request, item.deck, status, previous_status)
    summary = sessions.summarize(request, item.deck)
    summary['completed'] = _session_complete(request.user, item, summary)

    deck_progress = ItemProgress.objects.filter(user=request.user, review_item__deck=item.deck)
    profile = getattr(request.user, 'profile', None)
    return JsonResponse({
        'ok': True,
        'status': progress.mastery_status,
        'status_label': progress.get_mastery_status_display(),
        'confidence': progress.confidence_level,
        'confidence_label': progress.get_confidence_level_display(),
        'xp_awarded': xp_amount,
        'xp_points': profile.xp_points if profile else 0,
        'level': profile.level if profile else 1,
        'mastered': deck_progress.filter(mastery_status=ItemProgress.MasteryStatus.MASTERED).count(),
        'needs_review': deck_progress.filter(mastery_status=ItemProgress.MasteryStatus.NEEDS_REVIEW).count(),
        'total_marked': deck_progress.count(),
        'review_count': progress.review_count,
        'next_review_at': progress.next_review_at.isoformat() if progress.next_review_at else None,
        'session': summary,
    })

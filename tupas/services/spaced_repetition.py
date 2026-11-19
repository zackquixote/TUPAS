"""Leitner-style spaced repetition scheduling stored on ItemProgress."""

from datetime import timedelta

from django.utils import timezone

from ..models import ItemProgress

# Days until the next review after each consecutive successful recall.
REVIEW_INTERVALS = (1, 3, 7, 21, 60)

AGAIN_MINUTES = 10
HARD_DAYS = 1


def interval_days(review_count):
    """Interval for the Nth successful review, capped at the final step."""
    index = min(max(int(review_count or 0), 1), len(REVIEW_INTERVALS)) - 1
    return REVIEW_INTERVALS[index]


def schedule_next_review(progress, status, now=None, confidence=None):
    """Update ``next_review_at``/``review_count`` for a new mastery status.

    ``AGAIN`` comes back in 10 minutes.
    ``HARD`` (or legacy ``NEEDS_REVIEW``) comes back tomorrow.
    ``GOOD`` (or legacy ``MASTERED``) advances through 1, 3, 7, 21, then 60 days.
    """
    now = now or timezone.now()
    confidence = confidence or ''

    if confidence == ItemProgress.Confidence.AGAIN:
        progress.review_count = 0
        progress.confidence_level = ItemProgress.Confidence.AGAIN
        progress.next_review_at = now + timedelta(minutes=AGAIN_MINUTES)
        progress.save(update_fields=['review_count', 'confidence_level', 'next_review_at'])
        return progress.next_review_at

    if confidence == ItemProgress.Confidence.HARD:
        progress.review_count = 0
        progress.confidence_level = ItemProgress.Confidence.HARD
        progress.next_review_at = now + timedelta(days=HARD_DAYS)
        progress.save(update_fields=['review_count', 'confidence_level', 'next_review_at'])
        return progress.next_review_at

    if status == ItemProgress.MasteryStatus.MASTERED or confidence == ItemProgress.Confidence.GOOD:
        progress.review_count = (progress.review_count or 0) + 1
        days = interval_days(progress.review_count)
        progress.confidence_level = ItemProgress.Confidence.GOOD
        progress.next_review_at = now + timedelta(days=days)
        progress.save(update_fields=['review_count', 'confidence_level', 'next_review_at'])
        return progress.next_review_at

    # Legacy NEEDS_REVIEW without a 3-level confidence value.
    progress.review_count = 0
    progress.next_review_at = now + timedelta(days=1)
    progress.save(update_fields=['review_count', 'next_review_at'])
    return progress.next_review_at

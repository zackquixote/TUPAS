"""Weak-spot, unmastered, and due-today study filters."""

from django.utils import timezone

from ..models import ItemProgress

STUDY_FILTERS = {'all', 'weak', 'unmastered', 'due'}


def study_filter_key(request):
    """Read the `?filter=` study-mode selector, defaulting to all items."""
    value = (request.GET.get('filter') or 'all').strip().lower()
    return value if value in STUDY_FILTERS else 'all'


def study_progress_map(user, items):
    return {
        progress.review_item_id: progress.mastery_status
        for progress in ItemProgress.objects.filter(user=user, review_item__in=items)
    }


def due_item_ids(user, items, now=None):
    """Ids of items whose spaced-repetition review date has arrived."""
    return set(
        ItemProgress.objects
        .filter(
            user=user,
            review_item__in=items,
            next_review_at__lte=now or timezone.now(),
        )
        .values_list('review_item_id', flat=True)
    )


def filter_study_items(items, progress_map, filter_key, due_ids=None):
    """Narrow the deck's items to weak spots, anything unmastered, or due."""
    if filter_key == 'weak':
        return [
            item for item in items
            if progress_map.get(item.id) == ItemProgress.MasteryStatus.NEEDS_REVIEW
        ]
    if filter_key == 'unmastered':
        return [
            item for item in items
            if progress_map.get(item.id) != ItemProgress.MasteryStatus.MASTERED
        ]
    if filter_key == 'due':
        due_ids = due_ids or set()
        return [item for item in items if item.id in due_ids]
    return list(items)


def study_filter_context(items, progress_map, filter_key, due_ids=None):
    return {
        'current_filter': filter_key,
        'total_items': len(items),
        'weak_count': sum(
            1 for item in items
            if progress_map.get(item.id) == ItemProgress.MasteryStatus.NEEDS_REVIEW
        ),
        'unmastered_count': sum(
            1 for item in items
            if progress_map.get(item.id) != ItemProgress.MasteryStatus.MASTERED
        ),
        'due_count': len(due_ids or set()),
    }

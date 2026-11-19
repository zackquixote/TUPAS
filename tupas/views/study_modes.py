"""Flip card, highlight note, and enumeration study modes."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from ..models import Deck, ItemProgress, ReviewItem
from ..services import sessions
from ..services.grading import grade_enumeration
from ..services.access import can_edit_deck
from ..services.rbac import student_only
from ..services.progress import (
    due_item_ids,
    filter_study_items,
    study_filter_context,
    study_filter_key,
    study_progress_map,
)


def _session_summary(request, deck, items, progress_map):
    """Start a deck session and mark it complete when every shown card is marked."""
    sessions.start_session(request, deck)
    summary = sessions.summarize(request, deck)
    if items:
        marked = sum(1 for item in items if progress_map.get(item.id))
        summary['completed'] = summary['reviewed'] > 0 and marked == len(items)
    return summary


@student_only
def flip_mode(request, deck_id):
    deck = get_object_or_404(Deck.objects.select_related('subject'), pk=deck_id)
    deck_items = list(
        deck.items
        .filter(item_type=ReviewItem.ItemType.FLIP_CARD)
        .order_by('id')
    )
    progress_map = study_progress_map(request.user, deck_items)
    filter_key = study_filter_key(request)
    due_ids = due_item_ids(request.user, deck_items)
    items = filter_study_items(deck_items, progress_map, filter_key, due_ids)
    for item in items:
        item.student_status = progress_map.get(item.id, '')

    mastered = sum(1 for status in progress_map.values() if status == ItemProgress.MasteryStatus.MASTERED)
    needs_review = sum(1 for status in progress_map.values() if status == ItemProgress.MasteryStatus.NEEDS_REVIEW)

    context = {
        'deck': deck,
        'items': items,
        'total': len(items),
        'mastered': mastered,
        'needs_review': needs_review,
        'mastered_percent': round((mastered / len(deck_items)) * 100) if deck_items else 0,
        'mode_url_name': 'tupas:flip_mode',
        'can_edit_deck': can_edit_deck(request.user, deck),
        'session_summary': _session_summary(request, deck, items, progress_map),
    }
    context.update(study_filter_context(deck_items, progress_map, filter_key, due_ids))
    return render(request, 'tupas/flip_mode.html', context)


@student_only
def highlight_mode(request, deck_id):
    deck = get_object_or_404(Deck.objects.select_related('subject'), pk=deck_id)
    deck_items = list(
        deck.items
        .filter(item_type=ReviewItem.ItemType.HIGHLIGHT_NOTE)
        .order_by('id')
    )
    progress_map = study_progress_map(request.user, deck_items)
    filter_key = study_filter_key(request)
    due_ids = due_item_ids(request.user, deck_items)
    items = filter_study_items(deck_items, progress_map, filter_key, due_ids)
    for item in items:
        item.student_status = progress_map.get(item.id, '')

    context = {
        'deck': deck,
        'items': items,
        'mastered': sum(1 for status in progress_map.values() if status == ItemProgress.MasteryStatus.MASTERED),
        'needs_review': sum(1 for status in progress_map.values() if status == ItemProgress.MasteryStatus.NEEDS_REVIEW),
        'mode_url_name': 'tupas:highlight_mode',
        'session_summary': _session_summary(request, deck, items, progress_map),
    }
    context.update(study_filter_context(deck_items, progress_map, filter_key, due_ids))
    return render(request, 'tupas/highlight_mode.html', context)


@student_only
def enumeration_mode(request, deck_id):
    deck = get_object_or_404(Deck.objects.select_related('subject'), pk=deck_id)
    deck_items = list(
        deck.items
        .filter(item_type=ReviewItem.ItemType.ENUMERATION)
        .prefetch_related('enum_answers')
        .order_by('id')
    )
    progress_map = study_progress_map(request.user, deck_items)
    filter_key = study_filter_key(request)
    due_ids = due_item_ids(request.user, deck_items)
    items = filter_study_items(deck_items, progress_map, filter_key, due_ids)

    result = None
    result_item_id = None

    if request.method == 'POST':
        try:
            item_id = int(request.POST.get('item_id', ''))
        except (TypeError, ValueError):
            messages.error(request, 'Could not identify the enumeration item.')
            return redirect('tupas:enumeration_mode', deck_id=deck.id)

        target = next((item for item in items if item.id == item_id), None)
        if target is None:
            messages.error(request, 'That enumeration item does not belong to this deck.')
            return redirect('tupas:enumeration_mode', deck_id=deck.id)

        answers = list(target.enum_answers.all())
        submitted = [
            request.POST.get(f'answer_{target.id}_{index}', '')
            for index in range(1, len(answers) + 1)
        ]
        is_correct, checks = grade_enumeration(target, submitted)

        result_item_id = target.id
        result = {
            'item_id': target.id,
            'is_correct': is_correct,
            'checks': checks,
            'submitted': submitted,
            'correct_count': sum(1 for check in checks if check),
            'total': len(answers),
            'is_strict_order': target.is_strict_order,
        }
        if is_correct:
            messages.success(request, 'All answers are correct. Well done!')
        else:
            messages.warning(request, 'Some answers need another look. Check the feedback below.')

    for item in items:
        answers = list(item.enum_answers.all())
        item.answer_list = [answer.answer_text for answer in answers]
        item.student_status = progress_map.get(item.id, '')
        if item.id == result_item_id and result is not None:
            item.slots = [
                {
                    'number': index + 1,
                    'value': result['submitted'][index] if index < len(result['submitted']) else '',
                    'is_correct': result['checks'][index] if index < len(result['checks']) else None,
                }
                for index in range(len(answers))
            ]
        else:
            item.slots = [
                {'number': index + 1, 'value': '', 'is_correct': None}
                for index in range(len(answers))
            ]

    context = {
        'deck': deck,
        'items': items,
        'result': result,
        'result_item_id': result_item_id,
        'mastered': sum(1 for status in progress_map.values() if status == ItemProgress.MasteryStatus.MASTERED),
        'needs_review': sum(1 for status in progress_map.values() if status == ItemProgress.MasteryStatus.NEEDS_REVIEW),
        'mode_url_name': 'tupas:enumeration_mode',
        'session_summary': _session_summary(request, deck, items, progress_map),
    }
    context.update(study_filter_context(deck_items, progress_map, filter_key, due_ids))
    return render(request, 'tupas/enumeration_mode.html', context)

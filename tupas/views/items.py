"""Inline add / edit / delete plus explain-deeper, feedback, and regenerate."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, reverse
from django.views.decorators.http import require_POST

from ..ai import AIError, chat_with_tupas_tutor, generate_reviewer_json
from ..models import ActivityLog, Deck, ReviewItem
from ..services.access import can_edit_deck
from ..services.activity import record_activity
from ..services.gamification import award_xp, XP_GOOD
from ..services.items import create_review_item, update_review_item


def _redirect_to_deck_items(deck):
    return redirect(f"{reverse('tupas:deck_detail', args=[deck.pk])}#items")


@login_required
@require_POST
def add_review_item(request, deck_id):
    deck = get_object_or_404(Deck, pk=deck_id)
    if not can_edit_deck(request.user, deck):
        raise PermissionDenied('You can only manage items in decks you created.')

    try:
        item = create_review_item(deck, request.POST)
    except ValidationError as exc:
        messages.error(request, ' '.join(exc.messages))
        return _redirect_to_deck_items(deck)

    messages.success(
        request,
        f'Added a {item.get_item_type_display().lower()} to “{deck.title}”.',
    )
    record_activity(
        request.user,
        ActivityLog.Action.ITEM_CREATE,
        f'{item.get_item_type_display()} in {deck.title}',
        request,
    )
    return _redirect_to_deck_items(deck)


@login_required
@require_POST
def edit_review_item(request, deck_id, item_id):
    deck = get_object_or_404(Deck, pk=deck_id)
    if not can_edit_deck(request.user, deck):
        raise PermissionDenied('You can only manage items in decks you created.')
    item = get_object_or_404(ReviewItem, pk=item_id, deck=deck)

    try:
        update_review_item(item, request.POST)
    except ValidationError as exc:
        messages.error(request, ' '.join(exc.messages))
        return _redirect_to_deck_items(deck)

    messages.success(request, 'Review item updated.')
    record_activity(
        request.user,
        ActivityLog.Action.ITEM_UPDATE,
        f'{item.get_item_type_display()} in {deck.title}',
        request,
    )
    return _redirect_to_deck_items(deck)


@login_required
@require_POST
def delete_review_item(request, deck_id, item_id):
    deck = get_object_or_404(Deck, pk=deck_id)
    if not can_edit_deck(request.user, deck):
        raise PermissionDenied('You can only manage items in decks you created.')
    item = get_object_or_404(ReviewItem, pk=item_id, deck=deck)

    label = item.get_item_type_display()
    item.delete()
    messages.success(request, f'Deleted that {label.lower()} from “{deck.title}”.')
    record_activity(
        request.user,
        ActivityLog.Action.ITEM_DELETE,
        f'{label} in {deck.title}',
        request,
    )
    return _redirect_to_deck_items(deck)


def _item_json(item, extra=None):
    payload = {
        'ok': True,
        'item_id': item.id,
        'quality_flag': item.quality_flag,
    }
    if extra:
        payload.update(extra)
    return payload


@login_required
@require_POST
def explain_deeper(request, item_id):
    """Deeper real-world / Taglish breakdown for one review item."""
    item = get_object_or_404(
        ReviewItem.objects.select_related('deck'),
        pk=item_id,
    )
    question = (
        f'Explain this in more depth for a Filipino student, with a real-world '
        f'example and a short Taglish explanation. Keep it under 250 words.\n\n'
        f'Type: {item.get_item_type_display()}\n'
        f'Prompt: {item.prompt_text}\n'
        f'Answer: {item.main_answer or ""}\n'
        f'Existing explanation: {item.explanation or "(none)"}'
    )
    try:
        explanation = chat_with_tupas_tutor(
            item.prompt_text,
            question,
            chat_history=[],
        )
    except Exception:
        explanation = (
            f'{item.explanation or item.main_answer or item.prompt_text}\n\n'
            'Tip: relate this to something you already use every day, then say '
            'it out loud in Taglish to lock it in.'
        )
    return JsonResponse({
        'ok': True,
        'item_id': item.id,
        'explanation': str(explanation or '')[:4000],
    })


@login_required
@require_POST
def item_feedback(request, item_id):
    """👍 Helpful / 👎 Needs Fix quality flag on a card."""
    item = get_object_or_404(ReviewItem, pk=item_id)
    flag = (request.POST.get('flag') or '').strip().upper()
    if flag not in {'GOOD', 'FLAGGED', ''}:
        return JsonResponse(
            {'ok': False, 'error': 'Invalid quality flag.'},
            status=400,
        )
    item.quality_flag = flag
    item.save(update_fields=['quality_flag'])
    if flag == 'GOOD':
        award_xp(request.user, XP_GOOD)
    label = 'Helpful' if flag == 'GOOD' else ('Needs Fix' if flag == 'FLAGGED' else 'None')
    return JsonResponse({
        'ok': True,
        'item_id': item.id,
        'quality_flag': item.quality_flag,
        'label': label,
    })


@login_required
@require_POST
def regenerate_item(request, item_id):
    """1-click AI regenerate of one item (owner or staff only)."""
    item = get_object_or_404(ReviewItem.objects.select_related('deck'), pk=item_id)
    deck = item.deck
    if not can_edit_deck(request.user, deck):
        raise PermissionDenied('You can only regenerate items in decks you created.')

    try:
        payload = generate_reviewer_json(
            item.prompt_text,
            coverage_mode=1,
            selected_types=[item.item_type],
            exam_style='EXACT',
        )
    except Exception as exc:
        return JsonResponse(
            {'ok': False, 'error': str(exc) or 'AI is unavailable right now.'},
            status=400,
        )

    # Replace this one item in place, keeping its id and deck.
    new_items = []
    if item.item_type == 'FLIP_CARD' and payload.get('flip_cards'):
        new_items = [('FLIP_CARD', payload['flip_cards'][0])]
    elif item.item_type == 'HIGHLIGHT_NOTE' and payload.get('highlight_notes'):
        new_items = [('HIGHLIGHT_NOTE', payload['highlight_notes'][0])]
    elif item.item_type == 'ENUMERATION' and payload.get('enumerations'):
        new_items = [('ENUMERATION', payload['enumerations'][0])]

    if not new_items:
        return JsonResponse(
            {'ok': False, 'error': 'AI did not return a usable replacement.'},
            status=400,
        )

    kind, data = new_items[0]
    item.prompt_text = str(data.get('prompt_text') or item.prompt_text)
    item.main_answer = str(data.get('main_answer') or item.main_answer or '')
    item.explanation = str(data.get('explanation') or item.explanation or '')
    item.save(update_fields=['prompt_text', 'main_answer', 'explanation'])

    # Refresh choices / enumerations from the payload when present.
    from ..ai import save_generated_items
    from ..models import EnumerationItem, QuizChoice

    if kind == 'FLIP_CARD':
        item.choices.all().delete()
        answer = item.main_answer
        distractors = [
            str(d)[:255]
            for d in (data.get('distractors') or [])[:3]
            if str(d).strip() and str(d).strip() != answer
        ]
        if answer:
            QuizChoice.objects.create(
                review_item=item, choice_text=answer, is_correct=True,
            )
            for distractor in distractors:
                QuizChoice.objects.create(
                    review_item=item, choice_text=distractor, is_correct=False,
                )
    elif kind == 'ENUMERATION':
        item.enum_answers.all().delete()
        answers = data.get('answers') or []
        EnumerationItem.objects.bulk_create([
            EnumerationItem(
                review_item=item,
                answer_text=str(text)[:255],
                order_number=index,
            )
            for index, text in enumerate(answers, start=1)
        ])

    return JsonResponse({
        'ok': True,
        'item_id': item.id,
        'prompt_text': item.prompt_text,
        'main_answer': item.main_answer or '',
        'explanation': item.explanation,
    })

"""Creating and updating review items from submitted form data."""

from django.core.exceptions import ValidationError
from django.db import transaction

from ..models import EnumerationItem, QuizChoice, ReviewItem
from .text import clean_multiline, normalize, split_answers


def create_review_item(deck, data):
    """Validate one add-item submission and persist the item.

    Raises ``ValidationError`` with a user-facing message when the submission
    is incomplete; the caller turns that into a flash message.
    """
    item_type = (data.get('item_type') or '').strip()
    prompt = clean_multiline(data.get('prompt_text'))
    explanation = clean_multiline(data.get('explanation'))
    answer = (data.get('main_answer') or '').strip()[:255]

    if item_type not in ReviewItem.ItemType.values:
        raise ValidationError('Choose a valid review item type.')
    if not prompt:
        raise ValidationError('Add the question, note, or prompt text first.')

    answers = []
    if item_type == ReviewItem.ItemType.FLIP_CARD:
        if not answer:
            raise ValidationError('Flip cards need a back-of-card answer.')
    elif item_type == ReviewItem.ItemType.ENUMERATION:
        answers = split_answers(data.get('answers'))
        if len(answers) < 2:
            raise ValidationError('Add at least two accepted answers, one per line.')

    with transaction.atomic():
        item = ReviewItem.objects.create(
            deck=deck,
            item_type=item_type,
            prompt_text=prompt,
            main_answer=answer if item_type == ReviewItem.ItemType.FLIP_CARD else None,
            explanation=explanation,
            is_strict_order=(
                item_type == ReviewItem.ItemType.ENUMERATION
                and bool(data.get('is_strict_order'))
            ),
        )

        if item_type == ReviewItem.ItemType.FLIP_CARD:
            seen = {normalize(answer)}
            distractors = []
            for index in range(1, 4):
                candidate = (data.get(f'distractor_{index}') or '').strip()[:255]
                key = normalize(candidate)
                if not candidate or key in seen:
                    continue
                seen.add(key)
                distractors.append(candidate)
            if distractors:
                QuizChoice.objects.create(
                    review_item=item,
                    choice_text=answer,
                    is_correct=True,
                )
                for distractor in distractors:
                    QuizChoice.objects.create(
                        review_item=item,
                        choice_text=distractor,
                        is_correct=False,
                    )
        elif item_type == ReviewItem.ItemType.ENUMERATION:
            EnumerationItem.objects.bulk_create([
                EnumerationItem(
                    review_item=item,
                    answer_text=text,
                    order_number=index,
                )
                for index, text in enumerate(answers, start=1)
            ])

    return item


def update_review_item(item, data):
    """Apply an edit submission to an existing item and sync its children."""
    prompt = clean_multiline(data.get('prompt_text'))
    explanation = clean_multiline(data.get('explanation'))
    answer = (data.get('main_answer') or '').strip()[:255]
    answers = []

    if not prompt:
        raise ValidationError('The question, note, or prompt cannot be empty.')
    if item.item_type == ReviewItem.ItemType.FLIP_CARD and not answer:
        raise ValidationError('Flip cards need a back-of-card answer.')
    if item.item_type == ReviewItem.ItemType.ENUMERATION:
        answers = split_answers(data.get('answers'))
        if len(answers) < 2:
            raise ValidationError('Keep at least two accepted answers for this drill.')

    with transaction.atomic():
        item.prompt_text = prompt
        item.explanation = explanation
        update_fields = ['prompt_text', 'explanation']

        if item.item_type == ReviewItem.ItemType.FLIP_CARD:
            item.main_answer = answer
            update_fields.append('main_answer')
        if item.item_type == ReviewItem.ItemType.ENUMERATION:
            item.is_strict_order = bool(data.get('is_strict_order'))
            update_fields.append('is_strict_order')
        item.save(update_fields=update_fields)

        if item.item_type == ReviewItem.ItemType.FLIP_CARD:
            # Keep the generated multiple-choice answer in sync with the edit.
            item.choices.filter(is_correct=True).update(choice_text=answer)
        elif item.item_type == ReviewItem.ItemType.ENUMERATION:
            item.enum_answers.all().delete()
            EnumerationItem.objects.bulk_create([
                EnumerationItem(
                    review_item=item,
                    answer_text=text,
                    order_number=index,
                )
                for index, text in enumerate(answers, start=1)
            ])

    return item

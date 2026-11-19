"""Deck duplication and merge helpers."""

from django.db import transaction

from ..models import Deck, EnumerationItem, QuizChoice, ReviewItem
from .text import normalize


def _copy_item_to(deck, item):
    """Copy one ReviewItem (and its children) onto ``deck``."""
    copied_item = ReviewItem.objects.create(
        deck=deck,
        item_type=item.item_type,
        prompt_text=item.prompt_text,
        main_answer=item.main_answer,
        explanation=item.explanation,
        is_strict_order=item.is_strict_order,
        quality_flag=item.quality_flag,
    )
    QuizChoice.objects.bulk_create([
        QuizChoice(
            review_item=copied_item,
            choice_text=choice.choice_text,
            is_correct=choice.is_correct,
        )
        for choice in item.choices.all()
    ])
    EnumerationItem.objects.bulk_create([
        EnumerationItem(
            review_item=copied_item,
            answer_text=answer.answer_text,
            order_number=answer.order_number,
        )
        for answer in item.enum_answers.all()
    ])
    return copied_item


def copy_deck(source, user):
    """Copy a deck and all of its items, choices, and enumerations for ``user``."""
    with transaction.atomic():
        clone = Deck.objects.create(
            subject=source.subject,
            title=f'{source.title} (Copy)'[:200],
            description=source.description,
            created_by=user,
            tags=source.tags,
        )
        for item in source.items.prefetch_related('choices', 'enum_answers'):
            _copy_item_to(clone, item)
    return clone


def merge_decks(target, source):
    """Merge source items into target, skipping duplicate (type, prompt) pairs.

    Returns the number of newly copied items.
    """
    existing = {
        (item.item_type, normalize(item.prompt_text))
        for item in target.items.only('item_type', 'prompt_text')
    }
    copied = 0
    with transaction.atomic():
        for item in source.items.prefetch_related('choices', 'enum_answers'):
            key = (item.item_type, normalize(item.prompt_text))
            if key in existing:
                continue
            existing.add(key)
            _copy_item_to(target, item)
            copied += 1
    return copied

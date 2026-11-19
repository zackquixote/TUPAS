"""Building the printable cram-sheet context."""

from ..models import ItemProgress, ReviewItem
from .progress import filter_study_items, study_progress_map


def build_cram_sheet_context(deck, user, filter_key='all'):
    """Group a deck's items into the three cram-sheet sections.

    ``filter_key='weak'`` limits the sheet to the student's weak spots.
    """
    deck_items = list(
        deck.items.prefetch_related('choices', 'enum_answers').order_by('id')
    )
    progress_map = study_progress_map(user, deck_items)
    items = (
        filter_study_items(deck_items, progress_map, 'weak')
        if filter_key == 'weak'
        else deck_items
    )

    flip_cards = [
        item for item in items
        if item.item_type == ReviewItem.ItemType.FLIP_CARD and item.main_answer
    ]
    highlight_notes = [
        item for item in items
        if item.item_type == ReviewItem.ItemType.HIGHLIGHT_NOTE
    ]
    enumerations = [
        item for item in items
        if item.item_type == ReviewItem.ItemType.ENUMERATION
    ]
    for item in enumerations:
        item.cram_answers = [answer.answer_text for answer in item.enum_answers.all()]

    return {
        'deck': deck,
        'filter_key': filter_key,
        'flip_cards': flip_cards,
        'highlight_notes': highlight_notes,
        'enumerations': enumerations,
        'total_items': len(items),
        'weak_count': sum(
            1 for item in deck_items
            if progress_map.get(item.id) == ItemProgress.MasteryStatus.NEEDS_REVIEW
        ),
    }

"""HTTP layer for the TUPAS reviewer app.

Each module owns one feature area; reusable logic lives in `tupas.services`
and data definitions in `tupas.models`. These re-exports let `tupas.urls`
keep using ``views.<name>``.
"""

from ..ai import extract_text_from_pptx_stream_view
from .ai import ai_generator, extract_image, health
from .auth import RateLimitedLoginView, register
from .dashboard import dashboard, insights
from .decks import (
    clone_deck,
    deck_cram_sheet,
    deck_detail,
    deck_short_link,
    export_all_attempts,
    export_attempts,
    export_deck,
    import_deck,
    merge_deck,
    toggle_archive_deck,
    toggle_pin_deck,
)
from .exams import exam_attempt_detail, exam_mode
from .items import (
    add_review_item,
    delete_review_item,
    edit_review_item,
    explain_deeper,
    item_feedback,
    regenerate_item,
)
from .leaderboard import leaderboard
from .progress import mark_progress
from .profile import profile
from .study_buddy import (
    study_buddy,
    study_buddy_chat,
    study_buddy_clear_chat,
    study_buddy_delete_doc,
    study_buddy_delete_message,
    study_buddy_detail,
    study_buddy_edit_doc,
    study_buddy_edit_message,
    study_buddy_generate_deck,
)
from .study_modes import enumeration_mode, flip_mode, highlight_mode
from .users import (
    admin_edit_account,
    admin_set_password,
    set_user_role,
    user_detail,
    user_list,
)

__all__ = [
    'add_review_item',
    'ai_generator',
    'clone_deck',
    'dashboard',
    'extract_text_from_pptx_stream_view',
    'deck_cram_sheet',
    'deck_detail',
    'deck_short_link',
    'delete_review_item',
    'export_all_attempts',
    'edit_review_item',
    'export_attempts',
    'enumeration_mode',
    'exam_mode',
    'exam_attempt_detail',
    'export_deck',
    'extract_image',
    'flip_mode',
    'highlight_mode',
    'insights',
    'import_deck',
    'item_feedback',
    'leaderboard',
    'mark_progress',
    'merge_deck',
    'profile',
    'regenerate_item',
    'register',
    'study_buddy',
    'study_buddy_chat',
    'study_buddy_clear_chat',
    'study_buddy_delete_doc',
    'study_buddy_delete_message',
    'study_buddy_detail',
    'study_buddy_edit_doc',
    'study_buddy_edit_message',
    'study_buddy_generate_deck',
    'RateLimitedLoginView',
    'toggle_archive_deck',
    'toggle_pin_deck',
    'explain_deeper',
    'set_user_role',
    'user_detail',
    'user_list',
    'admin_edit_account',
    'admin_set_password',
]

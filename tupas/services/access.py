"""Ownership and role-based curation permissions."""

from .rbac import effective_role, is_admin  # noqa: F401


def can_edit_deck(user, deck):
    """Admins curate any deck; students curate their own."""
    if not getattr(user, 'is_authenticated', False):
        return False
    if effective_role(user) == 'ADMIN':
        return True
    return deck.created_by_id == user.pk


def can_edit_study_document(user, document):
    """Study Buddy handouts are private: only the owner may manage them."""
    return bool(
        getattr(user, 'is_authenticated', False)
        and document is not None
        and document.user_id == user.pk
    )


def can_manage_users(user):
    return is_admin(user)


def can_view_all_users(user):
    return is_admin(user)

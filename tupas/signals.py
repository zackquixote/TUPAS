"""Auth + admin signals that append to ActivityLog."""

from django.contrib.admin.models import DELETION, LogEntry
from django.contrib.auth.signals import (
    user_login_failed,
    user_logged_in,
    user_logged_out,
)
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import ActivityLog
from .services.activity import mask_identifier, record_activity


@receiver(user_logged_in)
def log_login(sender, request, user, **kwargs):
    record_activity(user, ActivityLog.Action.LOGIN, 'Signed in', request)


@receiver(user_logged_out)
def log_logout(sender, request, user=None, **kwargs):
    if user is not None and getattr(user, 'is_authenticated', False):
        record_activity(user, ActivityLog.Action.LOGOUT, 'Signed out', request)


@receiver(user_login_failed)
def log_failed_login(sender, credentials=None, request=None, **kwargs):
    username = (credentials or {}).get('username') or ''
    record_activity(
        None,
        ActivityLog.Action.LOGIN_FAILED,
        f'Identifier: {mask_identifier(username)}' if username else 'Unknown identifier',
        request,
    )


@receiver(post_save, sender=LogEntry)
def log_admin_log_entry(sender, instance, created, **kwargs):
    """Mirror Django-admin add/change/delete into the TUPAS audit trail.

    ``LogEntry`` is written after the object is saved or deleted, so this is
    the single hook that covers every ModelAdmin (decks, items, users, …)
    without touching each admin class.
    """
    if not created:
        return
    model_name = (instance.content_type.model or '').lower()
    if instance.action_flag == DELETION:
        special = {
            'user': ActivityLog.Action.USER_DELETE,
            'deck': ActivityLog.Action.DECK_DELETE,
            'reviewitem': ActivityLog.Action.ITEM_DELETE,
            'studydocument': ActivityLog.Action.DOCUMENT_DELETE,
        }
        action = special.get(model_name, ActivityLog.Action.ADMIN_EDIT)
        detail = f'{instance.content_type.app_label}.{model_name} #{instance.object_id} {instance.object_repr}'
    else:
        action = ActivityLog.Action.ADMIN_EDIT
        flag = instance.get_action_flag_display()
        detail = f'{instance.object_repr} ({flag})'
    record_activity(instance.user, action, detail)

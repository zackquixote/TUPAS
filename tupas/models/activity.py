"""Activity log (admin visibility): auth events and key content/exam actions."""

from django.conf import settings
from django.db import models


class ActivityLog(models.Model):
    """Append-only trail of account and study activity for the admin panel."""

    class Action(models.TextChoices):
        LOGIN = 'LOGIN', 'Logged in'
        LOGIN_FAILED = 'LOGIN_FAILED', 'Failed login'
        LOGOUT = 'LOGOUT', 'Logged out'
        REGISTER = 'REGISTER', 'Registered'
        ROLE_CHANGE = 'ROLE_CHANGE', 'Changed role'
        PASSWORD_CHANGE = 'PASSWORD_CHANGE', 'Changed password'
        DECK_CREATE = 'DECK_CREATE', 'Created deck'
        DECK_IMPORT = 'DECK_IMPORT', 'Imported deck'
        DECK_DELETE = 'DECK_DELETE', 'Deleted deck'
        DOCUMENT_UPLOAD = 'DOCUMENT_UPLOAD', 'Uploaded handout'
        DOCUMENT_DELETE = 'DOCUMENT_DELETE', 'Deleted handout'
        EXAM_SUBMIT = 'EXAM_SUBMIT', 'Submitted exam'
        ITEM_CREATE = 'ITEM_CREATE', 'Added review item'
        ITEM_UPDATE = 'ITEM_UPDATE', 'Updated review item'
        ITEM_DELETE = 'ITEM_DELETE', 'Deleted review item'
        ADMIN_EDIT = 'ADMIN_EDIT', 'Admin content change'
        USER_DELETE = 'USER_DELETE', 'Deleted user'
        DENIED = 'DENIED', 'Access denied'
        RATE_LIMITED = 'RATE_LIMITED', 'Rate limited'
        SHIELD_BLOCK = 'SHIELD_BLOCK', 'Shield blocked'
        EXPORT = 'EXPORT', 'Exported deck'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='activity_logs',
    )
    # Snapshots survive the user row being deleted (SET_NULL would not).
    username_snapshot = models.CharField(max_length=150, blank=True, default='')
    user_id_snapshot = models.PositiveBigIntegerField(null=True, blank=True)
    request_id = models.CharField(max_length=64, blank=True, default='', db_index=True)
    action = models.CharField(max_length=40, choices=Action.choices, db_index=True)
    detail = models.CharField(max_length=255, blank=True, default='')
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'activity log'
        verbose_name_plural = 'activity logs'
        default_permissions = ('view',)
        indexes = [
            models.Index(fields=['action', '-created_at']),
        ]

    def __str__(self):
        who = self.username_snapshot or (self.user.username if self.user else 'anonymous')
        return f'{who} — {self.get_action_display()}'

    @property
    def actor_username(self):
        """Best-known username; snapshots survive SET_NULL."""
        if self.username_snapshot:
            return self.username_snapshot
        if self.user is not None:
            return self.user.username
        return '—'

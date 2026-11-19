"""Role-based access control helpers.

Roles:
- ``ADMIN``: ``User.is_staff`` or ``User.is_superuser``. Full curation
  plus user management (accounts, passwords, roles).
- ``STUDENT``: everyone else. Own decks and own Study Buddy notes only.

``TEACHER`` was removed; the migration turns any leftover
``profile.role == 'TEACHER'`` into ``STUDENT``.
"""

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import redirect


def effective_role(user):
    """Return ``ADMIN``, ``STUDENT``, or ``None`` (anonymous)."""
    if not getattr(user, 'is_authenticated', False):
        return None
    if user.is_superuser or user.is_staff:
        return 'ADMIN'
    return 'STUDENT'


def is_admin(user):
    return effective_role(user) == 'ADMIN'


def can_manage_users(user):
    return is_admin(user)


def can_view_all_users(user):
    return is_admin(user)


def role_required(*roles):
    """View decorator: login plus one of the allowed roles."""
    allowed = set(roles)

    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(request, *args, **kwargs):
            if effective_role(request.user) not in allowed:
                raise PermissionDenied(
                    'Your account does not have permission to open that page.'
                )
            return view(request, *args, **kwargs)

        return wrapped

    return decorator


def admin_required(view):
    return role_required('ADMIN')(view)


def student_only(view):
    """Allow STUDENT only. Send ADMIN to the admin dashboard.

    Administrators curate content and review accounts/activity; they do not
    use student study tools (AI generator, Study Buddy, review modes,
    exams, leaderboard, mastery marks).
    """
    @wraps(view)
    @login_required
    def wrapped(request, *args, **kwargs):
        if effective_role(request.user) == 'ADMIN':
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse(
                    {
                        'ok': False,
                        'error': 'Administrators cannot use student study tools.',
                    },
                    status=403,
                )
            return redirect('tupas:dashboard')
        return view(request, *args, **kwargs)

    return wrapped

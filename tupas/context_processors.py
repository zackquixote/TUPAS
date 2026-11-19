"""Template context: the effective TUPAS role for the signed-in user."""

from .services.rbac import effective_role


def role(request):
    if not getattr(request.user, 'is_authenticated', False):
        return {'tupas_role': None}
    return {'tupas_role': effective_role(request.user)}

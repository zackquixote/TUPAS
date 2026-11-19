"""Student registration and a login view with an IP-based attempt brake."""

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect, render

from ..forms import StudentRegistrationForm, TupasAuthenticationForm
from ..models import ActivityLog
from ..services.activity import mask_identifier, record_activity
from ..services.quotas import (
    clear_username_failures,
    login_attempt_quota,
    login_quota_message,
    record_username_failure,
    username_login_allowed,
)


class RateLimitedLoginView(LoginView):
    """Django login plus a per-IP attempt limit and a per-username lockout."""

    template_name = 'tupas/auth/login.html'
    authentication_form = TupasAuthenticationForm
    redirect_authenticated_user = True

    def post(self, request, *args, **kwargs):
        username = (request.POST.get('username') or '').strip()
        allowed, _remaining = login_attempt_quota(request)
        if not allowed:
            request._tupas_login_throttled = True
            record_activity(
                None,
                ActivityLog.Action.RATE_LIMITED,
                'login: IP attempt limit',
                request,
            )
            form = self.get_form()
            form.add_error(None, login_quota_message())
            return self.form_invalid(form)
        if username and not username_login_allowed(username):
            request._tupas_login_throttled = True
            record_activity(
                None,
                ActivityLog.Action.RATE_LIMITED,
                f'login: account lock {mask_identifier(username)}',
                request,
            )
            form = self.get_form()
            form.add_error(
                None,
                'Too many failed attempts for that account. '
                'Please wait a few minutes and try again.',
            )
            return self.form_invalid(form)
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        username = form.cleaned_data.get('username') or ''
        response = super().form_valid(form)
        clear_username_failures(username)
        return response

    def form_invalid(self, form):
        # Count credential failures for the username lockout. IP-throttled
        # and already-locked requests are not extra credential guesses.
        if not getattr(self.request, '_tupas_login_throttled', False):
            username = (self.request.POST.get('username') or '').strip()
            if username:
                record_username_failure(username)
        return super().form_invalid(form)


def register(request):
    if request.user.is_authenticated:
        return redirect('tupas:dashboard')

    if request.method == 'POST':
        form = StudentRegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            record_activity(
                user,
                ActivityLog.Action.REGISTER,
                user.username,
                request,
            )
            messages.success(request, f'Welcome to TUPAS, {user.profile.full_name}!')
            return redirect('tupas:dashboard')
    else:
        form = StudentRegistrationForm()

    return render(request, 'tupas/auth/register.html', {'form': form})

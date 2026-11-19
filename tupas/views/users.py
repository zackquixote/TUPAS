"""Admin user management: directory, roles (ADMIN/STUDENT), passwords, accounts.

Teachers were removed. The only roles are:

- ``ADMIN``  — ``is_staff`` or ``is_superuser``
- ``STUDENT`` — everyone else

Superusers cannot be demoted or have their role changed from the app.
The last remaining ADMIN cannot be demoted.
"""

from django import forms
from django.contrib import messages
from django.contrib.auth import password_validation
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.forms import ModelForm
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from ..models import ActivityLog, Deck, ExamAttempt, ItemProgress, StudentProfile, StudyDocument
from ..services.activity import record_activity
from ..services.gamification import BADGE_LABELS
from ..services.rbac import admin_required, effective_role


def _role_label(user):
    return effective_role(user) or 'STUDENT'


def _admin_count_excluding(user_pk=None):
    qs = User.objects.filter(Q(is_staff=True) | Q(is_superuser=True))
    if user_pk is not None:
        qs = qs.exclude(pk=user_pk)
    return qs.count()


class AdminUserForm(ModelForm):
    """Edit the account fields an admin is allowed to change."""

    role = forms.ChoiceField(
        choices=[('STUDENT', 'Student'), ('ADMIN', 'Admin')],
        initial='STUDENT',
    )

    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email', 'is_active']
        labels = {
            'first_name': 'First name',
            'last_name': 'Last name',
            'email': 'Email',
            'is_active': 'Account is active (can log in)',
        }

    def __init__(self, *args, initial_role=None, **kwargs):
        super().__init__(*args, **kwargs)
        if initial_role:
            self.fields['role'].initial = initial_role
        for field in self.fields.values():
            css = field.widget.attrs.get('class', '')
            field.widget.attrs['class'] = (css + ' form-control').strip()
        self.fields['is_active'].widget.attrs['class'] = 'form-check-input'


class AdminSetPasswordForm(forms.Form):
    new_password1 = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'autocomplete': 'new-password'}),
        label='New password',
    )
    new_password2 = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'autocomplete': 'new-password'}),
        label='Repeat new password',
    )

    def __init__(self, user, data=None, **kwargs):
        self.user = user
        super().__init__(data=data, **kwargs)

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get('new_password1') or ''
        p2 = cleaned.get('new_password2') or ''
        if p1 != p2:
            raise ValidationError('The two password fields did not match.')
        if p1:
            password_validation.validate_password(p1, user=self.user)
        return cleaned

    def save(self):
        self.user.set_password(self.cleaned_data['new_password1'])
        self.user.save(update_fields=['password'])


@never_cache
@admin_required
def user_list(request):
    """Searchable list of every account. Admins can open and edit them."""
    query = (request.GET.get('q') or '').strip()
    role_filter = (request.GET.get('role') or '').strip().upper()

    users = (
        User.objects
        .select_related('profile')
        .annotate(
            deck_count=Count('decks_created', distinct=True),
            document_count=Count('documents', distinct=True),
        )
        .order_by('-date_joined')
    )
    if query:
        users = users.filter(
            Q(username__icontains=query)
            | Q(email__icontains=query)
            | Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(profile__full_name__icontains=query)
            | Q(profile__school_name__icontains=query)
            | Q(profile__course_or_strand__icontains=query)
        )
    if role_filter == 'ADMIN':
        users = users.filter(Q(is_staff=True) | Q(is_superuser=True))
    elif role_filter == 'STUDENT':
        users = users.filter(is_staff=False, is_superuser=False)

    paginator = Paginator(users, 25)
    page = paginator.get_page(request.GET.get('page'))

    rows = []
    for user in page.object_list:
        profile = getattr(user, 'profile', None)
        rows.append({
            'user': user,
            'profile': profile,
            'role': _role_label(user),
            'deck_count': user.deck_count,
            'document_count': user.document_count,
            'xp_points': profile.xp_points if profile else 0,
            'level': profile.level if profile else 1,
            'current_streak': profile.current_streak if profile else 0,
            'school_name': profile.school_name if profile else '',
            'course_or_strand': profile.course_or_strand if profile else '',
            'is_active': user.is_active,
        })

    return render(request, 'tupas/users.html', {
        'page': page,
        'rows': rows,
        'search_q': query,
        'role_filter': role_filter,
        'is_admin_view': True,
        'total_users': users.count(),
    })


@never_cache
@admin_required
def user_detail(request, user_id):
    """Admin view of one account, with role / password / profile controls."""
    target = get_object_or_404(
        User.objects.select_related('profile'),
        pk=user_id,
    )
    profile = getattr(target, 'profile', None)
    mastered = ItemProgress.objects.filter(
        user=target, mastery_status=ItemProgress.MasteryStatus.MASTERED,
    ).count()
    needs_review = ItemProgress.objects.filter(
        user=target, mastery_status=ItemProgress.MasteryStatus.NEEDS_REVIEW,
    ).count()
    exams = ExamAttempt.objects.filter(user=target).order_by('-date_taken')[:10]
    decks = Deck.objects.filter(created_by=target).select_related('subject')[:12]
    documents = StudyDocument.objects.filter(user=target).order_by('-created_at')[:8]

    role_initial = 'ADMIN' if (target.is_staff or target.is_superuser) else 'STUDENT'
    account_form = AdminUserForm(instance=target, initial={'role': role_initial})
    password_form = AdminSetPasswordForm(user=target)
    can_edit_account = True
    can_change_role = not target.is_superuser
    can_edit_password = True

    return render(request, 'tupas/user_detail.html', {
        'target_user': target,
        'target_profile': profile,
        'target_role': _role_label(target),
        'mastered': mastered,
        'needs_review': needs_review,
        'exams': exams,
        'decks': decks,
        'documents': documents,
        'can_change_role': can_change_role,
        'can_edit_account': can_edit_account,
        'can_edit_password': can_edit_password,
        'account_form': account_form,
        'password_form': password_form,
        'badge_labels': BADGE_LABELS,
    })


@admin_required
@require_POST
def set_user_role(request, user_id):
    """Set a non-superuser to STUDENT or ADMIN (is_staff)."""
    target = get_object_or_404(User.objects.select_related('profile'), pk=user_id)
    if target.is_superuser:
        messages.error(request, 'Superusers keep the ADMIN role.')
        return redirect('tupas:user_detail', user_id=target.id)

    new_role = (request.POST.get('role') or '').strip().upper()
    if new_role not in {'STUDENT', 'ADMIN'}:
        messages.error(request, 'Choose STUDENT or ADMIN.')
        return redirect('tupas:user_detail', user_id=target.id)

    if target.is_staff and new_role == 'STUDENT' and _admin_count_excluding(target.pk) == 0:
        messages.error(request, 'Cannot remove the last remaining ADMIN.')
        return redirect('tupas:user_detail', user_id=target.id)

    target.is_staff = new_role == 'ADMIN'
    target.save(update_fields=['is_staff'])
    profile, _ = StudentProfile.objects.get_or_create(
        user=target,
        defaults={
            'full_name': target.get_full_name() or target.username,
            'student_level': StudentProfile.StudentLevel.COLLEGE,
            'school_name': '',
            'course_or_strand': '',
            'year_level': '',
            'region': '',
            'province': '',
            'city_municipality': '',
        },
    )
    if profile.role != 'STUDENT':
        profile.role = 'STUDENT'
        profile.save(update_fields=['role'])

    record_activity(
        request.user,
        ActivityLog.Action.ROLE_CHANGE,
        f'{target.username} → {new_role}',
        request,
    )
    if new_role == 'ADMIN':
        messages.success(request, f'{target.username} is now an ADMIN.')
    else:
        messages.success(request, f'{target.username} is now a STUDENT.')
    return redirect('tupas:user_detail', user_id=target.id)


@admin_required
@require_POST
def admin_edit_account(request, user_id):
    """Edit name, email, active flag, and role."""
    target = get_object_or_404(User, pk=user_id)
    form = AdminUserForm(request.POST, instance=target)
    if not form.is_valid():
        for errors in form.errors.values():
            for err in errors:
                messages.error(request, err)
        return redirect('tupas:user_detail', user_id=target.id)

    new_role = form.cleaned_data.get('role') or 'STUDENT'
    if target.is_superuser:
        # Keep superusers ADMIN; still allow name/email/active.
        target = form.save(commit=False)
        target.is_staff = True
        target.save()
        messages.success(request, f'Account updated for {target.username}.')
        return redirect('tupas:user_detail', user_id=target.id)

    if target.is_staff and new_role == 'STUDENT' and _admin_count_excluding(target.pk) == 0:
        messages.error(request, 'Cannot remove the last remaining ADMIN.')
        return redirect('tupas:user_detail', user_id=target.id)

    target = form.save(commit=False)
    target.is_staff = new_role == 'ADMIN'
    target.save()
    profile, _ = StudentProfile.objects.get_or_create(
        user=target,
        defaults={
            'full_name': target.get_full_name() or target.username,
            'student_level': StudentProfile.StudentLevel.COLLEGE,
            'school_name': '',
            'course_or_strand': '',
            'year_level': '',
            'region': '',
            'province': '',
            'city_municipality': '',
        },
    )
    if profile.role != 'STUDENT':
        profile.role = 'STUDENT'
        profile.save(update_fields=['role'])

    record_activity(
        request.user,
        ActivityLog.Action.ROLE_CHANGE,
        f'{target.username} → {new_role}',
        request,
    )
    messages.success(request, f'Account updated for {target.username}.')
    return redirect('tupas:user_detail', user_id=target.id)


@admin_required
@require_POST
def admin_set_password(request, user_id):
    """Admin sets a new password for this account (no old password needed)."""
    target = get_object_or_404(User, pk=user_id)
    form = AdminSetPasswordForm(user=target, data=request.POST)
    if not form.is_valid():
        for errors in form.errors.values():
            for err in errors:
                messages.error(request, err)
        return redirect('tupas:user_detail', user_id=target.id)

    form.save()
    record_activity(
        request.user,
        ActivityLog.Action.PASSWORD_CHANGE,
        f'admin reset password for {target.username}',
        request,
    )
    messages.success(request, f'Password updated for {target.username}.')
    return redirect('tupas:user_detail', user_id=target.id)

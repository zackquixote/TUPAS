"""Student profile page opened from the sidebar profile button."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth import update_session_auth_hash
from django.shortcuts import redirect, render
from django.db.models import Avg
from django.views.decorators.cache import never_cache

from ..forms import StudentProfileUpdateForm, TupasPasswordChangeForm
from ..models import ActivityLog, Deck, ExamAttempt, ItemProgress, StudentProfile, StudyDocument
from ..services.activity import record_activity
from ..services.quotas import password_change_quota, password_quota_message
from ..services.streaks import manila_today


@login_required
@never_cache
def profile(request):
    """Show and edit the signed-in student's profile, stats, and password."""
    user = request.user
    profile, _created = StudentProfile.objects.get_or_create(
        user=user,
        defaults={
            'full_name': user.get_full_name() or user.username,
            'student_level': StudentProfile.StudentLevel.COLLEGE,
            'school_name': '',
            'course_or_strand': '',
            'year_level': '',
            'region': '',
            'province': '',
            'city_municipality': '',
        },
    )
    today = manila_today()

    deck_count = (
        Deck.objects.count()
        if (user.is_staff or user.is_superuser)
        else Deck.objects.filter(created_by=user).count()
    )
    mastered_count = ItemProgress.objects.filter(
        user=user, mastery_status=ItemProgress.MasteryStatus.MASTERED,
    ).count()
    review_count = ItemProgress.objects.filter(
        user=user, mastery_status=ItemProgress.MasteryStatus.NEEDS_REVIEW,
    ).count()
    exams_taken = ExamAttempt.objects.filter(user=user).count()
    avg_exam_score = ExamAttempt.objects.filter(user=user).aggregate(
        average=Avg('percentage'),
    )['average']

    my_decks = (
        Deck.objects.filter(created_by=user).select_related('subject')[:8]
    )
    my_documents = StudyDocument.objects.filter(user=user).order_by('-created_at')[:5]

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'update_profile':
            profile_form = StudentProfileUpdateForm(
                request.POST,
                instance=profile,
                user=user,
            )
            password_form = TupasPasswordChangeForm(user)
            if profile_form.is_valid():
                profile_form.save()
                messages.success(request, 'Your profile has been updated!')
                return redirect('tupas:profile')
        elif action == 'change_password':
            if not password_change_quota(user)[0]:
                messages.error(request, password_quota_message())
                return redirect('tupas:profile')
            profile_form = StudentProfileUpdateForm(instance=profile, user=user)
            password_form = TupasPasswordChangeForm(user, request.POST)
            if password_form.is_valid():
                user = password_form.save()
                update_session_auth_hash(request, user)
                record_activity(
                    request.user,
                    ActivityLog.Action.PASSWORD_CHANGE,
                    request.user.username,
                    request,
                )
                messages.success(request, 'Password updated successfully!')
                return redirect('tupas:profile')
        else:
            profile_form = StudentProfileUpdateForm(instance=profile, user=user)
            password_form = TupasPasswordChangeForm(user)
            messages.warning(request, 'Unknown profile action.')
    else:
        profile_form = StudentProfileUpdateForm(instance=profile, user=user)
        password_form = TupasPasswordChangeForm(user)

    display_name = profile.full_name if profile else user.username
    initial = (display_name or user.username or 'U').strip()[:1].upper() or 'U'

    context = {
        'profile': profile,
        'display_name': display_name,
        'initial': initial,
        'profile_form': profile_form,
        'password_form': password_form,
        'deck_count': deck_count,
        'mastered': mastered_count,
        'mastered_count': mastered_count,
        'needs_review': review_count,
        'review_count': review_count,
        'exams_taken': exams_taken,
        'exam_count': exams_taken,
        'avg_exam_score': avg_exam_score,
        'my_decks': my_decks,
        'my_documents': my_documents,
        'current_streak': profile.current_streak if profile else 0,
        'longest_streak': profile.longest_streak if profile else 0,
        'daily_goal': profile.daily_goal if profile else 10,
        'studied_today': bool(profile and profile.last_study_date == today),
        'today': today,
        'xp_points': profile.xp_points if profile else 0,
        'level': profile.level if profile else 1,
        'xp_into_level': profile.xp_into_level if profile else 0,
        'badges': profile.badges if profile else [],
        'exam_countdown': (
            (profile.target_exam_date - today).days
            if profile and profile.target_exam_date else None
        ),
    }
    return render(request, 'tupas/profile.html', context)

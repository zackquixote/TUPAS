"""XP, levels, badges, and leaderboard helpers."""

import json

from django.db.models import Sum
from django.db.models import Q

from ..models import ExamAttempt, ItemProgress, StudentProfile

BADGE_LABELS = {
    'first_card': '🃏 First Card',
    'master_25': '🏅 25 Mastered',
    'master_100': '🏆 100 Mastered',
    'exam_ace': '🎯 Exam Ace',
    'ai_creator': '🤖 AI Creator',
    'streak_7': '🔥 7-Day Streak',
}

XP_GOOD = 10
XP_AGAIN = 5
XP_HARD = 5
XP_EXAM_BASE = 40
XP_EXAM_ACE_BONUS = 25


def award_xp(user, amount):
    """Add XP to the student's profile and return the updated profile."""
    profile = getattr(user, 'profile', None)
    if profile is None or amount <= 0:
        return profile
    profile.xp_points = (profile.xp_points or 0) + int(amount)
    profile.save(update_fields=['xp_points'])
    unlock_badges(profile)
    return profile


def unlock_badges(profile):
    """Grant any newly earned badges. Returns the full badge list."""
    earned = set(profile.badges)
    mastered = ItemProgress.objects.filter(
        user=profile.user,
        mastery_status=ItemProgress.MasteryStatus.MASTERED,
    ).count()
    exams = ExamAttempt.objects.filter(user=profile.user)

    if mastered >= 1:
        earned.add('first_card')
    if mastered >= 25:
        earned.add('master_25')
    if mastered >= 100:
        earned.add('master_100')
    if exams.filter(percentage__gte=90).exists():
        earned.add('exam_ace')
    if (profile.current_streak or 0) >= 7:
        earned.add('streak_7')

    # ai_creator is granted by the AI views when a generation succeeds.
    new = sorted(earned - set(profile.badges))
    if new:
        profile.badges_json = json.dumps(sorted(earned))
        profile.save(update_fields=['badges_json'])
    return profile.badges


def grant_badge(profile, badge_id):
    """Force-grant a badge (used for ai_creator)."""
    if badge_id not in BADGE_LABELS:
        return profile.badges
    earned = set(profile.badges)
    if badge_id not in earned:
        earned.add(badge_id)
        profile.badges_json = json.dumps(sorted(earned))
        profile.save(update_fields=['badges_json'])
    return profile.badges


def exam_xp(percentage):
    """+40 for finishing an exam, +25 more if the score is at least 90%."""
    amount = XP_EXAM_BASE
    if percentage is not None and percentage >= 90:
        amount += XP_EXAM_ACE_BONUS
    return amount


def leaderboard(scope='overall', user=None):
    """Return opt-in students ranked by XP.

    Admins and staff members are strictly excluded so the leaderboard reflects
    genuine student achievements.
    ``scope`` is ``overall``, ``course``, or ``school``. Course/school
    scopes use the viewer's own profile values.
    """
    qs = StudentProfile.objects.filter(
        show_on_leaderboard=True,
        user__is_staff=False,
        user__is_superuser=False,
        user__is_active=True,
    ).select_related('user')
    profile = getattr(user, 'profile', None) if user is not None else None

    if scope == 'course' and profile and profile.course_or_strand.strip():
        qs = qs.filter(
            Q(course_or_strand__icontains=profile.course_or_strand.strip())
            | Q(user__username=profile.user.username),
        )
    elif scope == 'school' and profile and profile.school_name.strip():
        qs = qs.filter(
            Q(school_name__icontains=profile.school_name.strip())
            | Q(user__username=profile.user.username),
        )

    return list(
        qs.order_by('-xp_points', '-longest_streak', 'full_name')[:50],
    )

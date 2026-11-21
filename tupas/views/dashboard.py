"""Dashboard with personalized deck recommendations, streak, and study stats.

Administrators are sent to a separate admin dashboard (accounts, activity
log, platform stats). They do not share the student study dashboard.
"""

import csv
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.paginator import Paginator
from django.db.models import Avg, Count, Q
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.http import urlencode
from django.views.decorators.cache import never_cache

from ..models import (
    ActivityLog,
    Deck,
    ExamAttempt,
    ItemProgress,
    ReviewItem,
    StudentProfile,
    StudyDocument,
    Subject,
)
from ..services.rbac import is_admin
from ..services.streaks import manila_now, manila_today


def _get_profile(user):
    """Return the student profile if it exists, otherwise None."""
    return getattr(user, 'profile', None)


def _filtered_activity(request):
    """ActivityLog queryset shaped by the dashboard filters."""
    activity = ActivityLog.objects.select_related('user').order_by('-created_at')
    action = (request.GET.get('action') or '').strip()
    username = (request.GET.get('user') or '').strip()
    query = (request.GET.get('q') or '').strip()
    if action:
        activity = activity.filter(action=action)
    if username:
        activity = activity.filter(
            Q(username_snapshot__icontains=username)
            | Q(user__username__icontains=username)
        )
    if query:
        activity = activity.filter(
            Q(detail__icontains=query) | Q(ip_address__icontains=query)
        )
    return activity, action, username, query


def _activity_csv(request, activity):
    """Stream the filtered trail as CSV (capped so a stray export cannot OOM)."""
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="tupas-activity.csv"'
    writer = csv.writer(response)
    writer.writerow([
        'when_utc', 'username', 'action', 'detail', 'ip_address',
        'request_id', 'user_agent',
    ])
    for row in activity[:10000].iterator(chunk_size=500):
        writer.writerow([
            row.created_at.isoformat(),
            row.username_snapshot or (row.user.username if row.user else ''),
            row.action,
            row.detail,
            row.ip_address or '',
            row.request_id,
            row.user_agent,
        ])
    return response


@never_cache
def admin_dashboard(request):
    """Admin home: accounts, activity log, platform stats. No student tools."""
    day_start = manila_now().replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = timezone.now() - timedelta(days=7)

    total_users = User.objects.count()
    admin_count = User.objects.filter(Q(is_staff=True) | Q(is_superuser=True)).count()
    student_count = max(0, total_users - admin_count)
    active_count = User.objects.filter(is_active=True).count()

    logins_today = ActivityLog.objects.filter(
        action=ActivityLog.Action.LOGIN,
        created_at__gte=day_start,
    ).count()
    failed_today = ActivityLog.objects.filter(
        action=ActivityLog.Action.LOGIN_FAILED,
        created_at__gte=day_start,
    ).count()
    exams_today = ExamAttempt.objects.filter(date_taken__gte=day_start).count()
    new_this_week = User.objects.filter(date_joined__gte=week_ago).count()

    platform = {
        'subjects': Subject.objects.count(),
        'decks': Deck.objects.count(),
        'items': ReviewItem.objects.count(),
        'documents': StudyDocument.objects.count(),
        'exams': ExamAttempt.objects.count(),
    }

    recent_users = (
        User.objects.select_related('profile').order_by('-date_joined')[:8]
    )
    recent_exams = (
        ExamAttempt.objects.select_related('user', 'deck').order_by('-date_taken')[:8]
    )

    activity, action, username, query = _filtered_activity(request)
    if request.GET.get('export') == 'csv':
        return _activity_csv(request, activity)

    paginator = Paginator(activity, 20)
    page = paginator.get_page(request.GET.get('page'))
    keep = {
        key: value
        for key, value in request.GET.items()
        if key != 'page' and value
    }
    return render(request, 'tupas/admin_dashboard.html', {
        'total_users': total_users,
        'admin_count': admin_count,
        'active_count': active_count,
        'student_count': student_count,
        'new_this_week': new_this_week,
        'logins_today': logins_today,
        'failed_today': failed_today,
        'exams_today': exams_today,
        'platform': platform,
        'recent_users': recent_users,
        'recent_exams': recent_exams,
        'page': page,
        'activity_rows': page.object_list,
        'action_choices': ActivityLog.Action.choices,
        'selected_action': action,
        'user_filter': username,
        'search_q': query,
        'filter_qs': urlencode(keep),
    })


@login_required
@never_cache
def dashboard(request):
    if is_admin(request.user):
        return admin_dashboard(request)

    profile = _get_profile(request.user)

    view = (request.GET.get('view') or 'active').strip().lower()
    query = (request.GET.get('q') or '').strip()
    tag = (request.GET.get('tag') or '').strip()

    decks = (
        Deck.objects
        .select_related('subject', 'created_by')
        .annotate(item_count=Count('items'))
    )
    if view == 'archived':
        decks = decks.filter(is_archived=True)
    else:
        view = 'active'
        decks = decks.filter(is_archived=False)

    if query:
        decks = decks.filter(
            Q(title__icontains=query)
            | Q(description__icontains=query)
            | Q(subject__subject_name__icontains=query)
            | Q(tags__icontains=query)
        )
    if tag:
        decks = decks.filter(tags__icontains=tag)

    recommended_decks = []
    recommended_ids = set()
    if profile and profile.course_or_strand.strip():
        course = profile.course_or_strand.strip()
        recommendation_filter = (
            Q(subject__target_course__icontains=course)
            | Q(subject__subject_name__icontains=course)
            | Q(title__icontains=course)
            | Q(description__icontains=course)
        )
        recommended_query = (
            decks.filter(recommendation_filter)
            .filter(is_archived=False)
            .distinct()
        )
        recommended_ids = set(recommended_query.values_list('pk', flat=True))
        recommended_decks = list(recommended_query[:6])
        for deck in recommended_decks:
            deck.is_recommended = True

    # Pinned first, then newest.
    deck_list = list(decks.order_by('-is_pinned', '-created_at'))
    for deck in deck_list:
        deck.is_recommended = deck.pk in recommended_ids

    progress = ItemProgress.objects.filter(user=request.user)
    attempts = ExamAttempt.objects.filter(user=request.user)

    now = timezone.now()
    today = manila_today()
    day_start = manila_now().replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)

    due_count = progress.filter(next_review_at__lte=now).count()
    today_reviewed = progress.filter(
        last_reviewed__gte=day_start,
        last_reviewed__lt=day_end,
    ).count()

    daily_goal = max(1, profile.daily_goal) if profile else 10
    goal_percent = min(100, round((today_reviewed / daily_goal) * 100))

    due_deck = (
        Deck.objects
        .filter(
            items__item_type=ReviewItem.ItemType.FLIP_CARD,
            items__progress_records__user=request.user,
            items__progress_records__next_review_at__lte=now,
        )
        .distinct()
        .first()
    )

    mastered = progress.filter(mastery_status=ItemProgress.MasteryStatus.MASTERED).count()
    needs_review = progress.filter(mastery_status=ItemProgress.MasteryStatus.NEEDS_REVIEW).count()
    average_score = attempts.aggregate(average=Avg('percentage'))['average']

    exam_countdown = None
    if profile and profile.target_exam_date:
        delta = profile.target_exam_date - today
        exam_countdown = delta.days

    stats = {
        'deck_count': len(deck_list),
        'mastered': mastered,
        'needs_review': needs_review,
        'average_score': average_score,
        'xp_points': profile.xp_points if profile else 0,
        'level': profile.level if profile else 1,
        'badges': profile.badges if profile else [],
    }

    # Live activity feed: recent actions from this student's cards + decks.
    my_activity = ActivityLog.objects.filter(
        Q(username_snapshot=request.user.username)
        | Q(user=request.user)
    ).order_by('-created_at')[:8]

    return render(request, 'tupas/dashboard.html', {
        'profile': profile,
        'decks': deck_list,
        'recommended_decks': recommended_decks,
        'recommended_ids': recommended_ids,
        'stats': stats,
        'recent_attempts': attempts.select_related('deck')[:5],
        'due_count': due_count,
        'due_deck': due_deck,
        'today_reviewed': today_reviewed,
        'daily_goal': daily_goal,
        'goal_percent': goal_percent,
        'current_streak': profile.current_streak if profile else 0,
        'longest_streak': profile.longest_streak if profile else 0,
        'studied_today': bool(profile and profile.last_study_date == today),
        'view': view,
        'search_q': query,
        'active_tag': tag,
        'exam_countdown': exam_countdown,
        'recent_activity': my_activity,
    })

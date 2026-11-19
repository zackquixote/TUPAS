"""Opt-in XP leaderboard (overall / my course / my school)."""

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.views.decorators.cache import never_cache

from ..services.gamification import BADGE_LABELS
from ..services.gamification import leaderboard as leaderboard_rows
from ..services.rbac import student_only


@student_only
@never_cache
def leaderboard(request):
    scope = (request.GET.get('scope') or 'overall').strip().lower()
    if scope not in {'overall', 'course', 'school'}:
        scope = 'overall'
    rows = leaderboard_rows(scope, user=request.user)
    return render(request, 'tupas/leaderboard.html', {
        'scope': scope,
        'rows': rows,
        'badge_labels': BADGE_LABELS,
    })

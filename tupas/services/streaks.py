"""Daily study streak tracking on the Asia/Manila calendar."""

from datetime import timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

MANILA = ZoneInfo('Asia/Manila')


def manila_now():
    return timezone.now().astimezone(MANILA)


def manila_today():
    return manila_now().date()


def record_study_day(user, day=None):
    """Advance the user's streak once per Manila calendar day.

    Consecutive days increment the streak; a missed day starts it over at 1.
    Users without a student profile (for example staff) are left untouched.
    """
    profile = getattr(user, 'profile', None)
    if profile is None:
        return None

    today = day or manila_today()
    last = profile.last_study_date

    if last == today:
        return profile
    if last == today - timedelta(days=1):
        profile.current_streak = (profile.current_streak or 0) + 1
    else:
        profile.current_streak = 1

    profile.longest_streak = max(profile.longest_streak or 0, profile.current_streak)
    profile.last_study_date = today
    profile.save(update_fields=['current_streak', 'longest_streak', 'last_study_date'])
    return profile

"""Student demographic and academic profile (table 2)."""

import json

from django.conf import settings
from django.db import models


class StudentProfile(models.Model):
    """Table 2: demographic and academic profile of a student."""

    class StudentLevel(models.TextChoices):
        JHS = 'JHS', 'Junior High School'
        SHS = 'SHS', 'Senior High School'
        COLLEGE = 'COLLEGE', 'College'
        BOARD = 'BOARD', 'Board Exam'

    class Role(models.TextChoices):
        STUDENT = 'STUDENT', 'Student'

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='profile',
    )
    full_name = models.CharField(max_length=200)
    birthdate = models.DateField(null=True, blank=True)
    region = models.CharField(max_length=100)
    province = models.CharField(max_length=100)
    city_municipality = models.CharField(max_length=100)
    school_name = models.CharField(max_length=200)
    student_level = models.CharField(max_length=50, choices=StudentLevel.choices)
    course_or_strand = models.CharField(max_length=150)
    year_level = models.CharField(max_length=50)
    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default='STUDENT',
        help_text='ADMIN is any User with is_staff or is_superuser. TEACHER was removed.',
    )
    current_streak = models.PositiveIntegerField(default=0)
    longest_streak = models.PositiveIntegerField(default=0)
    last_study_date = models.DateField(null=True, blank=True)
    daily_goal = models.PositiveIntegerField(default=10)
    xp_points = models.PositiveIntegerField(default=0)
    badges_json = models.TextField(blank=True, default='[]')
    show_on_leaderboard = models.BooleanField(default=True)
    target_exam_name = models.CharField(max_length=150, blank=True)
    target_exam_date = models.DateField(null=True, blank=True)

    class Meta:
        verbose_name = 'student profile'
        verbose_name_plural = 'student profiles'
        ordering = ['full_name']

    def __str__(self):
        return f'{self.full_name} ({self.user.username})'

    @property
    def level(self):
        """Level 1 at 0 XP, then one level per 100 XP."""
        return 1 + (self.xp_points // 100)

    @property
    def xp_into_level(self):
        return self.xp_points % 100

    @property
    def badges(self):
        try:
            parsed = json.loads(self.badges_json or '[]')
        except (TypeError, ValueError):
            return []
        return parsed if isinstance(parsed, list) else []

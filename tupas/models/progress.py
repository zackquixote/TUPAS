"""Mastery tracking and exam history tables."""

from django.conf import settings
from django.db import models

from .content import Deck, ReviewItem


class ItemProgress(models.Model):
    """Table 8: per-student mastery tracker."""

    class MasteryStatus(models.TextChoices):
        MASTERED = 'MASTERED', 'Got It'
        NEEDS_REVIEW = 'NEEDS_REVIEW', 'Study Again'

    class Confidence(models.TextChoices):
        AGAIN = 'AGAIN', 'Again'
        HARD = 'HARD', 'Hard'
        GOOD = 'GOOD', 'Good'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='item_progress',
    )
    review_item = models.ForeignKey(
        ReviewItem,
        on_delete=models.CASCADE,
        related_name='progress_records',
    )
    mastery_status = models.CharField(
        max_length=20,
        choices=MasteryStatus.choices,
    )
    confidence_level = models.CharField(
        max_length=20,
        default=Confidence.GOOD,
        choices=Confidence.choices,
    )
    last_reviewed = models.DateTimeField(auto_now=True)
    next_review_at = models.DateTimeField(null=True, blank=True, db_index=True)
    review_count = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ('user', 'review_item')
        ordering = ['-last_reviewed']
        verbose_name = 'item progress'
        verbose_name_plural = 'item progress'

    def __str__(self):
        return f'{self.user.username} — {self.review_item} — {self.get_mastery_status_display()}'


class ExamAttempt(models.Model):
    """Table 9: quiz scores and history."""

    class ExamMode(models.TextChoices):
        MULTIPLE_CHOICE = 'MULTIPLE_CHOICE', 'Multiple Choice'
        IDENTIFICATION = 'IDENTIFICATION', 'Identification'
        ENUMERATION = 'ENUMERATION', 'Enumeration'
        MIXED = 'MIXED', 'Mixed'
        TRUE_FALSE = 'TRUE_FALSE', 'True or False'
        MATCHING = 'MATCHING', 'Matching Type (Column A ↔ B)'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='exam_attempts',
    )
    deck = models.ForeignKey(
        Deck,
        on_delete=models.CASCADE,
        related_name='exam_attempts',
    )
    exam_mode = models.CharField(max_length=50, choices=ExamMode.choices)
    score = models.IntegerField()
    total_items = models.IntegerField()
    percentage = models.DecimalField(max_digits=5, decimal_places=2)
    details_json = models.TextField(blank=True, default='[]')
    date_taken = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date_taken']
        verbose_name = 'exam attempt'
        verbose_name_plural = 'exam attempts'

    def __str__(self):
        return f'{self.user.username} — {self.deck.title} — {self.percentage}%'

    @property
    def mode_slug(self):
        return {
            self.ExamMode.MULTIPLE_CHOICE: 'multiple-choice',
            self.ExamMode.IDENTIFICATION: 'identification',
            self.ExamMode.ENUMERATION: 'enumeration',
            self.ExamMode.MIXED: 'mixed',
            self.ExamMode.TRUE_FALSE: 'true-false',
            self.ExamMode.MATCHING: 'matching',
        }.get(self.exam_mode, 'mixed')

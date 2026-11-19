"""Subjects, decks, and the multi-modal review item tables."""

from django.conf import settings
from django.db import models


class Subject(models.Model):
    """Table 3: course/category grouping for reviewer decks."""

    subject_name = models.CharField(max_length=150)
    target_course = models.CharField(max_length=150, blank=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ['subject_name']

    def __str__(self):
        return self.subject_name


class Deck(models.Model):
    """Table 4: a reviewer set belonging to a subject."""

    subject = models.ForeignKey(
        Subject,
        on_delete=models.CASCADE,
        related_name='decks',
    )
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='decks_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    tags = models.CharField(
        max_length=200,
        blank=True,
        help_text='Comma-separated tags, e.g. midterm, finals, major.',
    )
    is_pinned = models.BooleanField(default=False)
    is_archived = models.BooleanField(default=False)

    class Meta:
        ordering = ['-is_pinned', '-created_at']

    def __str__(self):
        return self.title


class ReviewItem(models.Model):
    """Table 5: core multi-modal review row."""

    class ItemType(models.TextChoices):
        FLIP_CARD = 'FLIP_CARD', 'Flip Card'
        HIGHLIGHT_NOTE = 'HIGHLIGHT_NOTE', 'Highlight Note'
        ENUMERATION = 'ENUMERATION', 'Enumeration'

    deck = models.ForeignKey(
        Deck,
        on_delete=models.CASCADE,
        related_name='items',
    )
    item_type = models.CharField(
        max_length=30,
        choices=ItemType.choices,
        default=ItemType.FLIP_CARD,
    )
    prompt_text = models.TextField(
        help_text=(
            'Front question for Flip Card, a paragraph with [[keywords]] for '
            'Highlight Note, or a list prompt for Enumeration.'
        ),
    )
    main_answer = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text='Back answer for Flip Card / Identification.',
    )
    explanation = models.TextField(blank=True)
    is_strict_order = models.BooleanField(
        default=False,
        help_text='Used when item_type is ENUMERATION.',
    )
    quality_flag = models.CharField(
        max_length=20,
        blank=True,
        default='',
        choices=[
            ('', 'None'),
            ('GOOD', 'Helpful'),
            ('FLAGGED', 'Needs Fix'),
        ],
    )

    class Meta:
        ordering = ['id']

    def __str__(self):
        excerpt = self.prompt_text[:60]
        return f'[{self.get_item_type_display()}] {excerpt}'


class EnumerationItem(models.Model):
    """Table 6: ordered/unordered accepted answers for an enumeration item."""

    review_item = models.ForeignKey(
        ReviewItem,
        on_delete=models.CASCADE,
        related_name='enum_answers',
    )
    answer_text = models.CharField(max_length=255)
    order_number = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ['order_number', 'id']
        verbose_name = 'enumeration item'
        verbose_name_plural = 'enumeration items'

    def __str__(self):
        return f'{self.order_number}. {self.answer_text}'


class QuizChoice(models.Model):
    """Table 7: multiple-choice options for Exam Simulator mode."""

    review_item = models.ForeignKey(
        ReviewItem,
        on_delete=models.CASCADE,
        related_name='choices',
    )
    choice_text = models.CharField(max_length=255)
    is_correct = models.BooleanField(default=False)

    class Meta:
        ordering = ['id']
        verbose_name = 'quiz choice'
        verbose_name_plural = 'quiz choices'

    def __str__(self):
        marker = ' *' if self.is_correct else ''
        return f'{self.choice_text}{marker}'

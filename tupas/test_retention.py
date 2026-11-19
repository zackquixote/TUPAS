"""Tests for spaced repetition, daily streaks, session recaps, and accessibility."""

import json
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from .models import ItemProgress
from .services.spaced_repetition import schedule_next_review
from .tests import TupasBaseTestCase


class SpacedRepetitionTests(TupasBaseTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def _mark(self, item=None, status='MASTERED'):
        return self.client.post(
            reverse('tupas:mark_progress', args=[(item or self.flip).id]),
            data=json.dumps({'status': status}),
            content_type='application/json',
        )

    def test_mastered_advances_through_interval_ladder(self):
        progress = ItemProgress.objects.create(
            user=self.user,
            review_item=self.flip,
            mastery_status=ItemProgress.MasteryStatus.MASTERED,
        )
        now = timezone.now()

        for expected_count, days in enumerate((1, 3, 7, 21, 60), start=1):
            schedule_next_review(
                progress,
                ItemProgress.MasteryStatus.MASTERED,
                now=now,
            )
            progress.refresh_from_db()
            self.assertEqual(progress.review_count, expected_count)
            self.assertEqual(progress.next_review_at, now + timedelta(days=days))

    def test_interval_ladder_caps_at_sixty_days(self):
        progress = ItemProgress.objects.create(
            user=self.user,
            review_item=self.flip,
            mastery_status=ItemProgress.MasteryStatus.MASTERED,
        )
        now = timezone.now()

        for _ in range(7):
            schedule_next_review(
                progress,
                ItemProgress.MasteryStatus.MASTERED,
                now=now,
            )

        progress.refresh_from_db()
        self.assertEqual(progress.review_count, 7)
        self.assertEqual(progress.next_review_at, now + timedelta(days=60))

    def test_needs_review_restarts_the_ladder(self):
        progress = ItemProgress.objects.create(
            user=self.user,
            review_item=self.flip,
            mastery_status=ItemProgress.MasteryStatus.MASTERED,
        )
        now = timezone.now()

        schedule_next_review(progress, ItemProgress.MasteryStatus.MASTERED, now=now)
        schedule_next_review(progress, ItemProgress.MasteryStatus.MASTERED, now=now)
        schedule_next_review(progress, ItemProgress.MasteryStatus.NEEDS_REVIEW, now=now)

        progress.refresh_from_db()
        self.assertEqual(progress.review_count, 0)
        self.assertEqual(progress.next_review_at, now + timedelta(days=1))

    def test_mark_progress_persists_schedule_and_session_recap(self):
        response = self._mark()
        payload = response.json()

        progress = ItemProgress.objects.get(user=self.user, review_item=self.flip)
        self.assertEqual(progress.review_count, 1)
        self.assertGreater(progress.next_review_at, timezone.now())
        self.assertEqual(payload['review_count'], 1)
        self.assertEqual(payload['session']['reviewed'], 1)
        self.assertEqual(payload['session']['got_it_percent'], 100)
        self.assertFalse(payload['session']['completed'])

    def test_session_recap_completes_after_every_card_is_marked(self):
        self._mark(self.flip)
        payload = self._mark(self.identification).json()

        self.assertEqual(payload['session']['reviewed'], 2)
        self.assertTrue(payload['session']['completed'])

    def test_due_filter_returns_only_due_cards(self):
        now = timezone.now()
        ItemProgress.objects.create(
            user=self.user,
            review_item=self.flip,
            mastery_status=ItemProgress.MasteryStatus.MASTERED,
            next_review_at=now - timedelta(hours=1),
        )
        ItemProgress.objects.create(
            user=self.user,
            review_item=self.identification,
            mastery_status=ItemProgress.MasteryStatus.MASTERED,
            next_review_at=now + timedelta(days=3),
        )

        response = self.client.get(
            reverse('tupas:flip_mode', args=[self.deck.id]) + '?filter=due'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['current_filter'], 'due')
        self.assertEqual(
            [item.id for item in response.context['items']],
            [self.flip.id],
        )
        self.assertEqual(response.context['due_count'], 1)
        self.assertContains(response, 'Due Today')

    def test_dashboard_reports_due_cards_and_start_review(self):
        ItemProgress.objects.create(
            user=self.user,
            review_item=self.flip,
            mastery_status=ItemProgress.MasteryStatus.NEEDS_REVIEW,
            next_review_at=timezone.now() - timedelta(minutes=5),
        )

        response = self.client.get(reverse('tupas:dashboard'))

        self.assertEqual(response.context['due_count'], 1)
        self.assertEqual(response.context['due_deck'], self.deck)
        self.assertContains(response, 'Start Review')


class StreakTests(TupasBaseTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)
        self.today = timezone.localdate()

    def _mark(self):
        return self.client.post(
            reverse('tupas:mark_progress', args=[self.flip.id]),
            data=json.dumps({'status': 'MASTERED'}),
            content_type='application/json',
        )

    def test_streak_increments_on_a_consecutive_day(self):
        self.profile.last_study_date = self.today - timedelta(days=1)
        self.profile.current_streak = 4
        self.profile.longest_streak = 4
        self.profile.save()

        self._mark()

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.current_streak, 5)
        self.assertEqual(self.profile.longest_streak, 5)
        self.assertEqual(self.profile.last_study_date, self.today)

    def test_streak_resets_after_a_missed_day(self):
        self.profile.last_study_date = self.today - timedelta(days=3)
        self.profile.current_streak = 6
        self.profile.longest_streak = 9
        self.profile.save()

        self._mark()

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.current_streak, 1)
        self.assertEqual(self.profile.longest_streak, 9)
        self.assertEqual(self.profile.last_study_date, self.today)

    def test_same_day_marks_do_not_increment_twice(self):
        self.profile.last_study_date = self.today
        self.profile.current_streak = 3
        self.profile.longest_streak = 3
        self.profile.save()

        self._mark()
        self._mark()

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.current_streak, 3)
        self.assertEqual(self.profile.longest_streak, 3)

    def test_exam_submission_advances_the_streak(self):
        self.profile.last_study_date = self.today - timedelta(days=1)
        self.profile.current_streak = 2
        self.profile.longest_streak = 2
        self.profile.save()

        self.client.post(
            reverse('tupas:exam_mode', args=[self.deck.id, 'multiple-choice']),
            {
                **self.start_exam('multiple-choice'),
                f'q_{self.flip.id}': self.correct_choice.id,
            },
        )

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.current_streak, 3)
        self.assertEqual(self.profile.last_study_date, self.today)


class DarkModeAndAccessibilityTests(TupasBaseTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_base_template_bootstraps_theme_before_paint(self):
        response = self.client.get(reverse('tupas:dashboard'))

        self.assertContains(response, 'data-theme')
        self.assertContains(response, "localStorage.getItem('tupas-theme')")
        self.assertContains(response, 'prefers-color-scheme: dark')
        self.assertContains(response, 'id="tupas-theme-toggle"')
        self.assertContains(response, 'accessibility.js')

    def test_dark_theme_tokens_exist_in_the_stylesheet(self):
        css = (
            Path(settings.BASE_DIR) / 'static' / 'css' / 'style.css'
        ).read_text(encoding='utf-8')

        self.assertIn('[data-theme="dark"]', css)
        self.assertIn('#0b1020', css)
        self.assertIn('--tupas-surface', css)

    def test_flip_cards_have_read_aloud_controls(self):
        response = self.client.get(
            reverse('tupas:flip_mode', args=[self.deck.id])
        )

        self.assertContains(response, f'data-speak-target="flip-front-{self.flip.id}"')
        self.assertContains(response, f'data-speak-target="flip-back-{self.flip.id}"')
        self.assertContains(response, 'data-speak-label')

    def test_enumeration_prompts_have_read_aloud_controls(self):
        response = self.client.get(
            reverse('tupas:enumeration_mode', args=[self.deck.id])
        )

        self.assertContains(
            response,
            f'data-speak-target="enum-prompt-{self.enumeration.id}"',
        )

    def test_dashboard_shows_streak_and_daily_goal(self):
        self.profile.current_streak = 3
        self.profile.longest_streak = 5
        self.profile.save()

        response = self.client.get(reverse('tupas:dashboard'))

        self.assertEqual(response.context['current_streak'], 3)
        self.assertEqual(response.context['longest_streak'], 5)
        self.assertEqual(response.context['today_reviewed'], 0)
        self.assertEqual(response.context['daily_goal'], 10)
        self.assertContains(response, 'day streak')
        self.assertContains(response, 'cards today')
        self.assertContains(response, 'goal-ring')

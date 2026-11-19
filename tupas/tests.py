import io
import json
import os
import struct
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from datetime import date, timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.shortcuts import resolve_url
from django.template.loader import render_to_string
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .ai import (
    OPENROUTER_ENDPOINT,
    AIError,
    _extract_json_object,
    _post_json,
    extract_text_from_image,
    generate_reviewer_json,
    get_ai_status,
    normalize_generated_payload,
    save_generated_items,
)
from .forms import AIReviewerForm
from .models import (
    ActivityLog,
    Deck,
    EnumerationItem,
    ExamAttempt,
    ItemProgress,
    QuizChoice,
    ReviewItem,
    StudentProfile,
    StudyChatMessage,
    StudyDocument,
    Subject,
)
from .templatetags.tupas_extras import cram_note, highlight_prompt, strip_highlights
from .services.exams import build_exam_questions
from .services.gamification import award_xp
from .services.activity import record_activity


class TupasBaseTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username='student',
            email='student@example.com',
            password='StrongPass123!',
        )
        self.profile = StudentProfile.objects.create(
            user=self.user,
            full_name='Maria Santos',
            birthdate='2004-05-14',
            region='Region VI - Western Visayas',
            province='Negros Occidental',
            city_municipality='Kabankalan City',
            school_name='Kabankalan City National High School',
            student_level=StudentProfile.StudentLevel.COLLEGE,
            course_or_strand='BSIT',
            year_level='3rd Year',
        )
        self.subject = Subject.objects.create(
            subject_name='Information Technology',
            target_course='BSIT',
            description='IT fundamentals',
        )
        self.deck = Deck.objects.create(
            subject=self.subject,
            title='Networking Basics',
            description='Introductory networking reviewer',
            created_by=self.user,
        )
        self.flip = ReviewItem.objects.create(
            deck=self.deck,
            item_type=ReviewItem.ItemType.FLIP_CARD,
            prompt_text='What does IP stand for?',
            main_answer='Internet Protocol',
            explanation='IP is the principal communications protocol in the Internet protocol suite.',
        )
        self.correct_choice = QuizChoice.objects.create(
            review_item=self.flip,
            choice_text='Internet Protocol',
            is_correct=True,
        )
        self.wrong_choice_1 = QuizChoice.objects.create(
            review_item=self.flip,
            choice_text='Internal Program',
            is_correct=False,
        )
        self.wrong_choice_2 = QuizChoice.objects.create(
            review_item=self.flip,
            choice_text='Internet Provider',
            is_correct=False,
        )
        self.identification = ReviewItem.objects.create(
            deck=self.deck,
            item_type=ReviewItem.ItemType.FLIP_CARD,
            prompt_text='What does TCP stand for?',
            main_answer='Transmission Control Protocol',
            explanation='TCP is a reliable, connection-oriented transport protocol.',
        )
        self.highlight = ReviewItem.objects.create(
            deck=self.deck,
            item_type=ReviewItem.ItemType.HIGHLIGHT_NOTE,
            prompt_text='The [[OSI model]] divides networking into [[seven layers]].',
            explanation='The OSI model is a conceptual framework for networking.',
        )
        self.enumeration = ReviewItem.objects.create(
            deck=self.deck,
            item_type=ReviewItem.ItemType.ENUMERATION,
            prompt_text='List the first three OSI layers in order.',
            explanation='The layers build from physical transmission upward.',
            is_strict_order=True,
        )
        for order, answer in enumerate(['Physical', 'Data Link', 'Network'], start=1):
            EnumerationItem.objects.create(
                review_item=self.enumeration,
                answer_text=answer,
                order_number=order,
            )
        self.other_subject = Subject.objects.create(
            subject_name='Education',
            target_course='BSED',
        )
        self.other_deck = Deck.objects.create(
            subject=self.other_subject,
            title='Teaching Strategies',
        )

    def start_exam(self, mode):
        """Load the exam form and return its one-time submission fields."""
        response = self.client.get(
            reverse('tupas:exam_mode', args=[self.deck.id, mode])
        )
        self.assertEqual(response.status_code, 200)
        return {
            'attempt_token': response.context['attempt_token'],
            'exam_seed': response.context['exam_seed'],
        }


class ModelAndRegistrationTests(TupasBaseTestCase):
    def test_student_profile_is_one_to_one_with_related_name(self):
        self.assertEqual(self.user.profile, self.profile)
        self.assertEqual(self.profile.get_student_level_display(), 'College')

    def test_registration_creates_user_profile_and_logs_in(self):
        response = self.client.post(reverse('tupas:register'), {
            'username': 'newstudent',
            'email': 'newstudent@example.com',
            'full_name': 'Juan Dela Cruz',
            'birthdate': '2003-01-20',
            'region': 'Region VI - Western Visayas',
            'province': 'Negros Occidental',
            'city_municipality': 'Bago City',
            'school_name': 'Bago City College',
            'student_level': 'COLLEGE',
            'course_or_strand': 'BS Information Systems',
            'year_level': '2nd Year',
            'password1': 'VeryStrongPass456!',
            'password2': 'VeryStrongPass456!',
        })
        self.assertRedirects(response, reverse('tupas:dashboard'))
        user = User.objects.get(username='newstudent')
        self.assertEqual(user.email, 'newstudent@example.com')
        self.assertEqual(user.profile.full_name, 'Juan Dela Cruz')
        self.assertEqual(user.profile.city_municipality, 'Bago City')
        self.assertEqual(int(self.client.session['_auth_user_id']), user.id)

    def test_dashboard_requires_login(self):
        response = self.client.get(reverse('tupas:dashboard'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(resolve_url(settings.LOGIN_URL), response.url)

    def test_login_page_renders(self):
        response = self.client.get(reverse('tupas:login'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ready for focus?')

    def test_item_progress_is_unique_per_user_and_item(self):
        ItemProgress.objects.create(
            user=self.user,
            review_item=self.flip,
            mastery_status=ItemProgress.MasteryStatus.MASTERED,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ItemProgress.objects.create(
                    user=self.user,
                    review_item=self.flip,
                    mastery_status=ItemProgress.MasteryStatus.NEEDS_REVIEW,
                )


class DashboardAndFlipTests(TupasBaseTestCase):
    def test_dashboard_recommends_course_matches(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('tupas:dashboard'))
        self.assertEqual(response.status_code, 200)
        recommended_ids = {deck.id for deck in response.context['recommended_decks']}
        self.assertIn(self.deck.id, recommended_ids)
        self.assertNotIn(self.other_deck.id, recommended_ids)
        self.assertEqual(response.context['stats']['deck_count'], 2)

    def test_flip_mode_lists_flip_cards(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('tupas:flip_mode', args=[self.deck.id]))
        self.assertEqual(response.status_code, 200)
        prompts = [item.prompt_text for item in response.context['items']]
        self.assertIn(self.flip.prompt_text, prompts)
        self.assertNotIn(self.highlight.prompt_text, prompts)

    def test_deck_detail_renders_mode_counts(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('tupas:deck_detail', args=[self.deck.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['type_counts']['FLIP_CARD'], 2)
        self.assertEqual(response.context['type_counts']['HIGHLIGHT_NOTE'], 1)
        self.assertEqual(response.context['type_counts']['ENUMERATION'], 1)
        self.assertEqual(response.context['exam_counts']['MULTIPLE_CHOICE'], 1)
        self.assertContains(response, 'Networking Basics')

    def test_mark_progress_creates_and_updates_mastery(self):
        self.client.force_login(self.user)
        url = reverse('tupas:mark_progress', args=[self.flip.id])

        response = self.client.post(
            url,
            data=json.dumps({'status': 'MASTERED'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['mastered'], 1)
        self.assertEqual(payload['needs_review'], 0)

        progress = ItemProgress.objects.get(user=self.user, review_item=self.flip)
        self.assertEqual(progress.mastery_status, ItemProgress.MasteryStatus.MASTERED)

        response = self.client.post(
            url,
            data=json.dumps({'status': 'NEEDS_REVIEW'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ItemProgress.objects.count(), 1)
        progress.refresh_from_db()
        self.assertEqual(progress.mastery_status, ItemProgress.MasteryStatus.NEEDS_REVIEW)
        self.assertEqual(response.json()['needs_review'], 1)

    def test_mark_progress_rejects_invalid_status(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('tupas:mark_progress', args=[self.flip.id]),
            data=json.dumps({'status': 'NOT_A_STATUS'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ItemProgress.objects.exists())


class HighlighterAndEnumerationTests(TupasBaseTestCase):
    def test_highlight_filter_marks_keywords_and_escapes_html(self):
        rendered = highlight_prompt('Use <script> with [[TCP]] and [[IP]].')
        self.assertIn('&lt;script&gt;', rendered)
        self.assertNotIn('<script>', rendered)
        self.assertIn('<mark class="tupas-highlight"', rendered)
        self.assertIn('data-answer="TCP"', rendered)
        self.assertIn('data-answer="IP"', rendered)

    def test_strip_highlights_removes_markers_for_exam_prompts(self):
        rendered = strip_highlights('The [[OSI model]] has [[seven layers]].')
        self.assertNotIn('[[', rendered)
        self.assertIn('OSI model', rendered)
        self.assertIn('seven layers', rendered)

    def test_highlighter_mode_renders_notes(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('tupas:highlight_mode', args=[self.deck.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['items']), 1)

    def test_enumeration_strict_order_rejects_wrong_sequence(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('tupas:enumeration_mode', args=[self.deck.id]),
            {
                'item_id': self.enumeration.id,
                f'answer_{self.enumeration.id}_1': 'Network',
                f'answer_{self.enumeration.id}_2': 'Physical',
                f'answer_{self.enumeration.id}_3': 'Data Link',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['result']['is_correct'])
        self.assertEqual(response.context['result']['correct_count'], 0)

    def test_enumeration_any_order_accepts_reordered_answers(self):
        any_order = ReviewItem.objects.create(
            deck=self.deck,
            item_type=ReviewItem.ItemType.ENUMERATION,
            prompt_text='Name two transport protocols.',
            is_strict_order=False,
        )
        EnumerationItem.objects.create(review_item=any_order, answer_text='TCP', order_number=1)
        EnumerationItem.objects.create(review_item=any_order, answer_text='UDP', order_number=2)

        self.client.force_login(self.user)
        response = self.client.post(
            reverse('tupas:enumeration_mode', args=[self.deck.id]),
            {
                'item_id': any_order.id,
                f'answer_{any_order.id}_1': ' udp ',
                f'answer_{any_order.id}_2': 'Tcp',
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['result']['is_correct'])
        self.assertEqual(response.context['result']['correct_count'], 2)


class ExamTests(TupasBaseTestCase):
    def test_multiple_choice_exam_saves_attempt(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('tupas:exam_mode', args=[self.deck.id, 'multiple-choice']),
            {
                **self.start_exam('multiple-choice'),
                f'q_{self.flip.id}': self.correct_choice.id,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['show_results'])

        attempt = ExamAttempt.objects.get(user=self.user, deck=self.deck)
        self.assertEqual(attempt.exam_mode, ExamAttempt.ExamMode.MULTIPLE_CHOICE)
        self.assertEqual(attempt.score, 1)
        self.assertEqual(attempt.total_items, 1)
        self.assertEqual(attempt.percentage, Decimal('100.00'))

    def test_mixed_exam_grades_all_modes_and_saves_percentage(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('tupas:exam_mode', args=[self.deck.id, 'mixed']),
            {
                **self.start_exam('mixed'),
                f'q_{self.flip.id}': self.correct_choice.id,
                f'q_{self.identification.id}': '  transmission control protocol ',
                f'q_{self.enumeration.id}_1': 'Physical',
                f'q_{self.enumeration.id}_2': 'Data Link',
                f'q_{self.enumeration.id}_3': 'Network',
            },
        )
        self.assertEqual(response.status_code, 200)
        results = response.context['results']
        self.assertEqual(len(results), 3)
        self.assertTrue(all(result['is_correct'] for result in results))

        attempt = ExamAttempt.objects.get(user=self.user, deck=self.deck)
        self.assertEqual(attempt.exam_mode, ExamAttempt.ExamMode.MIXED)
        self.assertEqual(attempt.score, 3)
        self.assertEqual(attempt.total_items, 3)
        self.assertEqual(attempt.percentage, Decimal('100.00'))

        dashboard = self.client.get(reverse('tupas:dashboard'))
        self.assertEqual(dashboard.context['stats']['mastered'], 0)
        self.assertEqual(dashboard.context['recent_attempts'][0], attempt)

    def test_exam_with_wrong_answer_still_saves_attempt(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('tupas:exam_mode', args=[self.deck.id, 'multiple-choice']),
            {
                **self.start_exam('multiple-choice'),
                f'q_{self.flip.id}': self.wrong_choice_1.id,
            },
        )
        self.assertEqual(response.status_code, 200)
        attempt = ExamAttempt.objects.get(user=self.user, deck=self.deck)
        self.assertEqual(attempt.score, 0)
        self.assertEqual(attempt.percentage, Decimal('0.00'))
        self.assertFalse(response.context['results'][0]['is_correct'])


class AIGeneratorServiceTests(TupasBaseTestCase):
    def sample_payload(self):
        return {
            'flip_cards': [{
                'prompt_text': 'What is encapsulation?',
                'main_answer': 'Wrapping data with protocol information',
                'explanation': 'Each layer adds its own header or trailer.',
                'distractors': [
                    'Compressing data',
                    'Encrypting data',
                    'Deleting headers',
                    'Compressing data',
                ],
            }],
            'highlight_notes': [{
                'prompt_text': 'The [[network layer]] handles [[routing]] between networks.',
                'explanation': 'Routing selects a path for packets.',
            }],
            'enumerations': [{
                'prompt_text': 'List the two transport protocols.',
                'answers': ['TCP', 'UDP'],
                'is_strict_order': False,
                'explanation': 'Both operate at the transport layer.',
            }],
        }

    def test_normalize_generated_payload_cleans_items(self):
        normalized = normalize_generated_payload(self.sample_payload(), limit=5)
        self.assertEqual(len(normalized['flip_cards']), 1)
        self.assertEqual(len(normalized['highlight_notes']), 1)
        self.assertEqual(len(normalized['enumerations']), 1)
        self.assertEqual(len(normalized['flip_cards'][0]['distractors']), 3)
        self.assertNotIn(
            'Compressing data',
            normalized['flip_cards'][0]['distractors'][1:],
        )

    def test_normalize_adds_highlight_markers_from_keywords(self):
        normalized = normalize_generated_payload({
            'highlight_notes': [{
                'prompt_text': 'The cell is the basic unit of life.',
                'keywords': ['cell'],
            }],
        })
        self.assertEqual(len(normalized['highlight_notes']), 1)
        self.assertIn('[[cell]]', normalized['highlight_notes'][0]['prompt_text'])

    def test_save_generated_items_writes_all_tables(self):
        normalized = normalize_generated_payload(self.sample_payload(), limit=5)
        counts = save_generated_items(self.deck, normalized)

        self.assertEqual(counts['flip_cards'], 1)
        self.assertEqual(counts['highlight_notes'], 1)
        self.assertEqual(counts['enumerations'], 1)
        self.assertEqual(counts['quiz_choices'], 4)
        self.assertEqual(counts['enum_answers'], 2)

        generated_flip = ReviewItem.objects.filter(
            deck=self.deck,
            prompt_text='What is encapsulation?',
        ).get()
        self.assertEqual(generated_flip.choices.count(), 4)
        self.assertEqual(generated_flip.choices.filter(is_correct=True).count(), 1)
        self.assertEqual(generated_flip.choices.filter(is_correct=False).count(), 3)

        generated_enum = ReviewItem.objects.filter(
            deck=self.deck,
            prompt_text='List the two transport protocols.',
        ).get()
        self.assertEqual(
            list(generated_enum.enum_answers.values_list('answer_text', flat=True)),
            ['TCP', 'UDP'],
        )

    def test_extract_json_handles_code_fences_and_trailing_commas(self):
        data = _extract_json_object('```json\n{"flip_cards": [], "highlight_notes": [],}\n```')
        self.assertEqual(data['flip_cards'], [])

    def test_get_ai_status_prefers_configured_environment_key(self):
        with patch.dict(os.environ, {
            'TUPAS_AI_PROVIDER': 'groq',
            'GROQ_API_KEY': 'test-key-not-real',
        }, clear=False):
            status = get_ai_status()
        self.assertTrue(status['configured'])
        self.assertEqual(status['provider'], 'groq')
        self.assertNotIn('test-key-not-real', json.dumps(status))

    def test_get_ai_status_supports_openrouter(self):
        with patch.dict(os.environ, {
            'TUPAS_AI_PROVIDER': 'openrouter',
            'OPENROUTER_API_KEY': 'test-openrouter-key',
        }, clear=False):
            status = get_ai_status()
        self.assertTrue(status['configured'])
        self.assertEqual(status['provider'], 'openrouter')
        self.assertEqual(status['env_var'], 'OPENROUTER_API_KEY')
        self.assertNotIn('test-openrouter-key', json.dumps(status))

    def test_openrouter_generation_uses_env_key_and_configured_model(self):
        fake_response = {
            'choices': [{
                'message': {'content': json.dumps(self.sample_payload())},
            }],
        }
        notes = 'Networking notes covering the OSI model and transport protocols. ' * 2

        with patch.dict(os.environ, {
            'TUPAS_AI_PROVIDER': 'openrouter',
            'OPENROUTER_API_KEY': 'test-openrouter-key',
            'OPENROUTER_MODEL': 'google/gemma-4-31b-it:free',
        }, clear=False), patch('tupas.ai._post_json', return_value=fake_response) as post:
            payload = generate_reviewer_json(notes, limit=3)

        self.assertEqual(len(payload['flip_cards']), 1)
        self.assertEqual(len(payload['highlight_notes']), 1)
        self.assertEqual(len(payload['enumerations']), 1)

        args, _kwargs = post.call_args
        self.assertEqual(args[0], OPENROUTER_ENDPOINT)
        self.assertEqual(args[1]['model'], 'google/gemma-4-31b-it:free')
        self.assertEqual(args[2]['Authorization'], 'Bearer test-openrouter-key')
        self.assertEqual(args[2]['X-Title'], 'TUPAS Reviewer')

    def test_ai_generator_view_saves_generated_items(self):
        self.client.force_login(self.user)
        payload = self.sample_payload()

        with patch.dict(os.environ, {'TUPAS_AI_PROVIDER': 'groq', 'GROQ_API_KEY': 'test-key-not-real'}, clear=False), \
                patch('tupas.views.ai.generate_reviewer_json', return_value=payload):
            response = self.client.post(reverse('tupas:ai_generator'), {
                'deck': self.deck.id,
                'notes': 'Networking notes. ' * 20,
                'items_per_type': '3',
                'item_types': ['FLIP_CARD', 'HIGHLIGHT_NOTE', 'ENUMERATION'],
                'exam_style': 'EXACT',
            })

        self.assertRedirects(response, reverse('tupas:deck_detail', args=[self.deck.id]))
        self.assertTrue(
            ReviewItem.objects.filter(
                deck=self.deck,
                prompt_text='What is encapsulation?',
            ).exists()
        )
        self.assertTrue(
            ReviewItem.objects.filter(
                deck=self.deck,
                item_type=ReviewItem.ItemType.HIGHLIGHT_NOTE,
                prompt_text__contains='[[network layer]]',
            ).exists()
        )

    def test_ai_generator_page_does_not_expose_api_key(self):
        self.client.force_login(self.user)
        with patch.dict(os.environ, {'GROQ_API_KEY': 'super-secret-value'}, clear=False):
            response = self.client.get(reverse('tupas:ai_generator'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'super-secret-value')
        self.assertContains(response, 'GROQ_API_KEY')

    def test_ai_generator_page_shows_ready_provider_when_configured(self):
        self.client.force_login(self.user)
        with patch.dict(os.environ, {
            'TUPAS_AI_PROVIDER': 'groq',
            'GROQ_API_KEY': 'test-key-not-real',
        }, clear=False):
            response = self.client.get(reverse('tupas:ai_generator'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ready')
        self.assertContains(response, 'Groq')

    def test_ai_generator_requires_login(self):
        response = self.client.get(reverse('tupas:ai_generator'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(resolve_url(settings.LOGIN_URL), response.url)

    def test_single_mode_flip_cards_only(self):
        notes = 'Networking notes covering the OSI model and transport protocols. ' * 2
        fake_response = {
            'choices': [{'message': {'content': json.dumps({
                'flip_cards': [{'prompt_text': 'Q?', 'main_answer': 'A', 'explanation': 'E', 'distractors': ['x', 'y', 'z']}],
                'highlight_notes': [],
                'enumerations': [],
            })}}],
        }
        with patch.dict(os.environ, {'TUPAS_AI_PROVIDER': 'groq', 'GROQ_API_KEY': 'test-key-not-real'}, clear=False), \
                patch('tupas.ai._post_json', return_value=fake_response):
            payload = generate_reviewer_json(
                notes, coverage_mode=3, selected_types=['FLIP_CARD'], exam_style='EXACT',
            )
        self.assertEqual(len(payload['flip_cards']), 1)
        self.assertEqual(payload['highlight_notes'], [])
        self.assertEqual(payload['enumerations'], [])

    def test_single_mode_enumerations_only(self):
        notes = 'Networking notes covering the OSI model and transport protocols. ' * 2
        fake_response = {
            'choices': [{'message': {'content': json.dumps({
                'flip_cards': [],
                'highlight_notes': [],
                'enumerations': [{'prompt_text': 'List layers', 'answers': ['a', 'b'], 'is_strict_order': True, 'explanation': 'E'}],
            })}}],
        }
        with patch.dict(os.environ, {'TUPAS_AI_PROVIDER': 'groq', 'GROQ_API_KEY': 'test-key-not-real'}, clear=False), \
                patch('tupas.ai._post_json', return_value=fake_response):
            payload = generate_reviewer_json(
                notes, coverage_mode=5, selected_types=['ENUMERATION'], exam_style='TAGLISH',
            )
        self.assertEqual(payload['flip_cards'], [])
        self.assertEqual(payload['highlight_notes'], [])
        self.assertEqual(len(payload['enumerations']), 1)

    def test_exhaustive_mode_all(self):
        notes = 'Networking notes covering the OSI model and transport protocols. ' * 2
        fake_response = {
            'choices': [{'message': {'content': json.dumps({
                'flip_cards': [{'prompt_text': 'Q?', 'main_answer': 'A', 'explanation': 'E', 'distractors': ['x', 'y', 'z']}],
                'highlight_notes': [{'prompt_text': 'Para [[term]]', 'explanation': 'E'}],
                'enumerations': [{'prompt_text': 'List', 'answers': ['a'], 'is_strict_order': False, 'explanation': 'E'}],
            })}}],
        }
        with patch.dict(os.environ, {'TUPAS_AI_PROVIDER': 'groq', 'GROQ_API_KEY': 'test-key-not-real'}, clear=False), \
                patch('tupas.ai._post_json', return_value=fake_response):
            payload = generate_reviewer_json(
                notes, coverage_mode='ALL', selected_types=['FLIP_CARD', 'HIGHLIGHT_NOTE', 'ENUMERATION'],
                exam_style='SITUATIONAL',
            )
        self.assertGreaterEqual(len(payload['flip_cards']) + len(payload['highlight_notes']) + len(payload['enumerations']), 1)

    def test_exam_style_values_accepted(self):
        notes = 'Networking notes covering the OSI model and transport protocols. ' * 2
        for style in ('EXACT', 'TAGLISH', 'SITUATIONAL'):
            fake_response = {
                'choices': [{'message': {'content': json.dumps({
                    'flip_cards': [{'prompt_text': 'Q?', 'main_answer': 'A', 'explanation': 'E', 'distractors': ['x', 'y', 'z']}],
                    'highlight_notes': [],
                    'enumerations': [],
                })}}],
            }
            with patch.dict(os.environ, {'TUPAS_AI_PROVIDER': 'groq', 'GROQ_API_KEY': 'test-key-not-real'}, clear=False), \
                    patch('tupas.ai._post_json', return_value=fake_response):
                payload = generate_reviewer_json(
                    notes, coverage_mode=3, selected_types=['FLIP_CARD'], exam_style=style,
                )
            self.assertEqual(len(payload['flip_cards']), 1)

class PWATests(TestCase):
    icon_sizes = {
        'icon-192.png': (192, 192),
        'icon-512.png': (512, 512),
        'icon-maskable-512.png': (512, 512),
        'apple-touch-icon.png': (180, 180),
    }

    def test_manifest_has_required_install_fields(self):
        manifest_path = Path(settings.BASE_DIR) / 'static' / 'manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))

        self.assertEqual(manifest['name'], 'TUPAS: Study & Exam Reviewer')
        self.assertEqual(manifest['short_name'], 'TUPAS')
        self.assertEqual(manifest['start_url'], '/')
        self.assertEqual(manifest['display'], 'standalone')
        self.assertEqual(manifest['background_color'], '#faf7ee')
        self.assertEqual(manifest['theme_color'], '#a8ee00')
        self.assertEqual(manifest['orientation'], 'portrait-primary')

        icon_sizes = {icon['sizes'] for icon in manifest['icons']}
        self.assertIn('192x192', icon_sizes)
        self.assertIn('512x512', icon_sizes)

    def test_manifest_icons_exist_with_correct_dimensions(self):
        icons_dir = Path(settings.BASE_DIR) / 'static' / 'icons'
        for name, expected_size in self.icon_sizes.items():
            with self.subTest(icon=name):
                data = (icons_dir / name).read_bytes()
                self.assertEqual(data[:8], b'\x89PNG\r\n\x1a\n')
                width, height = struct.unpack('>II', data[16:24])
                self.assertEqual((width, height), expected_size)

    def test_service_worker_is_served_from_root_with_javascript_content_type(self):
        response = self.client.get('/sw.js')

        self.assertEqual(response.status_code, 200)
        self.assertIn('application/javascript', response['Content-Type'])
        self.assertContains(response, 'const CACHE_VERSION')
        self.assertContains(response, 'tupas-static-')
        self.assertContains(response, '/offline/')
        self.assertContains(response, '/static/css/style.css')

    def test_offline_page_is_public(self):
        response = self.client.get(reverse('tupas:offline'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "You're offline")
        self.assertContains(response, 'Try again')

    def test_base_template_includes_pwa_hooks(self):
        html = render_to_string('base.html')

        self.assertIn('rel="manifest"', html)
        self.assertIn('name="theme-color"', html)
        self.assertIn('apple-mobile-web-app-capable', html)
        self.assertIn('apple-mobile-web-app-status-bar-style', html)
        self.assertIn('apple-touch-icon', html)
        self.assertIn('id="tupas-install-btn"', html)
        self.assertIn('id="tupas-install-hint"', html)

    def test_custom_auth_pages_load_pwa_assets(self):
        for url_name in ('login', 'register', 'landing'):
            with self.subTest(page=url_name):
                response = self.client.get(reverse(f'tupas:{url_name}'))

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'rel="manifest"')
                self.assertContains(response, 'apple-mobile-web-app-capable')
                self.assertContains(response, '/static/js/tupas.js')

    def test_client_script_wires_install_prompt_and_service_worker(self):
        script_path = Path(settings.BASE_DIR) / 'static' / 'js' / 'tupas.js'
        script = script_path.read_text(encoding='utf-8')

        self.assertIn('beforeinstallprompt', script)
        self.assertIn('appinstalled', script)
        self.assertIn("navigator.serviceWorker.register('/sw.js')", script)


class ExamRandomizationAndIdempotencyTests(TupasBaseTestCase):
    def _add_mcq_items(self, count=5):
        for index in range(count):
            item = ReviewItem.objects.create(
                deck=self.deck,
                prompt_text=f'Extra question {index}',
                main_answer=f'Answer {index}',
            )
            for choice_index in range(4):
                QuizChoice.objects.create(
                    review_item=item,
                    choice_text=f'Choice {index}-{choice_index}',
                    is_correct=choice_index == 0,
                )

    def test_same_seed_rebuilds_an_identical_paper(self):
        self._add_mcq_items()
        mode = ExamAttempt.ExamMode.MULTIPLE_CHOICE
        first = build_exam_questions(self.deck, mode, seed='a1b2c3d4')
        second = build_exam_questions(self.deck, mode, seed='a1b2c3d4')

        self.assertEqual(
            [question['item'].id for question in first],
            [question['item'].id for question in second],
        )
        self.assertEqual(
            [[choice.id for choice in question['choices']] for question in first],
            [[choice.id for choice in question['choices']] for question in second],
        )

    def test_different_seeds_change_the_question_order(self):
        self._add_mcq_items()
        mode = ExamAttempt.ExamMode.MULTIPLE_CHOICE
        papers = {
            tuple(
                question['item'].id
                for question in build_exam_questions(self.deck, mode, seed=seed)
            )
            for seed in ('a1b2c3d4', 'e5f6a7b8', '11223344')
        }
        self.assertGreater(len(papers), 1)

    def test_exam_form_carries_a_seed_and_attempt_token(self):
        self.client.force_login(self.user)
        response = self.client.get(
            reverse('tupas:exam_mode', args=[self.deck.id, 'multiple-choice'])
        )

        self.assertContains(response, 'name="exam_seed"')
        self.assertContains(response, 'name="attempt_token"')
        self.assertRegex(response.context['exam_seed'], r'^[0-9a-f]{16}$')
        self.assertEqual(
            response.context['attempt_token'],
            self.client.session[f'tupas:exam-token:{self.deck.id}:multiple-choice'],
        )

    def test_repeated_exam_post_saves_only_one_attempt(self):
        self.client.force_login(self.user)
        data = {
            **self.start_exam('multiple-choice'),
            f'q_{self.flip.id}': self.correct_choice.id,
        }
        url = reverse('tupas:exam_mode', args=[self.deck.id, 'multiple-choice'])

        first = self.client.post(url, data)
        second = self.client.post(url, data)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(
            ExamAttempt.objects.filter(user=self.user, deck=self.deck).count(),
            1,
        )


class AnyOrderEnumerationTests(TupasBaseTestCase):
    def _make_item(self):
        item = ReviewItem.objects.create(
            deck=self.deck,
            item_type=ReviewItem.ItemType.ENUMERATION,
            prompt_text='Name the two transport protocols.',
            is_strict_order=False,
        )
        EnumerationItem.objects.create(
            review_item=item, answer_text='TCP', order_number=1,
        )
        EnumerationItem.objects.create(
            review_item=item, answer_text='UDP', order_number=2,
        )
        return item

    def test_swapped_answers_mark_every_slot_correct(self):
        item = self._make_item()
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('tupas:enumeration_mode', args=[self.deck.id]),
            {
                'item_id': item.id,
                f'answer_{item.id}_1': 'udp',
                f'answer_{item.id}_2': 'TCP',
            },
        )
        result = response.context['result']

        self.assertTrue(result['is_correct'])
        self.assertEqual(result['checks'], [True, True])

    def test_unmatched_slot_is_red_while_matched_slot_stays_green(self):
        item = self._make_item()
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('tupas:enumeration_mode', args=[self.deck.id]),
            {
                'item_id': item.id,
                f'answer_{item.id}_1': 'TCP',
                f'answer_{item.id}_2': 'ICMP',
            },
        )
        result = response.context['result']

        self.assertFalse(result['is_correct'])
        self.assertEqual(result['checks'], [True, False])


class AIGeneratorAuthorizationTests(TupasBaseTestCase):
    def test_students_only_see_decks_they_created(self):
        other = User.objects.create_user('other', 'other@example.com', 'StrongPass123!')
        other_deck = Deck.objects.create(
            subject=self.subject, title='Other deck', created_by=other,
        )
        staff = User.objects.create_user(
            'staff', 'staff@example.com', 'StrongPass123!', is_staff=True,
        )

        student_form = AIReviewerForm(user=self.user)
        student_ids = set(
            student_form.fields['deck'].queryset.values_list('pk', flat=True)
        )
        self.assertEqual(student_ids, {self.deck.id})
        self.assertNotIn(other_deck.id, student_ids)

        staff_form = AIReviewerForm(user=staff)
        staff_ids = set(
            staff_form.fields['deck'].queryset.values_list('pk', flat=True)
        )
        self.assertIn(other_deck.id, staff_ids)
        self.assertIn(self.deck.id, staff_ids)

    def test_simplified_deck_creation_auto_subject(self):
        self.client.force_login(self.user)
        payload = {
            'flip_cards': [{
                'prompt_text': 'What is CPU?',
                'main_answer': 'Central Processing Unit',
                'explanation': 'Core processor',
                'distractors': ['GPU', 'RAM', 'SSD'],
            }],
            'highlight_notes': [],
            'enumerations': [],
        }
        notes = 'A central processing unit (CPU) is also called a central processor or main processor. It executes instructions.' * 2
        url = reverse('tupas:ai_generator')
        form_data = {
            'new_deck_title': 'Introduction to Computer Hardware',
            'subject_name': '',  # Left blank!
            'notes': notes,
            'items_per_type': '3',
            'item_types': ['FLIP_CARD'],
            'exam_style': 'EXACT',
        }

        with patch.dict(os.environ, {'TUPAS_AI_PROVIDER': 'groq', 'GROQ_API_KEY': 'test-key-not-real'}, clear=False), \
                patch('tupas.views.ai.generate_reviewer_json', return_value=payload):
            response = self.client.post(url, form_data)

        self.assertEqual(response.status_code, 302)
        new_deck = Deck.objects.filter(title='Introduction to Computer Hardware').first()
        self.assertIsNotNone(new_deck)
        # Should default to student's course_or_strand or General Studies
        self.assertIn(new_deck.subject.subject_name, [self.user.profile.course_or_strand, 'General Studies'])
        self.assertEqual(new_deck.items.count(), 1)

    def test_simplified_deck_creation_custom_subject_autocomplete(self):
        self.client.force_login(self.user)
        payload = {
            'flip_cards': [{
                'prompt_text': 'What is RAM?',
                'main_answer': 'Random Access Memory',
                'explanation': 'Volatile memory',
                'distractors': ['ROM', 'HDD', 'SSD'],
            }],
            'highlight_notes': [],
            'enumerations': [],
        }
        notes = 'Random-access memory is a form of electronic computer memory that can be read and changed in any order.' * 2
        url = reverse('tupas:ai_generator')
        form_data = {
            'new_deck_title': 'Memory Systems',
            'subject_name': 'CompArch 201',
            'notes': notes,
            'items_per_type': '3',
            'item_types': ['FLIP_CARD'],
            'exam_style': 'EXACT',
        }

        with patch.dict(os.environ, {'TUPAS_AI_PROVIDER': 'groq', 'GROQ_API_KEY': 'test-key-not-real'}, clear=False), \
                patch('tupas.views.ai.generate_reviewer_json', return_value=payload):
            response = self.client.post(url, form_data)

        self.assertEqual(response.status_code, 302)
        new_deck = Deck.objects.filter(title='Memory Systems').first()
        self.assertIsNotNone(new_deck)
        self.assertEqual(new_deck.subject.subject_name, 'CompArch 201')
        self.assertEqual(new_deck.items.count(), 1)


class AIGenerationRateLimitTests(TupasBaseTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()

    def test_second_generation_is_blocked_after_the_limit(self):
        self.client.force_login(self.user)
        payload = {'flip_cards': [], 'highlight_notes': [], 'enumerations': []}
        notes = 'Networking notes covering the OSI model and transport protocols. ' * 2
        url = reverse('tupas:ai_generator')
        form_data = {
            'deck': self.deck.id,
            'notes': notes,
            'items_per_type': '3',
            'item_types': ['FLIP_CARD'],
            'exam_style': 'EXACT',
        }

        with override_settings(TUPAS_AI_RATE_LIMIT=1, TUPAS_AI_RATE_WINDOW=900), \
                patch.dict(os.environ, {
                    'TUPAS_AI_PROVIDER': 'groq',
                    'GROQ_API_KEY': 'test-key-not-real',
                }, clear=False), \
                patch('tupas.views.ai.generate_reviewer_json', return_value=payload) as generate:
            first = self.client.post(url, form_data)
            second = self.client.post(url, form_data)

        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(generate.call_count, 1)
        self.assertContains(second, 'AI generation allowance')


class AIErrorSanitizationTests(TestCase):
    def test_provider_body_is_logged_but_hidden_from_users(self):
        body = io.BytesIO(b'{"error":"invalid_api_key","account":"acct-998877"}')
        error = HTTPError(
            'https://api.groq.com/openai/v1/chat/completions',
            401,
            'Unauthorized',
            {},
            body,
        )
        with patch('tupas.ai.urllib.request.urlopen', side_effect=error), \
                self.assertLogs('tupas.ai', level='WARNING') as captured:
            with self.assertRaises(AIError) as caught:
                _post_json(
                    'https://api.groq.com/openai/v1/chat/completions',
                    {'model': 'test-model'},
                )

        message = str(caught.exception)
        self.assertIn('HTTP 401', message)
        self.assertNotIn('invalid_api_key', message)
        self.assertNotIn('acct-998877', message)
        self.assertTrue(any('acct-998877' in line for line in captured.output))


class StudyBuddyTests(TupasBaseTestCase):
    def test_requires_login(self):
        response = self.client.get(reverse('tupas:study_buddy'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(resolve_url(settings.LOGIN_URL), response.url)

    def test_page_renders_for_a_signed_in_student(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('tupas:study_buddy'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Talk to your handouts')

    def test_pasted_notes_create_a_study_document(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('tupas:study_buddy'), {
            'title': 'Biology Handout',
            'pasted_notes': 'The cell is the basic unit of life. ' * 12,
        })

        document = StudyDocument.objects.get(user=self.user)
        self.assertRedirects(
            response,
            reverse('tupas:study_buddy_detail', args=[document.id]),
        )
        self.assertEqual(document.title, 'Biology Handout')
        self.assertGreater(document.word_count, 0)

    def test_empty_upload_is_rejected_without_creating_a_document(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('tupas:study_buddy'), {'title': 'Empty'})

        self.assertEqual(response.status_code, 200)
        self.assertFalse(StudyDocument.objects.exists())

    def test_chat_saves_student_and_tutor_messages(self):
        document = StudyDocument.objects.create(
            user=self.user,
            title='Biology',
            extracted_text='Cells are the basic unit of life. ' * 5,
        )
        self.client.force_login(self.user)
        with patch(
            'tupas.views.study_buddy.chat_with_tupas_tutor',
            return_value='Here is your summary.',
        ) as tutor:
            response = self.client.post(
                reverse('tupas:study_buddy_chat', args=[document.id]),
                {'message': 'Summarize this'},
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            list(document.messages.values_list('sender', 'message')),
            [
                (StudyChatMessage.Sender.STUDENT, 'Summarize this'),
                (StudyChatMessage.Sender.TUPAS, 'Here is your summary.'),
            ],
        )
        tutor.assert_called_once()

    def test_another_users_document_is_not_reachable(self):
        other = User.objects.create_user('other', 'other@example.com', 'StrongPass123!')
        document = StudyDocument.objects.create(
            user=other,
            title='Private notes',
            extracted_text='Private material.',
        )
        self.client.force_login(self.user)

        self.assertEqual(
            self.client.get(
                reverse('tupas:study_buddy_detail', args=[document.id])
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(
                reverse('tupas:study_buddy_chat', args=[document.id]),
                {'message': 'Hi'},
            ).status_code,
            404,
        )

    def test_generate_deck_creates_an_owned_deck_with_items(self):
        document = StudyDocument.objects.create(
            user=self.user,
            title='Biology',
            extracted_text='Cells are the basic unit of life. ' * 20,
        )
        payload = {
            'flip_cards': [{
                'prompt_text': 'What is the basic unit of life?',
                'main_answer': 'The cell',
                'explanation': 'All living things are made of cells.',
                'distractors': ['Atom', 'Organ', 'Tissue'],
            }],
            'highlight_notes': [],
            'enumerations': [],
        }
        self.client.force_login(self.user)

        with patch(
            'tupas.views.study_buddy.generate_deck_payload_from_text',
            return_value=payload,
        ):
            response = self.client.post(
                reverse('tupas:study_buddy_generate_deck', args=[document.id]),
                {
                    'deck_title': 'Biology Reviewer',
                    'new_subject_name': 'Biology',
                    'items_count': '5',
                },
            )

        deck = Deck.objects.get(title='Biology Reviewer')
        self.assertRedirects(
            response,
            reverse('tupas:deck_detail', args=[deck.id]),
            fetch_redirect_response=False,
        )
        self.assertEqual(deck.created_by, self.user)
        self.assertEqual(deck.items.count(), 1)
        self.assertEqual(deck.items.first().choices.count(), 4)

        document.refresh_from_db()
        self.assertEqual(document.generated_deck, deck)


class CollectStaticConfigTests(TestCase):
    def test_static_root_is_configured(self):
        self.assertTrue(settings.STATIC_ROOT)

    def test_collectstatic_dry_run_succeeds(self):
        output = io.StringIO()
        call_command(
            'collectstatic',
            '--dry-run',
            '--noinput',
            verbosity=0,
            stdout=output,
        )


class WeakSpotFilterTests(TupasBaseTestCase):
    def setUp(self):
        super().setUp()
        ItemProgress.objects.create(
            user=self.user,
            review_item=self.flip,
            mastery_status=ItemProgress.MasteryStatus.MASTERED,
        )
        ItemProgress.objects.create(
            user=self.user,
            review_item=self.identification,
            mastery_status=ItemProgress.MasteryStatus.NEEDS_REVIEW,
        )
        ItemProgress.objects.create(
            user=self.user,
            review_item=self.enumeration,
            mastery_status=ItemProgress.MasteryStatus.NEEDS_REVIEW,
        )
        self.client.force_login(self.user)

    def test_flip_weak_filter_returns_only_study_again_items(self):
        response = self.client.get(
            reverse('tupas:flip_mode', args=[self.deck.id]) + '?filter=weak'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['current_filter'], 'weak')
        self.assertEqual(
            [item.id for item in response.context['items']],
            [self.identification.id],
        )
        self.assertEqual(response.context['weak_count'], 1)
        self.assertEqual(response.context['total_items'], 2)
        self.assertContains(response, 'Weak Spots Only')

    def test_flip_unmastered_filter_excludes_mastered_items(self):
        response = self.client.get(
            reverse('tupas:flip_mode', args=[self.deck.id]) + '?filter=unmastered'
        )

        ids = [item.id for item in response.context['items']]
        self.assertNotIn(self.flip.id, ids)
        self.assertIn(self.identification.id, ids)
        self.assertEqual(response.context['unmastered_count'], 1)

    def test_highlight_weak_filter(self):
        ItemProgress.objects.create(
            user=self.user,
            review_item=self.highlight,
            mastery_status=ItemProgress.MasteryStatus.NEEDS_REVIEW,
        )

        response = self.client.get(
            reverse('tupas:highlight_mode', args=[self.deck.id]) + '?filter=weak'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['items']), 1)
        self.assertEqual(response.context['items'][0].id, self.highlight.id)
        self.assertContains(response, 'Not Yet Mastered')

    def test_enumeration_weak_filter(self):
        response = self.client.get(
            reverse('tupas:enumeration_mode', args=[self.deck.id]) + '?filter=weak'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item.id for item in response.context['items']],
            [self.enumeration.id],
        )
        self.assertEqual(response.context['current_filter'], 'weak')


class ExamWeakSpotTests(TupasBaseTestCase):
    def _add_extra_mcq(self):
        extra = ReviewItem.objects.create(
            deck=self.deck,
            prompt_text='What does DNS resolve?',
            main_answer='Domain names to IP addresses',
        )
        QuizChoice.objects.create(
            review_item=extra, choice_text='Domain names to IP addresses', is_correct=True,
        )
        QuizChoice.objects.create(
            review_item=extra, choice_text='Ports to protocols', is_correct=False,
        )
        return extra

    def test_wrong_exam_items_are_marked_needs_review(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('tupas:exam_mode', args=[self.deck.id, 'multiple-choice']),
            {
                **self.start_exam('multiple-choice'),
                f'q_{self.flip.id}': self.wrong_choice_1.id,
            },
        )

        self.assertEqual(response.status_code, 200)
        progress = ItemProgress.objects.get(user=self.user, review_item=self.flip)
        self.assertEqual(
            progress.mastery_status,
            ItemProgress.MasteryStatus.NEEDS_REVIEW,
        )
        self.assertEqual(response.context['wrong_item_ids'], [self.flip.id])

    def test_weak_filter_limits_exam_to_study_again_items(self):
        extra = self._add_extra_mcq()
        ItemProgress.objects.create(
            user=self.user,
            review_item=self.flip,
            mastery_status=ItemProgress.MasteryStatus.NEEDS_REVIEW,
        )
        self.client.force_login(self.user)

        response = self.client.get(
            reverse('tupas:exam_mode', args=[self.deck.id, 'multiple-choice'])
            + '?filter=weak'
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['is_weak_exam'])
        self.assertEqual(
            [question['item'].id for question in response.context['questions']],
            [self.flip.id],
        )
        self.assertNotIn(
            extra.id,
            [question['item'].id for question in response.context['questions']],
        )

    def test_retry_ids_limits_exam_to_missed_items(self):
        extra = self._add_extra_mcq()
        self.client.force_login(self.user)

        response = self.client.get(
            reverse('tupas:exam_mode', args=[self.deck.id, 'multiple-choice'])
            + f'?retry_ids={extra.id}'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['retry_count'], 1)
        self.assertEqual(
            [question['item'].id for question in response.context['questions']],
            [extra.id],
        )
        self.assertContains(response, 'Retake mode')

    def test_results_offer_retake_wrong_answers_button(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('tupas:exam_mode', args=[self.deck.id, 'multiple-choice']),
            {
                **self.start_exam('multiple-choice'),
                f'q_{self.flip.id}': self.wrong_choice_1.id,
            },
        )

        self.assertContains(response, 'Retake Only Wrong Answers (1 items)')
        self.assertContains(response, f'?retry_ids={self.flip.id}')


class EnumerationMnemonicTests(TupasBaseTestCase):
    def test_mnemonic_is_normalized_and_saved_as_explanation(self):
        payload = {
            'flip_cards': [],
            'highlight_notes': [],
            'enumerations': [{
                'prompt_text': 'List the four management functions.',
                'answers': ['Planning', 'Leading', 'Organizing', 'Controlling'],
                'is_strict_order': False,
                'explanation': 'Classic management framework.',
                'mnemonic': '💡 Memory Trick: P-L-O-C (Planning, Leading, Organizing, Controlling)',
            }],
        }

        normalized = normalize_generated_payload(payload)
        self.assertEqual(
            normalized['enumerations'][0]['mnemonic'],
            '💡 Memory Trick: P-L-O-C (Planning, Leading, Organizing, Controlling)',
        )

        save_generated_items(self.deck, normalized)
        item = ReviewItem.objects.get(prompt_text='List the four management functions.')
        self.assertIn('P-L-O-C', item.explanation)
        self.assertEqual(item.enum_answers.count(), 4)

    def test_enumeration_mode_shows_memory_trick_toggle(self):
        self.enumeration.explanation = '💡 Memory Trick: PDN (Physical, Data Link, Network)'
        self.enumeration.save(update_fields=['explanation'])
        self.client.force_login(self.user)

        response = self.client.get(
            reverse('tupas:enumeration_mode', args=[self.deck.id])
        )

        self.assertContains(response, 'Show Memory Trick / Acronym')
        self.assertContains(response, 'PDN')
        self.assertIn('data-mnemonic-target', response.content.decode())

    def test_cram_note_escapes_html_and_underlines_keywords(self):
        rendered = cram_note('Use <script> with [[TCP]] and [[IP]].')

        self.assertIn('&lt;script&gt;', rendered)
        self.assertNotIn('<script>', rendered)
        self.assertIn('<strong><u>TCP</u></strong>', rendered)


class DeckItemManagementTests(TupasBaseTestCase):
    def _other_deck(self):
        other = User.objects.create_user('classmate', 'mate@example.com', 'StrongPass123!')
        deck = Deck.objects.create(
            subject=self.subject,
            title='Classmate deck',
            created_by=other,
        )
        item = ReviewItem.objects.create(
            deck=deck,
            prompt_text='Classmate card',
            main_answer='Classmate answer',
        )
        return other, deck, item

    def test_owner_can_add_flip_card_with_distractors(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('tupas:add_review_item', args=[self.deck.id]),
            {
                'item_type': 'FLIP_CARD',
                'prompt_text': 'What is a subnet mask?',
                'main_answer': 'It defines the network portion of an IP address',
                'explanation': 'It separates network and host bits.',
                'distractor_1': 'It encrypts traffic',
                'distractor_2': 'It assigns MAC addresses',
                'distractor_3': 'It routes packets',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.endswith('#items'))
        item = ReviewItem.objects.get(prompt_text='What is a subnet mask?')
        self.assertEqual(item.choices.count(), 4)
        self.assertEqual(item.choices.filter(is_correct=True).count(), 1)

    def test_owner_can_add_enumeration_with_mnemonic(self):
        self.client.force_login(self.user)
        self.client.post(
            reverse('tupas:add_review_item', args=[self.deck.id]),
            {
                'item_type': 'ENUMERATION',
                'prompt_text': 'List the four management functions.',
                'answers': 'Planning\nLeading\nOrganizing\nControlling',
                'is_strict_order': 'on',
                'explanation': '💡 Memory Trick: P-L-O-C',
            },
        )

        item = ReviewItem.objects.get(prompt_text='List the four management functions.')
        self.assertTrue(item.is_strict_order)
        self.assertEqual(item.explanation, '💡 Memory Trick: P-L-O-C')
        self.assertEqual(
            list(item.enum_answers.values_list('answer_text', flat=True)),
            ['Planning', 'Leading', 'Organizing', 'Controlling'],
        )

    def test_owner_can_edit_a_flip_card(self):
        self.client.force_login(self.user)
        self.client.post(
            reverse('tupas:edit_review_item', args=[self.deck.id, self.flip.id]),
            {
                'prompt_text': 'What does IP stand for? (updated)',
                'main_answer': 'Internet Protocol Suite',
                'explanation': 'Updated explanation.',
            },
        )

        self.flip.refresh_from_db()
        self.assertEqual(self.flip.prompt_text, 'What does IP stand for? (updated)')
        self.assertEqual(self.flip.main_answer, 'Internet Protocol Suite')
        self.assertEqual(self.flip.explanation, 'Updated explanation.')
        self.assertEqual(
            self.flip.choices.get(is_correct=True).choice_text,
            'Internet Protocol Suite',
        )

    def test_owner_can_replace_enumeration_answers(self):
        self.client.force_login(self.user)
        self.client.post(
            reverse('tupas:edit_review_item', args=[self.deck.id, self.enumeration.id]),
            {
                'prompt_text': 'List the first three OSI layers in order.',
                'answers': 'Physical\nData Link\nNetwork',
                'explanation': 'PDN from the bottom up.',
            },
        )

        self.enumeration.refresh_from_db()
        self.assertFalse(self.enumeration.is_strict_order)
        self.assertEqual(
            list(self.enumeration.enum_answers.values_list('answer_text', flat=True)),
            ['Physical', 'Data Link', 'Network'],
        )

    def test_owner_can_delete_an_item(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('tupas:delete_review_item', args=[self.deck.id, self.flip.id])
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(ReviewItem.objects.filter(pk=self.flip.id).exists())

    def test_other_students_cannot_manage_someone_elses_deck(self):
        _other, deck, item = self._other_deck()
        self.client.force_login(self.user)

        self.assertEqual(
            self.client.post(
                reverse('tupas:add_review_item', args=[deck.id]),
                {'item_type': 'FLIP_CARD', 'prompt_text': 'Nope', 'main_answer': 'No'},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                reverse('tupas:edit_review_item', args=[deck.id, item.id]),
                {'prompt_text': 'Hacked', 'main_answer': 'Hacked'},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                reverse('tupas:delete_review_item', args=[deck.id, item.id]),
            ).status_code,
            403,
        )
        item.refresh_from_db()
        self.assertEqual(item.prompt_text, 'Classmate card')

    def test_staff_can_manage_any_deck(self):
        _other, deck, _item = self._other_deck()
        staff = User.objects.create_user(
            'teacher', 'teacher@example.com', 'StrongPass123!', is_staff=True,
        )
        self.client.force_login(staff)

        response = self.client.post(
            reverse('tupas:add_review_item', args=[deck.id]),
            {
                'item_type': 'HIGHLIGHT_NOTE',
                'prompt_text': 'The [[OSI model]] has [[seven layers]].',
                'explanation': 'Staff-added note.',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            ReviewItem.objects.filter(deck=deck, prompt_text__contains='[[OSI model]]').exists()
        )

    def test_deck_detail_only_shows_item_controls_to_editors(self):
        other, deck, _item = self._other_deck()

        self.client.force_login(self.user)
        own = self.client.get(reverse('tupas:deck_detail', args=[self.deck.id]))
        self.assertTrue(own.context['can_edit_deck'])
        self.assertContains(own, '+ Add Review Item')
        self.assertContains(own, 'deck-items-data')

        self.client.force_login(other)
        shared = self.client.get(reverse('tupas:deck_detail', args=[self.deck.id]))
        self.assertFalse(shared.context['can_edit_deck'])
        self.assertNotContains(shared, '+ Add Review Item')


class CramSheetAndCloneTests(TupasBaseTestCase):
    def test_cram_sheet_renders_all_sections(self):
        self.enumeration.explanation = '💡 Memory Trick: PDN'
        self.enumeration.save(update_fields=['explanation'])
        self.client.force_login(self.user)

        response = self.client.get(
            reverse('tupas:deck_cram_sheet', args=[self.deck.id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Key Terms')
        self.assertContains(response, 'Highlighted Study Notes')
        self.assertContains(response, 'Must-Memorize Enumerations')
        self.assertContains(response, self.deck.title)
        self.assertContains(response, 'PDN')
        self.assertContains(response, '<strong><u>OSI model</u></strong>')

    def test_cram_sheet_weak_filter_only_includes_study_again_items(self):
        ItemProgress.objects.create(
            user=self.user,
            review_item=self.flip,
            mastery_status=ItemProgress.MasteryStatus.NEEDS_REVIEW,
        )
        self.client.force_login(self.user)

        response = self.client.get(
            reverse('tupas:deck_cram_sheet', args=[self.deck.id]) + '?filter=weak'
        )

        self.assertEqual(response.context['filter_key'], 'weak')
        self.assertEqual(response.context['total_items'], 1)
        self.assertContains(response, self.flip.prompt_text)
        self.assertNotContains(response, self.highlight.prompt_text)

    def test_clone_deck_copies_items_choices_and_enumerations(self):
        self.client.force_login(self.user)
        source_items = self.deck.items.count()

        response = self.client.post(
            reverse('tupas:clone_deck', args=[self.deck.id])
        )

        clone = Deck.objects.get(title__endswith='(Copy)')
        self.assertRedirects(
            response,
            reverse('tupas:deck_detail', args=[clone.id]),
            fetch_redirect_response=False,
        )
        self.assertEqual(clone.created_by, self.user)
        self.assertEqual(clone.items.count(), source_items)
        self.assertEqual(self.deck.items.count(), source_items)

        cloned_flip = clone.items.get(prompt_text=self.flip.prompt_text)
        self.assertEqual(cloned_flip.choices.count(), self.flip.choices.count())
        cloned_enum = clone.items.get(prompt_text=self.enumeration.prompt_text)
        self.assertEqual(
            list(cloned_enum.enum_answers.values_list('answer_text', flat=True)),
            ['Physical', 'Data Link', 'Network'],
        )

    def test_clone_requires_post(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('tupas:clone_deck', args=[self.deck.id]))
        self.assertEqual(response.status_code, 405)


class ImageOcrTests(TupasBaseTestCase):
    def test_missing_gemini_key_returns_clean_error(self):
        with patch.dict(os.environ, {'GEMINI_API_KEY': ''}, clear=False):
            result = extract_text_from_image(b'fake-image', 'image/png')

        self.assertFalse(result['ok'])
        self.assertIn('GEMINI_API_KEY', result['error'])

    def test_gemini_multimodal_request_returns_transcription(self):
        fake_response = {
            'candidates': [{'content': {'parts': [{'text': 'Slide: Mitosis stages'}]}}],
        }
        with patch.dict(os.environ, {'GEMINI_API_KEY': 'test-key'}, clear=False), \
                patch('tupas.ai._post_json', return_value=fake_response) as post:
            result = extract_text_from_image(b'image-bytes', 'image/png')

        self.assertTrue(result['ok'])
        self.assertEqual(result['text'], 'Slide: Mitosis stages')
        args, _kwargs = post.call_args
        self.assertIn('x-goog-api-key', args[2])
        self.assertEqual(args[1]['contents'][0]['parts'][1]['inline_data']['mime_type'], 'image/png')

    def test_endpoint_returns_extracted_text(self):
        self.client.force_login(self.user)
        upload = SimpleUploadedFile(
            'slide.png', b'\x89PNG\r\n\x1a\nfake-bytes', content_type='image/png',
        )

        with patch(
            'tupas.views.ai.extract_text_from_image',
            return_value={'ok': True, 'text': 'Whiteboard notes'},
        ):
            response = self.client.post(
                reverse('tupas:extract_image'), {'image': upload},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'ok': True, 'text': 'Whiteboard notes'})

    def test_endpoint_rejects_unsupported_files(self):
        self.client.force_login(self.user)
        upload = SimpleUploadedFile('diagram.gif', b'GIF89a', content_type='image/gif')

        response = self.client.post(reverse('tupas:extract_image'), {'image': upload})

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()['ok'])

    def test_endpoint_requires_login(self):
        upload = SimpleUploadedFile('slide.png', b'fake', content_type='image/png')
        response = self.client.post(reverse('tupas:extract_image'), {'image': upload})
        self.assertEqual(response.status_code, 302)




class ProfilePageTests(TupasBaseTestCase):
    def test_profile_requires_login(self):
        response = self.client.get(reverse('tupas:profile'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(resolve_url(settings.LOGIN_URL), response.url)

    def test_profile_renders_for_signed_in_student(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('tupas:profile'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Edit Student Profile')
        self.assertContains(response, 'Change Password')
        self.assertContains(response, 'My Created Decks')
        self.assertContains(response, 'Maria Santos')
        self.assertIsNotNone(response.context['profile_form'])
        self.assertIsNotNone(response.context['password_form'])

    def test_profile_lists_recent_handouts(self):
        StudyDocument.objects.create(
            user=self.user,
            title='Cells and Organelles',
            extracted_text='Cells are the basic unit of life.',
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse('tupas:profile'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Recent Handouts')
        self.assertContains(response, 'Cells and Organelles')
        self.assertEqual(len(response.context['my_documents']), 1)

    def test_profile_get_or_creates_a_missing_profile(self):
        self.profile.delete()
        self.client.force_login(self.user)
        response = self.client.get(reverse('tupas:profile'))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(StudentProfile.objects.filter(user=self.user).exists())

    def test_update_profile_saves_fields_and_email(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('tupas:profile'), {
            'action': 'update_profile',
            'full_name': 'Maria D. Santos',
            'email': 'maria@example.com',
            'birthdate': '2004-06-01',
            'school_name': 'Kabankalan City National High School',
            'student_level': 'COLLEGE',
            'course_or_strand': 'BS Information Technology',
            'year_level': '4th Year',
            'region': 'Region VI - Western Visayas',
            'province': 'Negros Occidental',
            'city_municipality': 'Kabankalan City',
        })
        self.assertRedirects(response, reverse('tupas:profile'))

        self.profile.refresh_from_db()
        self.user.refresh_from_db()
        self.assertEqual(self.profile.full_name, 'Maria D. Santos')
        self.assertEqual(self.profile.year_level, '4th Year')
        self.assertEqual(self.user.email, 'maria@example.com')

    def test_duplicate_email_is_rejected(self):
        User.objects.create_user(
            'other', 'taken@example.com', 'VeryStrongPass456!',
        )
        self.client.force_login(self.user)
        response = self.client.post(reverse('tupas:profile'), {
            'action': 'update_profile',
            'full_name': 'Maria Santos',
            'email': 'taken@example.com',
            'birthdate': '2004-05-14',
            'school_name': 'Kabankalan City National High School',
            'student_level': 'COLLEGE',
            'course_or_strand': 'BSIT',
            'year_level': '3rd Year',
            'region': 'Region VI - Western Visayas',
            'province': 'Negros Occidental',
            'city_municipality': 'Kabankalan City',
        })
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'student@example.com')
        self.assertContains(response, 'already exists')

    def test_password_change_updates_password_and_keeps_session(self):
        self.client.force_login(self.user)
        old_password = 'OldStrongPass111!'
        self.user.set_password(old_password)
        self.user.save()
        # Re-login after the password reset above.
        self.client.force_login(self.user)

        response = self.client.post(reverse('tupas:profile'), {
            'action': 'change_password',
            'old_password': old_password,
            'new_password1': 'BrandNewPass222!',
            'new_password2': 'BrandNewPass222!',
        })
        self.assertRedirects(response, reverse('tupas:profile'))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('BrandNewPass222!'))
        # The student must stay logged in.
        session_user_id = int(self.client.session['_auth_user_id'])
        self.assertEqual(session_user_id, self.user.id)

        # The old password no longer works; the new one does.
        self.assertFalse(self.user.check_password(old_password))

    def test_password_change_rejects_wrong_current_password(self):
        self.client.force_login(self.user)
        self.user.set_password('CurrentPass111!')
        self.user.save()
        self.client.force_login(self.user)

        response = self.client.post(reverse('tupas:profile'), {
            'action': 'change_password',
            'old_password': 'WrongPass999!',
            'new_password1': 'BrandNewPass222!',
            'new_password2': 'BrandNewPass222!',
        })
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('CurrentPass111!'))

    def test_sidebar_has_profile_link_not_a_navbar_dropdown(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('tupas:dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'tupas-sidebar-nav')
        self.assertContains(response, 'My Profile & Settings')
        self.assertNotContains(response, 'dropdown-item')


class StudyBuddyManagementTests(TupasBaseTestCase):
    def setUp(self):
        super().setUp()
        self.document = StudyDocument.objects.create(
            user=self.user,
            title='Biology Handout',
            extracted_text='Cells are the basic unit of life. ' * 8,
        )
        self.student_msg = StudyChatMessage.objects.create(
            document=self.document,
            sender=StudyChatMessage.Sender.STUDENT,
            message='What is a cell?',
        )
        self.tupas_msg = StudyChatMessage.objects.create(
            document=self.document,
            sender=StudyChatMessage.Sender.TUPAS,
            message='A cell is the basic unit of life.',
        )

    def test_edit_message_requires_login(self):
        response = self.client.post(
            reverse(
                'tupas:study_buddy_edit_message',
                args=[self.document.id, self.student_msg.id],
            ),
            {'message': 'Edited question'},
        )
        self.assertEqual(response.status_code, 302)

    def test_edit_student_message_removes_stale_replies_and_regenerates(self):
        later_student = StudyChatMessage.objects.create(
            document=self.document,
            sender=StudyChatMessage.Sender.STUDENT,
            message='Follow-up?',
        )
        later_tupas = StudyChatMessage.objects.create(
            document=self.document,
            sender=StudyChatMessage.Sender.TUPAS,
            message='Stale follow-up answer.',
        )
        self.client.force_login(self.user)
        with patch(
            'tupas.views.study_buddy.chat_with_tupas_tutor',
            return_value='Fresh regenerated answer.',
        ) as tutor:
            response = self.client.post(
                reverse(
                    'tupas:study_buddy_edit_message',
                    args=[self.document.id, self.student_msg.id],
                ),
                {'message': 'Define a cell in Taglish.'},
            )

        self.assertEqual(response.status_code, 302)
        tutor.assert_called_once()
        self.student_msg.refresh_from_db()
        self.assertEqual(self.student_msg.message, 'Define a cell in Taglish.')
        remaining = list(self.document.messages.order_by('id'))
        self.assertEqual(len(remaining), 2)
        self.assertEqual(remaining[0].id, self.student_msg.id)
        self.assertEqual(remaining[0].message, 'Define a cell in Taglish.')
        self.assertEqual(remaining[1].sender, StudyChatMessage.Sender.TUPAS)
        self.assertEqual(remaining[1].message, 'Fresh regenerated answer.')
        self.assertFalse(
            StudyChatMessage.objects.filter(id=later_student.id).exists()
        )
        self.assertFalse(
            StudyChatMessage.objects.filter(id=later_tupas.id).exists()
        )

    def test_edit_supports_ajax_json(self):
        self.client.force_login(self.user)
        with patch(
            'tupas.views.study_buddy.chat_with_tupas_tutor',
            return_value='AJAX answer.',
        ):
            response = self.client.post(
                reverse(
                    'tupas:study_buddy_edit_message',
                    args=[self.document.id, self.student_msg.id],
                ),
                {'message': 'AJAX question?'},
                HTTP_X_REQUESTED_WITH='XMLHttpRequest',
            )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['message'], 'AJAX question?')

    def test_cannot_edit_a_tupas_message(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse(
                'tupas:study_buddy_edit_message',
                args=[self.document.id, self.tupas_msg.id],
            ),
            {'message': 'Hacked prompt'},
        )
        self.assertEqual(response.status_code, 400)
        self.tupas_msg.refresh_from_db()
        self.assertEqual(self.tupas_msg.message, 'A cell is the basic unit of life.')

    def test_delete_single_student_message_also_drops_immediate_reply(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse(
                'tupas:study_buddy_delete_message',
                args=[self.document.id, self.student_msg.id],
            ),
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            StudyChatMessage.objects.filter(id=self.student_msg.id).exists()
        )
        self.assertFalse(
            StudyChatMessage.objects.filter(id=self.tupas_msg.id).exists()
        )

    def test_delete_tupas_message_only_removes_that_row(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse(
                'tupas:study_buddy_delete_message',
                args=[self.document.id, self.tupas_msg.id],
            ),
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            StudyChatMessage.objects.filter(id=self.student_msg.id).exists()
        )
        self.assertFalse(
            StudyChatMessage.objects.filter(id=self.tupas_msg.id).exists()
        )

    def test_clear_chat_resets_transcript_with_fresh_welcome(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('tupas:study_buddy_clear_chat', args=[self.document.id]),
        )
        self.assertEqual(response.status_code, 302)
        messages_now = list(self.document.messages.all())
        self.assertEqual(len(messages_now), 1)
        self.assertEqual(messages_now[0].sender, StudyChatMessage.Sender.TUPAS)
        self.assertIn('Fresh start', messages_now[0].message)

    def test_edit_document_title_and_extracted_text(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('tupas:study_buddy_edit_doc', args=[self.document.id]),
            {
                'title': 'Biology (corrected)',
                'extracted_text': 'Corrected OCR text about cells and organelles.',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.document.refresh_from_db()
        self.assertEqual(self.document.title, 'Biology (corrected)')
        self.assertEqual(
            self.document.extracted_text,
            'Corrected OCR text about cells and organelles.',
        )

    def test_edit_document_rejects_empty_text(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('tupas:study_buddy_edit_doc', args=[self.document.id]),
            {'title': 'Still here', 'extracted_text': '   '},
        )
        self.assertEqual(response.status_code, 302)
        self.document.refresh_from_db()
        self.assertEqual(self.document.title, 'Biology Handout')

    def test_delete_document_removes_doc_and_chat(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('tupas:study_buddy_delete_doc', args=[self.document.id]),
        )
        self.assertRedirects(response, reverse('tupas:study_buddy'))
        self.assertFalse(StudyDocument.objects.filter(id=self.document.id).exists())
        self.assertEqual(StudyChatMessage.objects.count(), 0)

    def test_other_user_gets_404_on_all_document_actions(self):
        other = User.objects.create_user('other', 'other@example.com', 'StrongPass123!')
        self.client.force_login(other)
        urls = [
            reverse('tupas:study_buddy_detail', args=[self.document.id]),
            reverse('tupas:study_buddy_chat', args=[self.document.id]),
            reverse('tupas:study_buddy_clear_chat', args=[self.document.id]),
            reverse('tupas:study_buddy_edit_doc', args=[self.document.id]),
            reverse('tupas:study_buddy_delete_doc', args=[self.document.id]),
            reverse(
                'tupas:study_buddy_edit_message',
                args=[self.document.id, self.student_msg.id],
            ),
            reverse(
                'tupas:study_buddy_delete_message',
                args=[self.document.id, self.student_msg.id],
            ),
        ]
        for url in urls:
            with self.subTest(url=url):
                response = self.client.post(url, {'message': 'hi'})
                self.assertEqual(response.status_code, 404)

    def test_get_is_rejected_on_post_only_endpoints(self):
        self.client.force_login(self.user)
        urls = [
            reverse('tupas:study_buddy_chat', args=[self.document.id]),
            reverse('tupas:study_buddy_clear_chat', args=[self.document.id]),
            reverse('tupas:study_buddy_edit_doc', args=[self.document.id]),
            reverse('tupas:study_buddy_delete_doc', args=[self.document.id]),
            reverse(
                'tupas:study_buddy_edit_message',
                args=[self.document.id, self.student_msg.id],
            ),
            reverse(
                'tupas:study_buddy_delete_message',
                args=[self.document.id, self.student_msg.id],
            ),
        ]
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 405)


class SecurityHardeningTests(TupasBaseTestCase):
    def test_security_headers_are_present(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('tupas:profile'))
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(response['X-Frame-Options'], 'DENY')
        self.assertEqual(response['Referrer-Policy'], 'same-origin')
        self.assertIn('camera=()', response['Permissions-Policy'])
        self.assertIn("default-src 'self'", response['Content-Security-Policy'])
        self.assertIn("frame-ancestors 'none'", response['Content-Security-Policy'])
        self.assertIn('cdn.jsdelivr.net', response['Content-Security-Policy'])
        self.assertIn('cdnjs.cloudflare.com', response['Content-Security-Policy'])
        self.assertEqual(response['Cross-Origin-Opener-Policy'], 'same-origin')
        self.assertEqual(response['Cross-Origin-Resource-Policy'], 'same-origin')

    def test_authenticated_pages_are_no_store(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('tupas:profile'))
        cache_control = response.headers.get('Cache-Control', '')
        self.assertIn('no-store', cache_control)
        self.assertIn('private', cache_control)
        self.assertIn('Cookie', response.headers.get('Vary', ''))

    def test_session_cookie_is_httponly_and_samesite_lax(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('tupas:profile'))
        self.assertTrue(settings.SESSION_COOKIE_HTTPONLY)
        self.assertEqual(settings.SESSION_COOKIE_SAMESITE, 'Lax')
        set_cookie = response.cookies.get(settings.SESSION_COOKIE_NAME)
        if set_cookie is not None:
            self.assertTrue(set_cookie['httponly'])
            self.assertEqual(str(set_cookie['samesite']).lower(), 'lax')

    def test_chat_rate_limit_blocks_extra_messages(self):
        document = StudyDocument.objects.create(
            user=self.user,
            title='Biology',
            extracted_text='Cells are the basic unit of life. ' * 5,
        )
        self.client.force_login(self.user)
        with override_settings(TUPAS_CHAT_RATE_LIMIT=1, TUPAS_CHAT_RATE_WINDOW=300), \
                patch(
                    'tupas.views.study_buddy.chat_with_tupas_tutor',
                    return_value='ok',
                ):
            first = self.client.post(
                reverse('tupas:study_buddy_chat', args=[document.id]),
                {'message': 'First'},
            )
            second = self.client.post(
                reverse('tupas:study_buddy_chat', args=[document.id]),
                {'message': 'Second'},
            )
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        # Only the first pair of messages is stored.
        self.assertEqual(document.messages.count(), 2)
        self.assertContains(
            self.client.get(reverse('tupas:study_buddy_detail', args=[document.id])),
            'Study Buddy allowance',
        )

    def test_login_rate_limit_blocks_the_eleventh_attempt(self):
        with override_settings(TUPAS_LOGIN_RATE_LIMIT=2, TUPAS_LOGIN_RATE_WINDOW=300):
            # Two allowed attempts (they may fail auth; the brake still counts).
            self.client.post(reverse('tupas:login'), {
                'username': 'student', 'password': 'WrongPass123!',
            })
            self.client.post(reverse('tupas:login'), {
                'username': 'student', 'password': 'WrongPass123!',
            })
            third = self.client.post(reverse('tupas:login'), {
                'username': 'student', 'password': 'WrongPass123!',
            })
        self.assertEqual(third.status_code, 200)
        self.assertContains(third, 'Too many login attempts')

    def test_password_change_rate_limit(self):
        self.client.force_login(self.user)
        self.user.set_password('CurrentPass111!')
        self.user.save()
        self.client.force_login(self.user)
        with override_settings(TUPAS_PASSWORD_RATE_LIMIT=1, TUPAS_PASSWORD_RATE_WINDOW=900):
            first = self.client.post(reverse('tupas:profile'), {
                'action': 'change_password',
                'old_password': 'CurrentPass111!',
                'new_password1': 'BrandNewPass222!',
                'new_password2': 'BrandNewPass222!',
            })
            self.assertRedirects(first, reverse('tupas:profile'))
            second = self.client.post(reverse('tupas:profile'), {
                'action': 'change_password',
                'old_password': 'BrandNewPass222!',
                'new_password1': 'AnotherPass333!',
                'new_password2': 'AnotherPass333!',
            })
        self.assertRedirects(second, reverse('tupas:profile'))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('BrandNewPass222!'))
        self.assertFalse(self.user.check_password('AnotherPass333!'))

    def test_study_documents_are_always_owner_scoped(self):
        other = User.objects.create_user('other', 'other@example.com', 'StrongPass123!')
        other_doc = StudyDocument.objects.create(
            user=other,
            title='Private',
            extracted_text='Secret notes.',
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse('tupas:study_buddy'))
        self.assertNotContains(response, 'Private')
        self.assertEqual(
            self.client.get(
                reverse('tupas:study_buddy_detail', args=[other_doc.id])
            ).status_code,
            404,
        )

    def test_password_validators_are_configured(self):
        names = [v['NAME'] for v in settings.AUTH_PASSWORD_VALIDATORS]
        self.assertIn(
            'django.contrib.auth.password_validation.MinimumLengthValidator', names,
        )
        self.assertIn(
            'django.contrib.auth.password_validation.CommonPasswordValidator', names,
        )
        self.assertIn(
            'django.contrib.auth.password_validation.NumericPasswordValidator', names,
        )

    def test_security_check_command_passes(self):
        from io import StringIO
        buffer = StringIO()
        with override_settings(
            DEBUG=False,
            SECRET_KEY='test-production-key-not-insecure',
            ALLOWED_HOSTS=['testserver', 'localhost'],
            SESSION_COOKIE_SECURE=True,
            CSRF_COOKIE_SECURE=True,
            SECURE_HSTS_SECONDS=31536000,
            SECURE_HSTS_INCLUDE_SUBDOMAINS=True,
            SECURE_HSTS_PRELOAD=True,
            ADMINS=[('TUPAS Admin', 'ops@example.com')],
        ):
            call_command('security_check', stdout=buffer)
        output = buffer.getvalue()
        self.assertIn('checks passed', output)
        self.assertNotIn('[FAIL]', output)



class GamificationTests(TupasBaseTestCase):
    def test_good_mark_awards_xp_and_first_card_badge(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('tupas:mark_progress', args=[self.flip.id]),
            {'confidence': 'GOOD'},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['confidence'], 'GOOD')
        self.assertEqual(payload['status'], 'MASTERED')
        self.assertEqual(payload['xp_awarded'], 10)
        self.assertGreaterEqual(payload['xp_points'], 10)
        self.profile.refresh_from_db()
        self.assertIn('first_card', self.profile.badges)
        self.assertEqual(self.profile.level, 1 + self.profile.xp_points // 100)

    def test_again_schedules_ten_minutes(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('tupas:mark_progress', args=[self.flip.id]),
            {'confidence': 'AGAIN'},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.profile.refresh_from_db()
        self.assertEqual(response.json()['xp_awarded'], 5)
        progress = ItemProgress.objects.get(user=self.user, review_item=self.flip)
        self.assertEqual(progress.confidence_level, 'AGAIN')
        self.assertEqual(progress.mastery_status, 'NEEDS_REVIEW')
        delta = progress.next_review_at - timezone.now()
        self.assertLessEqual(delta.total_seconds(), 601)
        self.assertGreaterEqual(delta.total_seconds(), 540)

    def test_legacy_mastered_still_works(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('tupas:mark_progress', args=[self.flip.id]),
            {'status': 'MASTERED'},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['confidence'], 'GOOD')

    def test_typo_tolerance_accepts_close_spelling(self):
        from .services.grading import check_answer_match
        is_correct, is_typo = check_answer_match('photosythesis', 'photosynthesis')
        self.assertTrue(is_correct)
        self.assertTrue(is_typo)
        is_correct, is_typo = check_answer_match('hello', 'hello')
        self.assertTrue(is_correct)
        self.assertFalse(is_typo)
        is_correct, is_typo = check_answer_match('cat', 'dog')
        self.assertFalse(is_correct)

    def test_leaderboard_requires_login(self):
        response = self.client.get(reverse('tupas:leaderboard'))
        self.assertEqual(response.status_code, 302)

    def test_leaderboard_lists_opt_in_students(self):
        self.client.force_login(self.user)
        award_xp(self.user, 25)
        response = self.client.get(reverse('tupas:leaderboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Maria Santos')
        self.assertContains(response, 'XP')

    def test_leaderboard_hides_admin_accounts(self):
        self.client.force_login(self.user)
        # Create an admin user with opt-in leaderboard profile and high XP
        admin_user = User.objects.create_user(
            username='admin_boss', email='admin@tupas.edu.ph', password='pass', is_staff=True,
        )
        StudentProfile.objects.create(
            user=admin_user, full_name='Admin Boss', show_on_leaderboard=True, xp_points=99999,
        )
        response = self.client.get(reverse('tupas:leaderboard'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Admin Boss')



class ExamModeUpgradeTests(TupasBaseTestCase):
    def test_true_false_exam_roundtrip(self):
        self.client.force_login(self.user)
        response = self.client.get(
            reverse('tupas:exam_mode', args=[self.deck.id, 'true-false']),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'True or False')
        # Submit True for the flip card using the rendered statement.
        # The view randomizes True/False, so we just check a paper is built.
        self.assertTrue(response.context['questions'])

    def test_matching_exam_roundtrip(self):
        self.client.force_login(self.user)
        response = self.client.get(
            reverse('tupas:exam_mode', args=[self.deck.id, 'matching']),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['questions'])
        self.assertIn('right_pool', response.context['questions'][0])

    def test_exam_saves_details_json_and_xp(self):
        self.client.force_login(self.user)
        # Use identification so we can control the answer.
        url = reverse('tupas:exam_mode', args=[self.deck.id, 'identification'])
        page = self.client.get(url)
        questions = page.context['questions']
        self.assertTrue(questions)
        item = questions[0]['item']
        form_data = {
            'exam_seed': page.context['exam_seed'],
            'attempt_token': page.context['attempt_token'],
            'exam_filter': 'all',
            'retry_ids': '',
            f'q_{item.id}': item.main_answer or 'answer',
        }
        response = self.client.post(url, form_data)
        self.assertEqual(response.status_code, 200)
        attempt = ExamAttempt.objects.latest('id')
        self.assertIn('details_json', [f.name for f in ExamAttempt._meta.fields])
        details = json.loads(attempt.details_json or '[]')
        self.assertTrue(details)
        self.assertIn('prompt', details[0])
        self.assertIn('item_id', details[0])

    def test_exam_attempt_detail_is_owner_only(self):
        self.client.force_login(self.user)
        attempt = ExamAttempt.objects.create(
            user=self.user,
            deck=self.deck,
            exam_mode=ExamAttempt.ExamMode.IDENTIFICATION,
            score=1,
            total_items=1,
            percentage=100,
            details_json='[]',
        )
        other = User.objects.create_user('other', 'other@example.com', 'StrongPass123!')
        self.client.force_login(other)
        response = self.client.get(
            reverse('tupas:exam_attempt_detail', args=[attempt.id]),
        )
        self.assertEqual(response.status_code, 404)


class DeckUpgradeTests(TupasBaseTestCase):
    def test_pin_and_archive_toggle(self):
        self.client.force_login(self.user)
        self.assertFalse(self.deck.is_pinned)
        self.client.post(reverse('tupas:toggle_pin_deck', args=[self.deck.id]))
        self.deck.refresh_from_db()
        self.assertTrue(self.deck.is_pinned)
        self.client.post(reverse('tupas:toggle_archive_deck', args=[self.deck.id]))
        self.deck.refresh_from_db()
        self.assertTrue(self.deck.is_archived)

    def test_other_user_cannot_pin(self):
        other = User.objects.create_user('other', 'other@example.com', 'StrongPass123!')
        self.client.force_login(other)
        response = self.client.post(reverse('tupas:toggle_pin_deck', args=[self.deck.id]))
        self.assertEqual(response.status_code, 403)

    def test_export_json(self):
        self.client.force_login(self.user)
        response = self.client.get(
            reverse('tupas:export_deck', args=[self.deck.id]) + '?format=json',
        )
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(payload['title'], self.deck.title)
        self.assertTrue(payload['items'])

    def test_import_quizlet_tsv(self):
        self.client.force_login(self.user)
        tsv = 'Term\tDefinition\nMitosis\tCell division\nOsmosis\tWater movement\n'
        response = self.client.post(reverse('tupas:import_deck'), {
            'title': 'Imported Bio',
            'subject_name': 'Biology',
            'payload': tsv,
        })
        deck = Deck.objects.get(title='Imported Bio')
        self.assertRedirects(
            response,
            reverse('tupas:deck_detail', args=[deck.id]),
            fetch_redirect_response=False,
        )
        self.assertEqual(deck.items.count(), 2)

    def test_merge_skips_duplicates(self):
        other = User.objects.create_user('other', 'other@example.com', 'StrongPass123!')
        source = Deck.objects.create(
            subject=self.subject,
            title='Source deck',
            created_by=other,
        )
        ReviewItem.objects.create(
            deck=source,
            item_type=ReviewItem.ItemType.FLIP_CARD,
            prompt_text=self.flip.prompt_text,
            main_answer='duplicate should skip',
        )
        unique = ReviewItem.objects.create(
            deck=source,
            item_type=ReviewItem.ItemType.FLIP_CARD,
            prompt_text='Unique prompt about routers',
            main_answer='router',
        )
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('tupas:merge_deck', args=[self.deck.id]),
            {'source_deck': source.id},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            self.deck.items.filter(prompt_text='Unique prompt about routers').count(),
            1,
        )
        self.assertEqual(
            self.deck.items.filter(prompt_text=self.flip.prompt_text).count(),
            1,
        )

    def test_explain_deeper_returns_json(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('tupas:explain_deeper', args=[self.flip.id]),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ok'])

    def test_item_feedback_sets_quality_flag(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('tupas:item_feedback', args=[self.flip.id]),
            {'flag': 'GOOD'},
        )
        self.assertEqual(response.status_code, 200)
        self.flip.refresh_from_db()
        self.assertEqual(self.flip.quality_flag, 'GOOD')


class ProfileGamificationTests(TupasBaseTestCase):
    def test_profile_shows_xp_and_target_exam(self):
        self.client.force_login(self.user)
        award_xp(self.user, 30)
        self.profile.target_exam_name = 'Midterms'
        self.profile.target_exam_date = date.today() + timedelta(days=7)
        self.profile.save()
        response = self.client.get(reverse('tupas:profile'))
        self.assertContains(response, 'XP, Level')
        self.assertContains(response, 'Midterms')
        self.assertContains(response, 'Daily study reminder')

    def test_dashboard_shows_level_and_countdown(self):
        self.client.force_login(self.user)
        award_xp(self.user, 120)
        self.profile.target_exam_date = date.today() + timedelta(days=3)
        self.profile.target_exam_name = 'Finals'
        self.profile.save()
        response = self.client.get(reverse('tupas:dashboard'))
        self.assertContains(response, 'Lv')
        self.assertContains(response, 'Finals')
        self.assertContains(response, '3 days')



class RBACTests(TupasBaseTestCase):
    """Two roles only: ADMIN (is_staff / superuser) and STUDENT."""

    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_user(
            'adminuser', 'admin@example.com', 'AdminPass123!', is_staff=True,
        )

    def test_effective_role(self):
        from .services.rbac import effective_role
        self.assertEqual(effective_role(self.admin), 'ADMIN')
        self.assertEqual(effective_role(self.user), 'STUDENT')
        self.assertIsNone(effective_role(None))

    def test_student_cannot_open_user_list(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('tupas:user_list'))
        self.assertEqual(response.status_code, 403)

    def test_admin_can_view_and_change_role(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('tupas:user_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.user.username)

        response = self.client.post(
            reverse('tupas:set_user_role', args=[self.user.id]),
            {'role': 'ADMIN'},
        )
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_staff)
        from .services.rbac import effective_role
        self.assertEqual(effective_role(self.user), 'ADMIN')

    def test_admin_can_demote_to_student(self):
        self.client.force_login(self.admin)
        self.client.post(
            reverse('tupas:set_user_role', args=[self.user.id]),
            {'role': 'STUDENT'},
        )
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_staff)

    def test_admin_cannot_demote_superuser(self):
        self.admin.is_superuser = True
        self.admin.save()
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse('tupas:set_user_role', args=[self.admin.id]),
            {'role': 'STUDENT'},
        )
        self.assertEqual(response.status_code, 302)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_superuser)

    def test_rejects_teacher_and_junk_roles(self):
        self.client.force_login(self.admin)
        for bad in ('TEACHER', 'GOD', ''):
            response = self.client.post(
                reverse('tupas:set_user_role', args=[self.user.id]),
                {'role': bad},
            )
            self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_staff)

    def test_admin_can_set_password(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse('tupas:admin_set_password', args=[self.user.id]),
            {'new_password1': 'BrandNewPass123!', 'new_password2': 'BrandNewPass123!'},
        )
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('BrandNewPass123!'))
        self.assertTrue(
            ActivityLog.objects.filter(
                action=ActivityLog.Action.PASSWORD_CHANGE,
                detail__contains=self.user.username,
            ).exists()
        )

    def test_password_mismatch_is_rejected(self):
        self.client.force_login(self.admin)
        old = self.user.password
        response = self.client.post(
            reverse('tupas:admin_set_password', args=[self.user.id]),
            {'new_password1': 'BrandNewPass123!', 'new_password2': 'Different123!'},
        )
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(self.user.password, old)

    def test_admin_can_edit_account_details(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse('tupas:admin_edit_account', args=[self.user.id]),
            {
                'first_name': 'Maria',
                'last_name': 'Santos',
                'email': 'maria@example.com',
                'is_active': 'on',
                'role': 'STUDENT',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, 'Maria')
        self.assertEqual(self.user.email, 'maria@example.com')
        self.assertTrue(self.user.is_active)
        self.assertFalse(self.user.is_staff)

    def test_admin_can_deactivate_account(self):
        self.client.force_login(self.admin)
        self.client.post(
            reverse('tupas:admin_edit_account', args=[self.user.id]),
            {
                'first_name': 'Maria',
                'last_name': 'Santos',
                'email': 'maria@example.com',
                'role': 'STUDENT',
            },
        )
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)

    def test_cannot_remove_last_admin(self):
        self.admin.is_superuser = False
        self.admin.save()
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse('tupas:set_user_role', args=[self.admin.id]),
            {'role': 'STUDENT'},
        )
        self.assertEqual(response.status_code, 302)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_staff)

    def test_only_admin_and_student_exist(self):
        from .services.rbac import effective_role
        self.assertEqual(effective_role(self.admin), 'ADMIN')
        self.assertEqual(effective_role(self.user), 'STUDENT')
        self.profile.refresh_from_db()
        self.assertNotEqual(self.profile.role, 'TEACHER')

    def test_csrf_is_required_on_role_change(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        response = client.post(
            reverse('tupas:set_user_role', args=[self.user.id]),
            {'role': 'ADMIN'},
        )
        self.assertEqual(response.status_code, 403)

    def test_security_headers_still_present_on_users_page(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('tupas:user_list'))
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(response['X-Frame-Options'], 'DENY')
        self.assertIn('default-src', response['Content-Security-Policy'])


class RequestShieldTests(TupasBaseTestCase):
    """Anti-SQLi / anti-scanner / anti-flood middleware and login lockout."""

    def setUp(self):
        super().setUp()
        cache.clear()
        self.user.set_password('ShieldTest123!')
        self.user.save()

    def test_normal_search_is_not_blocked(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('tupas:dashboard'), {'q': 'Networking Basics'})
        self.assertEqual(response.status_code, 200)

    def test_sql_injection_in_querystring_is_blocked(self):
        response = self.client.get(
            reverse('tupas:login'),
            {'q': "1' OR 1=1 --"},
        )
        self.assertEqual(response.status_code, 400)

    def test_union_select_in_querystring_is_blocked(self):
        response = self.client.get(
            reverse('tupas:login'),
            {'q': '1 UNION SELECT username, password FROM users'},
        )
        self.assertEqual(response.status_code, 400)

    def test_encoded_sqli_is_blocked(self):
        response = self.client.get('/login/?q=1%27%20OR%201%3D1%20--')
        self.assertEqual(response.status_code, 400)

    def test_scanner_env_path_is_404(self):
        response = self.client.get('/.env')
        self.assertEqual(response.status_code, 404)

    def test_git_probe_is_404(self):
        response = self.client.get('/.git/config')
        self.assertEqual(response.status_code, 404)

    def test_php_probe_is_404(self):
        response = self.client.get('/wp-login.php')
        self.assertEqual(response.status_code, 404)

    def test_sqlmap_user_agent_is_blocked(self):
        response = self.client.get(
            reverse('tupas:login'),
            HTTP_USER_AGENT='sqlmap/1.7.11#stable',
        )
        self.assertEqual(response.status_code, 403)

    def test_request_flood_returns_429(self):
        self.client.force_login(self.user)
        with override_settings(TUPAS_REQ_LIMIT=3, TUPAS_REQ_WINDOW=60):
            cache.clear()
            for _ in range(3):
                response = self.client.get(reverse('tupas:dashboard'))
                self.assertEqual(response.status_code, 200)
            flooded = self.client.get(reverse('tupas:dashboard'))
        self.assertEqual(flooded.status_code, 429)
        self.assertTrue(flooded.has_header('Retry-After'))

    def test_health_and_static_are_exempt_from_throttle(self):
        with override_settings(TUPAS_REQ_LIMIT=1, TUPAS_REQ_WINDOW=60):
            cache.clear()
            for _ in range(5):
                response = self.client.get(reverse('tupas:health'))
                self.assertEqual(response.status_code, 200)

    def test_shield_hit_bans_ip_after_threshold(self):
        with override_settings(TUPAS_SHIELD_BAN_AFTER=2, TUPAS_SHIELD_BAN_SECONDS=900):
            cache.clear()
            self.client.get('/.env')
            self.client.get('/.env')
            banned = self.client.get(reverse('tupas:login'))
        self.assertEqual(banned.status_code, 429)

    def test_oversized_body_is_413(self):
        with override_settings(TUPAS_MAX_BODY_BYTES=100):
            response = self.client.post(
                reverse('tupas:login'),
                {'username': 'student', 'password': 'x' * 500},
            )
        self.assertEqual(response.status_code, 413)

    def test_username_lockout_blocks_login(self):
        with override_settings(
            TUPAS_LOGIN_USER_LIMIT=1,
            TUPAS_LOGIN_USER_WINDOW=900,
            TUPAS_LOGIN_RATE_LIMIT=50,
        ):
            first = self.client.post(reverse('tupas:login'), {
                'username': 'student', 'password': 'WrongPass123!',
            })
            self.assertEqual(first.status_code, 200)
            second = self.client.post(reverse('tupas:login'), {
                'username': 'student', 'password': 'ShieldTest123!',
            })
        self.assertEqual(second.status_code, 200)
        self.assertContains(second, 'Too many failed attempts for that account')

    def test_successful_login_clears_username_lockout(self):
        from .services.quotas import username_login_allowed
        with override_settings(
            TUPAS_LOGIN_USER_LIMIT=1,
            TUPAS_LOGIN_USER_WINDOW=900,
            TUPAS_LOGIN_RATE_LIMIT=50,
        ):
            first = self.client.post(reverse('tupas:login'), {
                'username': 'student', 'password': 'WrongPass123!',
            })
            self.assertEqual(first.status_code, 200)
            self.assertFalse(username_login_allowed('student'))
            # Correct password cannot get in while locked.
            locked = self.client.post(reverse('tupas:login'), {
                'username': 'student', 'password': 'ShieldTest123!',
            })
            self.assertContains(locked, 'Too many failed attempts for that account')

        # A real successful login resets the counter.
        client = Client()
        response = client.post(reverse('tupas:login'), {
            'username': 'student', 'password': 'ShieldTest123!',
        })
        self.assertRedirects(response, reverse('tupas:dashboard'))
        self.assertTrue(username_login_allowed('student'))

    def test_other_usernames_are_not_locked_together(self):
        from .services.quotas import record_username_failure, username_login_allowed
        with override_settings(TUPAS_LOGIN_USER_LIMIT=1, TUPAS_LOGIN_USER_WINDOW=900):
            cache.clear()
            record_username_failure('student')
            self.assertFalse(username_login_allowed('student'))
            self.assertTrue(username_login_allowed('someoneelse'))

    def test_shield_can_be_disabled(self):
        with override_settings(TUPAS_SHIELD_ENABLED=False):
            response = self.client.get('/.env')
            self.assertEqual(response.status_code, 404)  # Django URL 404, not shield

    def test_security_headers_still_on_blocked_response(self):
        response = self.client.get('/.env')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.assertIn('default-src', response['Content-Security-Policy'])


class AdminSeparationTests(TupasBaseTestCase):
    """Admins get their own dashboard and cannot use student study tools."""

    def setUp(self):
        super().setUp()
        cache.clear()
        self.admin = User.objects.create_user(
            'adminuser', 'admin@example.com', 'AdminPass123!', is_staff=True,
        )

    def test_admin_gets_admin_dashboard_not_student(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('tupas:dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Admin dashboard')
        self.assertNotContains(response, 'Student Dashboard')
        self.assertNotContains(response, 'Ready for focus')

    def test_student_still_gets_student_dashboard(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('tupas:dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Student Dashboard')

    def test_admin_cannot_open_student_features(self):
        self.client.force_login(self.admin)
        urls = [
            reverse('tupas:flip_mode', args=[self.deck.id]),
            reverse('tupas:highlight_mode', args=[self.deck.id]),
            reverse('tupas:enumeration_mode', args=[self.deck.id]),
            reverse('tupas:exam_mode', args=[self.deck.id, 'multiple-choice']),
            reverse('tupas:leaderboard'),
            reverse('tupas:deck_cram_sheet', args=[self.deck.id]),
            reverse('tupas:clone_deck', args=[self.deck.id]),
        ]
        for url in urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302, url)
            self.assertEqual(response.url, reverse('tupas:dashboard'), url)

    def test_admin_cannot_open_ai_generator(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('tupas:ai_generator'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('tupas:dashboard'))

    def test_admin_cannot_open_study_buddy(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('tupas:study_buddy'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('tupas:dashboard'))

    def test_admin_ajax_student_feature_is_403(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse('tupas:mark_progress', args=[self.flip.id]),
            {'status': 'MASTERED'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_can_open_users_and_deck_detail(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('tupas:user_list')).status_code, 200)
        self.assertEqual(
            self.client.get(reverse('tupas:deck_detail', args=[self.deck.id])).status_code,
            200,
        )
        self.assertEqual(self.client.get('/admin/').status_code, 200)

    def test_deck_detail_hides_student_tools_for_admin(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('tupas:deck_detail', args=[self.deck.id]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Generate more with AI')
        self.assertNotContains(response, 'Exam Simulator')
        self.assertNotContains(response, 'Flip Card Mode')

    def test_login_creates_activity_log(self):
        self.client.force_login(self.user)
        self.assertTrue(
            ActivityLog.objects.filter(user=self.user, action=ActivityLog.Action.LOGIN).exists()
        )

    def test_failed_login_is_logged(self):
        self.client.post(reverse('tupas:login'), {
            'username': 'student', 'password': 'DefinitelyWrong123!',
        })
        self.assertTrue(
            ActivityLog.objects.filter(action=ActivityLog.Action.LOGIN_FAILED).exists()
        )

    def test_admin_dashboard_lists_activity(self):
        self.client.force_login(self.admin)
        record = ActivityLog.objects.create(
            user=self.user,
            action=ActivityLog.Action.REGISTER,
            detail='student',
        )
        response = self.client.get(reverse('tupas:dashboard'))
        self.assertContains(response, 'Activity log')
        self.assertContains(response, 'student')

    def test_role_change_is_logged(self):
        self.client.force_login(self.admin)
        self.client.post(
            reverse('tupas:set_user_role', args=[self.user.id]),
            {'role': 'ADMIN'},
        )
        self.assertTrue(
            ActivityLog.objects.filter(
                user=self.admin,
                action=ActivityLog.Action.ROLE_CHANGE,
            ).exists()
        )


class ActivityLogHardeningTests(TupasBaseTestCase):
    """The trail is undeletable, attributed, masked, and prunable."""

    def setUp(self):
        super().setUp()
        cache.clear()
        self.admin = User.objects.create_superuser(
            'adminuser', 'admin@example.com', 'AdminPass123!',
        )

    def test_failed_login_masks_identifier(self):
        self.client.post(reverse('tupas:login'), {
            'username': 'lourdianrentinoakol@gmail.com',
            'password': 'definitely-wrong',
        })
        row = ActivityLog.objects.filter(
            action=ActivityLog.Action.LOGIN_FAILED,
        ).latest('created_at')
        self.assertIn('*', row.detail)
        self.assertNotIn('lourdianrentinoakol@gmail.com', row.detail)
        self.assertEqual(row.username_snapshot, '')

    def test_activity_log_cannot_be_deleted_via_admin(self):
        row = ActivityLog.objects.create(
            user=self.user,
            action=ActivityLog.Action.LOGIN,
            detail='keep me',
        )
        self.client.force_login(self.admin)
        response = self.client.get(f'/admin/tupas/activitylog/{row.id}/delete/')
        self.assertEqual(response.status_code, 403)
        self.assertTrue(ActivityLog.objects.filter(pk=row.pk).exists())
        changelist = self.client.get('/admin/tupas/activitylog/')
        self.assertEqual(changelist.status_code, 200)
        self.assertNotContains(changelist, 'delete_selected')

    def test_snapshots_survive_user_delete(self):
        record_activity(self.user, ActivityLog.Action.LOGIN, 'snap')
        row = ActivityLog.objects.latest('created_at')
        self.assertEqual(row.username_snapshot, self.user.username)
        self.assertEqual(row.user_id_snapshot, self.user.pk)
        user_pk = self.user.pk
        username = self.user.username
        self.user.delete()
        row.refresh_from_db()
        self.assertIsNone(row.user)
        self.assertEqual(row.username_snapshot, username)
        self.assertEqual(row.user_id_snapshot, user_pk)

    def test_prune_activity_drops_old_rows(self):
        old = ActivityLog.objects.create(
            user=self.user,
            action=ActivityLog.Action.LOGIN,
            detail='old',
        )
        ActivityLog.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(days=400),
        )
        fresh = ActivityLog.objects.create(
            user=self.user,
            action=ActivityLog.Action.LOGIN,
            detail='fresh',
        )
        call_command('prune_activity', days=180, keep_sessions=True)
        self.assertFalse(ActivityLog.objects.filter(pk=old.pk).exists())
        self.assertTrue(ActivityLog.objects.filter(pk=fresh.pk).exists())

    def test_export_deck_is_logged(self):
        self.client.force_login(self.user)
        self.client.get(reverse('tupas:export_deck', args=[self.deck.id]))
        self.assertTrue(
            ActivityLog.objects.filter(
                user=self.user,
                action=ActivityLog.Action.EXPORT,
            ).exists()
        )

    def test_dashboard_activity_filters_and_csv(self):
        self.client.force_login(self.admin)
        ActivityLog.objects.create(
            user=self.user,
            action=ActivityLog.Action.EXPORT,
            detail='needle',
        )
        response = self.client.get(reverse('tupas:dashboard'), {'q': 'needle'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['activity_rows']), 1)
        csv_response = self.client.get(reverse('tupas:dashboard'), {'export': 'csv'})
        self.assertEqual(csv_response.status_code, 200)
        self.assertIn('tupas-activity.csv', csv_response['Content-Disposition'])
        self.assertIn(b'needle', csv_response.content)

    def test_health_answers_on_both_slash_forms(self):
        self.assertEqual(self.client.get('/health/').status_code, 200)
        self.assertEqual(self.client.get('/health').status_code, 200)

    def test_v1_models_is_probe_blocked(self):
        response = self.client.get('/v1/models')
        self.assertEqual(response.status_code, 404)
        self.assertTrue(response.has_header('X-TUPAS-Shield'))
        self.assertTrue(
            ActivityLog.objects.filter(action=ActivityLog.Action.SHIELD_BLOCK).exists()
        )

    def test_request_id_flows_into_activity(self):
        self.client.post(
            reverse('tupas:login'),
            {'username': 'student', 'password': 'not-the-password'},
            HTTP_X_REQUEST_ID='test-rid-123',
        )
        row = ActivityLog.objects.filter(
            action=ActivityLog.Action.LOGIN_FAILED,
        ).latest('created_at')
        self.assertEqual(row.request_id, 'test-rid-123')

    def test_trusted_proxy_ip_is_used_only_when_configured(self):
        from django.test import Client
        from django.test.utils import override_settings

        record_activity(self.user, ActivityLog.Action.LOGIN, 'no request')
        row = ActivityLog.objects.latest('created_at')
        self.assertIsNone(row.ip_address)

        request = Client(
            HTTP_X_FORWARDED_FOR='203.0.113.7', REMOTE_ADDR='127.0.0.1',
        ).get('/health/').wsgi_request
        record_activity(self.user, ActivityLog.Action.LOGIN, 'untrusted xff', request)
        row = ActivityLog.objects.latest('created_at')
        self.assertEqual(row.ip_address, '127.0.0.1')

        with override_settings(TUPAS_TRUSTED_PROXIES=['127.0.0.1']):
            request = Client(
                HTTP_X_FORWARDED_FOR='203.0.113.7', REMOTE_ADDR='127.0.0.1',
            ).get('/health/').wsgi_request
            record_activity(self.user, ActivityLog.Action.LOGIN, 'via proxy', request)
        latest = ActivityLog.objects.latest('created_at')
        self.assertEqual(latest.ip_address, '203.0.113.7')

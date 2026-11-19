"""Tests for handout uploads, AI item-count controls, and coverage reports."""

import io
import tempfile
import zipfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from .forms import AIReviewerForm, GenerateDeckFromDocForm, StudyDocumentUploadForm
from .models import StudyDocument
from .services.coverage import coverage_shortfalls, coverage_warning
from .services.documents import extract_uploaded_text
from .tests import TupasBaseTestCase


def _pptx_upload():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr(
            'ppt/slides/slide1.xml',
            '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            '<a:t>Cell theory states that cells are the unit of life.</a:t></p:sld>',
        )
        archive.writestr(
            'ppt/slides/slide2.xml',
            '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            '<a:t>Mitosis has four main phases.</a:t></p:sld>',
        )
    return SimpleUploadedFile(
        'lecture.pptx',
        buffer.getvalue(),
        content_type=(
            'application/vnd.openxmlformats-officedocument.'
            'presentationml.presentation'
        ),
    )


def _docx_upload():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr(
            'word/document.xml',
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body>'
            '<w:p><w:r><w:t>Water boils at 100 degrees Celsius.</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>Photosynthesis converts light into chemical energy.</w:t></w:r></w:p>'
            '</w:body></w:document>',
        )
    return SimpleUploadedFile(
        'notes.docx',
        buffer.getvalue(),
        content_type=(
            'application/vnd.openxmlformats-officedocument.'
            'wordprocessingml.document'
        ),
    )


class DocumentExtractionTests(TestCase):
    def test_pptx_text_is_extracted_per_slide(self):
        text, slide_count = extract_uploaded_text(_pptx_upload())

        self.assertEqual(slide_count, 2)
        self.assertIn('Cell theory', text)
        self.assertIn('Mitosis has four main phases', text)
        self.assertIn('--- [Slide 1] ---', text)

    def test_docx_text_is_extracted(self):
        text, page_count = extract_uploaded_text(_docx_upload())

        self.assertEqual(page_count, 1)
        self.assertIn('Water boils at 100 degrees Celsius.', text)
        self.assertIn('Photosynthesis converts light', text)

    def test_plain_text_is_extracted(self):
        upload = SimpleUploadedFile('notes.txt', b'Review this chapter on ethics.')
        text, page_count = extract_uploaded_text(upload)

        self.assertEqual(text, 'Review this chapter on ethics.')
        self.assertEqual(page_count, 1)


class UploadFormTests(TupasBaseTestCase):
    def test_pptx_is_accepted_by_the_study_buddy_form(self):
        form = StudyDocumentUploadForm(files={'pdf_file': _pptx_upload()})

        self.assertTrue(form.is_valid(), form.errors)

    def test_unsupported_extension_is_rejected(self):
        form = StudyDocumentUploadForm(
            files={'pdf_file': SimpleUploadedFile('notes.exe', b'not a handout')}
        )

        self.assertFalse(form.is_valid())
        self.assertIn('__all__', form.errors)

    def test_student_can_type_a_custom_item_count(self):
        form = GenerateDeckFromDocForm(
            data={'deck_title': 'Midterms', 'items_count': '17'}
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['items_count'], 17)

    def test_item_count_is_capped_at_thirty(self):
        form = GenerateDeckFromDocForm(
            data={'deck_title': 'Midterms', 'items_count': '31'}
        )

        self.assertFalse(form.is_valid())
        self.assertIn('items_count', form.errors)

    def test_ai_form_accepts_depth_choices_and_exhaustive_mode(self):
        form = AIReviewerForm(
            {
                'notes': 'x' * 120,
                'item_types': ['FLIP_CARD'],
                'deck': self.deck.id,
                'items_per_type': '12',
                'exam_style': 'EXACT',
                'exhaustive': 'on',
            },
            user=self.user,
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['items_per_type'], '12')
        self.assertTrue(form.cleaned_data['exhaustive'])

    def test_ai_form_accepts_all_depth(self):
        form = AIReviewerForm(
            {
                'notes': 'x' * 120,
                'item_types': ['FLIP_CARD'],
                'deck': self.deck.id,
                'items_per_type': 'ALL',
                'exam_style': 'EXACT',
            },
            user=self.user,
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['items_per_type'], 'ALL')


class StudyBuddyUploadTests(TupasBaseTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_pptx_upload_is_saved_and_searchable(self):
        with tempfile.TemporaryDirectory() as media_root:
            with self.settings(MEDIA_ROOT=media_root):
                response = self.client.post(
                    reverse('tupas:study_buddy'),
                    {'title': 'Cell Biology', 'pdf_file': _pptx_upload()},
                )

        self.assertEqual(response.status_code, 302)
        document = StudyDocument.objects.get(user=self.user)
        self.assertEqual(document.title, 'Cell Biology')
        self.assertEqual(document.page_count, 2)
        self.assertIn('Cell theory', document.extracted_text)
        self.assertIn('Mitosis', document.extracted_text)


class CoverageReportTests(TestCase):
    def test_shortfalls_list_only_types_below_the_request(self):
        counts = {
            'flip_cards': 6,
            'highlight_notes': 10,
            'enumerations': 4,
        }

        self.assertEqual(
            coverage_shortfalls(counts, 10),
            ['6 of 10 flip cards', '4 of 10 enumeration drills'],
        )

    def test_warning_names_the_shortfall_and_does_not_claim_full_coverage(self):
        message = coverage_warning(['6 of 10 flip cards'])

        self.assertIn('6 of 10 flip cards', message)
        self.assertIn('never pads with invented facts', message)

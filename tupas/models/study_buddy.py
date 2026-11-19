"""Study Buddy handout and tutor conversation tables."""

from django.conf import settings
from django.db import models

from .content import Deck


class StudyDocument(models.Model):
    """Uploaded PDF or pasted study document for AI tutoring and deck generation."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='documents',
    )
    title = models.CharField(max_length=200)
    pdf_file = models.FileField(upload_to='study_pdfs/', null=True, blank=True)
    extracted_text = models.TextField(blank=True)
    page_count = models.PositiveIntegerField(default=1)
    generated_deck = models.ForeignKey(
        Deck,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='source_documents',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'study document'
        verbose_name_plural = 'study documents'

    def __str__(self):
        return f'{self.title} ({self.user.username})'

    @property
    def word_count(self):
        if not self.extracted_text:
            return 0
        return len(self.extracted_text.split())


class StudyChatMessage(models.Model):
    """Interactive conversational tutoring message between Student and TUPAS Study Buddy."""

    class Sender(models.TextChoices):
        STUDENT = 'STUDENT', 'Student'
        TUPAS = 'TUPAS', 'TUPAS'

    document = models.ForeignKey(
        StudyDocument,
        on_delete=models.CASCADE,
        related_name='messages',
    )
    sender = models.CharField(max_length=20, choices=Sender.choices)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']
        verbose_name = 'study chat message'
        verbose_name_plural = 'study chat messages'

    def __str__(self):
        return f'{self.sender} on {self.document.title}: {self.message[:40]}'

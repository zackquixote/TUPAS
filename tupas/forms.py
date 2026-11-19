from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm, UserCreationForm
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import transaction

from .models import Deck, StudentProfile, StudyDocument, Subject


class BootstrapFormMixin:
    """Apply Bootstrap 5 classes and sensible autofocus behaviour."""

    def _style_fields(self):
        for name, field in self.fields.items():
            widget = field.widget
            if isinstance(widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple)):
                css_class = 'form-check-input'
            elif isinstance(widget, forms.Select):
                css_class = 'form-select'
            else:
                css_class = 'form-control'

            existing = widget.attrs.get('class', '')
            widget.attrs['class'] = f'{existing} {css_class}'.strip()



class StudentRegistrationForm(BootstrapFormMixin, UserCreationForm):
    """Create a Django User and its matching StudentProfile in one transaction."""

    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={'placeholder': 'you@example.com'}),
    )
    full_name = forms.CharField(
        max_length=200,
        widget=forms.TextInput(attrs={'placeholder': 'Juan Dela Cruz'}),
    )
    birthdate = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date'}),
    )
    region = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={'placeholder': 'e.g. Region VI - Western Visayas'}),
    )
    province = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={'placeholder': 'e.g. Negros Occidental'}),
    )
    city_municipality = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={'placeholder': 'e.g. Kabankalan City'}),
    )
    school_name = forms.CharField(
        max_length=200,
        widget=forms.TextInput(attrs={'placeholder': 'e.g. Kabankalan City National High School'}),
    )
    student_level = forms.ChoiceField(
        choices=StudentProfile.StudentLevel.choices,
        widget=forms.Select(),
    )
    course_or_strand = forms.CharField(
        max_length=150,
        widget=forms.TextInput(attrs={'placeholder': 'e.g. BS Information Technology'}),
    )
    year_level = forms.CharField(
        max_length=50,
        widget=forms.TextInput(attrs={'placeholder': 'e.g. 3rd Year'}),
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('username', 'email')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].widget.attrs.update({
            'placeholder': 'Choose a username',
            'autofocus': True,
        })
        self.fields['password1'].widget.attrs.update({'placeholder': 'Create a password'})
        self.fields['password2'].widget.attrs.update({'placeholder': 'Repeat the password'})
        self._style_fields()

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError('An account with this email already exists.')
        return email

    @transaction.atomic
    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data['email']

        if commit:
            user.save()
            StudentProfile.objects.create(
                user=user,
                full_name=self.cleaned_data['full_name'],
                birthdate=self.cleaned_data.get('birthdate'),
                region=self.cleaned_data['region'],
                province=self.cleaned_data['province'],
                city_municipality=self.cleaned_data['city_municipality'],
                school_name=self.cleaned_data['school_name'],
                student_level=self.cleaned_data['student_level'],
                course_or_strand=self.cleaned_data['course_or_strand'],
                year_level=self.cleaned_data['year_level'],
            )
        return user


class TupasAuthenticationForm(BootstrapFormMixin, AuthenticationForm):
    """Bootstrap-styled Django login form."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].widget.attrs.update({
            'placeholder': 'Username',
            'autofocus': True,
        })
        self.fields['password'].widget.attrs.update({'placeholder': 'Password'})
        self._style_fields()


class StudentProfileUpdateForm(BootstrapFormMixin, forms.ModelForm):
    """Edit the signed-in student's profile plus their login email."""

    email = forms.EmailField(
        required=True,
        label='Email',
        widget=forms.EmailInput(attrs={'placeholder': 'you@example.com'}),
    )

    class Meta:
        model = StudentProfile
        fields = (
            'full_name',
            'birthdate',
            'school_name',
            'student_level',
            'course_or_strand',
            'year_level',
            'region',
            'province',
            'city_municipality',
            'daily_goal',
            'show_on_leaderboard',
            'target_exam_name',
            'target_exam_date',
        )
        widgets = {
            'full_name': forms.TextInput(attrs={'placeholder': 'Juan Dela Cruz'}),
            'birthdate': forms.DateInput(attrs={'type': 'date'}),
            'school_name': forms.TextInput(
                attrs={'placeholder': 'e.g. Kabankalan City National High School'},
            ),
            'course_or_strand': forms.TextInput(
                attrs={'placeholder': 'e.g. BS Information Technology'},
            ),
            'year_level': forms.TextInput(attrs={'placeholder': 'e.g. 3rd Year'}),
            'region': forms.TextInput(
                attrs={'placeholder': 'e.g. Region VI - Western Visayas'},
            ),
            'province': forms.TextInput(attrs={'placeholder': 'e.g. Negros Occidental'}),
            'city_municipality': forms.TextInput(
                attrs={'placeholder': 'e.g. Kabankalan City'},
            ),
            'target_exam_name': forms.TextInput(
                attrs={'placeholder': 'e.g. Midterms / Board Exam'},
            ),
            'target_exam_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        if user is not None and not self.is_bound:
            self.initial.setdefault('email', user.email)
        self.fields['daily_goal'].required = False
        self.fields['show_on_leaderboard'].required = False
        self.fields['target_exam_name'].required = False
        self.fields['target_exam_date'].required = False
        self._style_fields()

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        queryset = User.objects.filter(email__iexact=email)
        if self.user is not None:
            queryset = queryset.exclude(pk=self.user.pk)
        if queryset.exists():
            raise ValidationError('An account with this email already exists.')
        return email

    def clean_full_name(self):
        return self.cleaned_data['full_name'].strip()

    @transaction.atomic
    def save(self, commit=True):
        profile = super().save(commit=False)
        if self.user is not None:
            self.user.email = self.cleaned_data['email']
        if commit:
            profile.save()
            if self.user is not None:
                self.user.save(update_fields=['email'])
        return profile


class TupasPasswordChangeForm(BootstrapFormMixin, PasswordChangeForm):
    """Password change styled to match the rest of TUPAS forms."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['old_password'].widget.attrs.update({
            'placeholder': 'Current password',
            'autocomplete': 'current-password',
        })
        self.fields['new_password1'].widget.attrs.update({
            'placeholder': 'New password',
            'autocomplete': 'new-password',
        })
        self.fields['new_password2'].widget.attrs.update({
            'placeholder': 'Repeat the new password',
            'autocomplete': 'new-password',
        })
        self._style_fields()


class StudyDocumentEditForm(BootstrapFormMixin, forms.ModelForm):
    """Rename a handout or fix OCR / extracted text."""

    class Meta:
        model = StudyDocument
        fields = ('title', 'extracted_text')
        widgets = {
            'title': forms.TextInput(attrs={
                'placeholder': 'Handout or topic title',
            }),
            'extracted_text': forms.Textarea(attrs={
                'rows': 10,
                'placeholder': 'Readable text the Study Buddy will use.',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()

    def clean_title(self):
        title = self.cleaned_data['title'].strip()
        if not title:
            raise ValidationError('Give this handout a title.')
        return title[:200]

    def clean_extracted_text(self):
        text = self.cleaned_data['extracted_text'].strip()
        if not text:
            raise ValidationError(
                'Extracted text cannot be empty. The Study Buddy needs notes to answer from.'
            )
        if len(text) > 80_000:
            raise ValidationError('Keep extracted text under 80,000 characters.')
        return text


class DeckChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f'{obj.title} — {obj.subject.subject_name}'


class AIReviewerForm(BootstrapFormMixin, forms.Form):
    """Choose a destination deck, paste notes, and configure generation."""

    ai_provider = forms.ChoiceField(
        choices=(),
        required=False,
        label='AI engine',
    )
    item_types = forms.MultipleChoiceField(
        choices=(
            ('FLIP_CARD', '3D Flip Cards & Quiz Choices'),
            ('HIGHLIGHT_NOTE', 'Smart Highlight Notes'),
            ('ENUMERATION', 'Enumeration Drills'),
        ),
        widget=forms.CheckboxSelectMultiple,
        initial=['FLIP_CARD', 'HIGHLIGHT_NOTE', 'ENUMERATION'],
        required=True,
        label='What should TUPAS AI create?',
    )
    deck = DeckChoiceField(
        queryset=Deck.objects.none(),
        required=False,
        empty_label='— Create a new deck instead —',
        label='Add generated items to an existing deck',
    )
    new_deck_title = forms.CharField(
        max_length=200,
        required=False,
        label='New deck title',
        widget=forms.TextInput(attrs={'placeholder': 'e.g. Midterm Reviewer: Data Structures'}),
    )
    new_deck_description = forms.CharField(
        required=False,
        label='New deck description',
        widget=forms.Textarea(attrs={
            'rows': 2,
            'placeholder': 'Optional short description for the deck.',
        }),
    )
    subject_name = forms.CharField(
        max_length=150,
        required=False,
        label='Subject / Course',
        widget=forms.TextInput(attrs={
            'placeholder': 'e.g. Purposive Communication or BSIT (optional)',
            'list': 'subject-datalist',
            'autocomplete': 'off',
        }),
    )
    subject = forms.ModelChoiceField(
        queryset=Subject.objects.all(),
        required=False,
        empty_label='— Select an existing subject —',
        label='Subject (for a new deck)',
    )
    new_subject_name = forms.CharField(
        max_length=150,
        required=False,
        label='…or create a new subject',
        widget=forms.TextInput(attrs={'placeholder': 'e.g. Data Structures and Algorithms'}),
    )
    new_subject_target_course = forms.CharField(
        max_length=150,
        required=False,
        label='New subject target course',
        widget=forms.TextInput(attrs={'placeholder': 'e.g. BS Information Technology'}),
    )
    notes = forms.CharField(
        max_length=30000,
        label='Lecture notes or study text',
        widget=forms.Textarea(attrs={
            'rows': 12,
            'placeholder': (
                'Paste your lecture notes, handout, or study text here.\n'
                'The AI will turn the important facts into flip cards, highlight '
                'notes, and enumeration drills.'
            ),
        }),
    )
    items_per_type = forms.ChoiceField(
        choices=(
            ('3', '⚡ Quick Cram (3 per selected type)'),
            ('5', '📚 Standard Quiz (5 per selected type)'),
            ('8', '📖 Solid Review (8 per selected type)'),
            ('12', '🔥 Deep Chapter Review (12 per selected type)'),
            ('ALL', '💯 Cover Everything in My PPT / Handout (Exhaustive Mode)'),
        ),
        initial='5',
        label='Coverage depth',
        help_text=(
            'Pick a depth or go exhaustive. Long notes are split into chunks '
            'so fewer slides are left out.'
        ),
        widget=forms.Select(),
    )
    exhaustive = forms.BooleanField(
        required=False,
        label='Cover everything (same as choosing Exhaustive Mode)',
        help_text=(
            'Best for full slide decks and handouts. Long notes are split into '
            'chunks so fewer sections are left out.'
        ),
    )
    exam_style = forms.ChoiceField(
        choices=(
            ('EXACT', 'Exact Word-for-Word (Strict PPT / Handout Definitions)'),
            ('TAGLISH', 'English Terms + Taglish Explanations (Easy to Understand)'),
            ('SITUATIONAL', 'Situational & Board-Exam Style (Scenario / Application Questions)'),
        ),
        initial='EXACT',
        label='Professor / Exam Style',
    )
    clear_existing = forms.BooleanField(
        required=False,
        label='Clear this deck’s existing items before saving (staff only)',
    )

    def __init__(self, *args, user=None, available_providers=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

        # Students may only append AI items to decks they created themselves;
        # admins keep access to every deck in the library.
        if user is not None and getattr(user, 'is_authenticated', False) and (
            user.is_staff or user.is_superuser
        ):
            deck_queryset = Deck.objects.select_related('subject').all()
        elif user is not None and user.is_authenticated:
            deck_queryset = (
                Deck.objects.select_related('subject').filter(created_by=user)
            )
        else:
            deck_queryset = Deck.objects.none()
        self.fields['deck'].queryset = deck_queryset

        if available_providers is None:
            from .ai import get_available_providers
            available_providers = get_available_providers()
        choices = [('', 'Auto (recommended)')]
        for item in available_providers:
            choices.append((item['provider'], f"{item['provider_label']} - {item['model']}"))
        self.fields['ai_provider'].choices = choices
        if len(available_providers) == 1:
            self.fields['ai_provider'].initial = available_providers[0]['provider']
        self._style_fields()

    def clean_notes(self):
        notes = self.cleaned_data['notes'].strip()
        if len(notes) < 80:
            raise ValidationError('Paste at least 80 characters so the AI has enough material.')
        return notes

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('deck'):
            return cleaned

        if not cleaned.get('new_deck_title', '').strip():
            self.add_error('new_deck_title', 'Give the new deck a title.')

        # Unified subject resolution: accepts subject_name, new_subject_name, or subject choice.
        # If student leaves it blank, seamlessly defaults to 'General Studies' instead of failing!
        resolved_subject_name = (
            cleaned.get('subject_name')
            or cleaned.get('new_subject_name')
            or ''
        ).strip()

        if not cleaned.get('subject') and not resolved_subject_name:
            default_subj = 'General Studies'
            if self.user and getattr(self.user, 'profile', None) and self.user.profile.course_or_strand.strip():
                default_subj = self.user.profile.course_or_strand.strip()
            cleaned['new_subject_name'] = default_subj
            cleaned['subject_name'] = default_subj
        elif resolved_subject_name:
            cleaned['new_subject_name'] = resolved_subject_name
            cleaned['subject_name'] = resolved_subject_name

        return cleaned


class StudyDocumentUploadForm(BootstrapFormMixin, forms.Form):
    """Upload a PDF handout or paste study notes to talk with the TUPAS AI Study Buddy."""

    title = forms.CharField(
        max_length=200,
        required=False,
        label='Handout or Topic Title',
        widget=forms.TextInput(attrs={'placeholder': 'e.g. Purposive Communication - Midterms Handout'}),
    )
    pdf_file = forms.FileField(
        required=False,
        label='Upload Handout (PDF, PPTX, DOCX, TXT)',
        widget=forms.FileInput(attrs={'accept': '.pdf,.pptx,.docx,.txt,.md'}),
    )
    pasted_notes = forms.CharField(
        required=False,
        label='Or Paste Lecture Notes / Text',
        widget=forms.Textarea(attrs={
            'rows': 7,
            'placeholder': 'Paste notes, chapter summaries, or lecture transcript here...',
        }),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()

    def clean(self):
        cleaned = super().clean()
        pdf_file = cleaned.get('pdf_file')
        pasted_notes = (cleaned.get('pasted_notes') or '').strip()

        if not pdf_file and not pasted_notes:
            raise ValidationError('Please upload a PDF file or paste your study notes to get started.')

        if pdf_file:
            ext = pdf_file.name.lower().split('.')[-1]
            if ext not in ('pdf', 'txt', 'md', 'pptx', 'docx'):
                raise ValidationError(
                    'Please upload a PDF, PowerPoint (.pptx), Word (.docx), '
                    'or text (.txt / .md) file.'
                )
            if pdf_file.size > 20 * 1024 * 1024:
                raise ValidationError('Uploaded file is too large (maximum 20MB).')

        return cleaned


class GenerateDeckFromDocForm(BootstrapFormMixin, forms.Form):
    """Generate a Reviewer Deck directly from an uploaded StudyDocument."""

    deck_title = forms.CharField(
        max_length=200,
        label='Deck Title',
        widget=forms.TextInput(attrs={'placeholder': 'e.g. Purposive Communication Midterms'}),
    )
    subject = forms.ModelChoiceField(
        queryset=Subject.objects.none(),
        required=False,
        empty_label='— Create a new subject instead —',
        label='Existing Subject',
    )
    new_subject_name = forms.CharField(
        max_length=150,
        required=False,
        label='New Subject Name (if not chosen above)',
        widget=forms.TextInput(attrs={'placeholder': 'e.g. General Education'}),
    )
    items_count = forms.IntegerField(
        min_value=1,
        max_value=30,
        initial=8,
        label='How many items per type?',
        help_text=(
            'Type any number from 1 to 30. TUPAS reports any shortfall after '
            'generation so you know what was not covered.'
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['subject'].queryset = Subject.objects.order_by('subject_name')
        self._style_fields()

    def clean(self):
        cleaned = super().clean()
        subject = cleaned.get('subject')
        new_name = (cleaned.get('new_subject_name') or '').strip()
        if not subject and not new_name:
            cleaned['new_subject_name'] = 'General Studies'
        return cleaned


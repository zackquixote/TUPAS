"""Study Buddy: PDF / note tutoring and one-click deck generation.

Every document and chat message is strictly scoped to ``request.user``.
Staff accounts do not get implicit access to another student's handout.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from ..ai import (
    AIError,
    chat_with_tupas_tutor,
    generate_deck_payload_from_text,
    get_ai_status,
    save_generated_items,
)
from ..forms import (
    GenerateDeckFromDocForm,
    StudyDocumentEditForm,
    StudyDocumentUploadForm,
)
from ..models import ActivityLog, Deck, StudyChatMessage, StudyDocument, Subject
from ..services.access import can_edit_study_document
from ..services.activity import record_activity
from ..services.coverage import coverage_shortfalls, coverage_warning
from ..services.documents import extract_uploaded_text
from ..services.quotas import ai_generation_quota, ai_quota_message, chat_message_quota, chat_quota_message
from ..services.rbac import student_only

MAX_CHAT_LENGTH = 2000


def _auto_analyze(document):
    """Analyze an uploaded handout so students see a summary without pasting."""
    try:
        reply = chat_with_tupas_tutor(
            document.extracted_text,
            "Summarize the key points of this handout in 2-3 short bullet points "
            "and note the main concepts students should focus on.",
        )
    except Exception:
        return
    if not reply:
        return
    StudyChatMessage.objects.create(
        document=document,
        sender='TUPAS',
        message=reply[:MAX_CHAT_LENGTH],
    )


def _wants_json(request):
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'


def _json_error(error, status=400):
    return JsonResponse({'ok': False, 'error': error}, status=status)


@student_only
@never_cache
def study_buddy(request):
    """List the student's handouts and accept a new PDF or pasted notes."""
    query = (request.GET.get('q') or '').strip()
    documents = StudyDocument.objects.filter(user=request.user)
    if query:
        documents = documents.filter(
            Q(title__icontains=query) | Q(extracted_text__icontains=query)
        )
    documents = documents[:12]
    form = StudyDocumentUploadForm(request.POST or None, request.FILES or None)

    if request.method == 'POST' and form.is_valid():
        pasted_notes = (form.cleaned_data.get('pasted_notes') or '').strip()
        upload = form.cleaned_data.get('pdf_file')
        title = (form.cleaned_data.get('title') or '').strip()
        text = pasted_notes
        page_count = 1

        if upload is not None:
            file_name = upload.name.rsplit('/', 1)[-1]
            title = title or file_name.rsplit('.', 1)[0]
            try:
                text, page_count = extract_uploaded_text(upload)
            except AIError as exc:
                form.add_error('pdf_file', str(exc))
            finally:
                # The FileField reads from the start of the stream again.
                upload.seek(0)

        if form.errors:
            pass
        elif not text.strip():
            form.add_error(
                None,
                'We could not find readable text in that file. '
                'Try a text-based PDF or paste the notes instead.',
            )
        else:
            document = StudyDocument.objects.create(
                user=request.user,
                title=(title or 'Untitled handout')[:200],
                pdf_file=upload,
                extracted_text=text.strip(),
                page_count=max(1, int(page_count or 1)),
            )
            record_activity(
                request.user,
                ActivityLog.Action.DOCUMENT_UPLOAD,
                document.title,
                request,
            )
            # Automatically analyze the document: send the Study Buddy a
            # summary request so the student doesn't have to paste anything.
            _auto_analyze(document)
            messages.success(
                request,
                f'â€œ{document.title}â€ is ready. I already analyzed it for you; '
                'check the chat to see the summary, or ask anything.',
            )
            return redirect('tupas:study_buddy_detail', doc_id=document.pk)

    return render(request, 'tupas/study_buddy.html', {
        'view_mode': 'list',
        'documents': documents,
        'form': form,
        'ai_status': get_ai_status(),
    })


@student_only
@never_cache
def study_buddy_detail(request, doc_id):
    document = get_object_or_404(StudyDocument, pk=doc_id, user=request.user)
    return render(request, 'tupas/study_buddy.html', {
        'view_mode': 'detail',
        'document': document,
        'chat_messages': document.messages.all(),
        'generate_form': GenerateDeckFromDocForm(),
        'edit_doc_form': StudyDocumentEditForm(instance=document),
        'ai_status': get_ai_status(),
    })


def _check_chat_quota(request, document):
    """Return a redirect/JSON response when the chat allowance is used up."""
    allowed, _remaining = chat_message_quota(request.user)
    if allowed:
        return None
    message = chat_quota_message()
    if _wants_json(request):
        return _json_error(message, status=429)
    messages.error(request, message)
    return redirect('tupas:study_buddy_detail', doc_id=document.pk)


@student_only
@require_POST
def study_buddy_chat(request, doc_id):
    document = get_object_or_404(StudyDocument, pk=doc_id, user=request.user)
    message_text = (request.POST.get('message') or '').strip()
    detail_url = reverse('tupas:study_buddy_detail', args=[document.pk])

    if not message_text:
        if _wants_json(request):
            return _json_error('Type a question for the Study Buddy first.')
        messages.warning(request, 'Type a question for the Study Buddy first.')
        return redirect(detail_url)
    if len(message_text) > MAX_CHAT_LENGTH:
        if _wants_json(request):
            return _json_error('Keep each question under 2,000 characters.')
        messages.warning(request, 'Keep each question under 2,000 characters.')
        return redirect(detail_url)

    limited = _check_chat_quota(request, document)
    if limited is not None:
        return limited

    # History is captured before the new question is stored, because the tutor
    # receives the current question separately.
    history = list(document.messages.all())
    StudyChatMessage.objects.create(
        document=document,
        sender=StudyChatMessage.Sender.STUDENT,
        message=message_text,
    )
    reply = chat_with_tupas_tutor(document.extracted_text, message_text, history)
    StudyChatMessage.objects.create(
        document=document,
        sender=StudyChatMessage.Sender.TUPAS,
        message=str(reply or '')[:8000],
    )
    if _wants_json(request):
        return JsonResponse({'ok': True, 'redirect': f'{detail_url}#chat'})
    return redirect(f'{detail_url}#chat')


@student_only
@require_POST
def study_buddy_edit_message(request, doc_id, msg_id):
    """Edit a student prompt, drop stale replies, and regenerate TUPAS's answer."""
    document = get_object_or_404(StudyDocument, pk=doc_id, user=request.user)
    if not can_edit_study_document(request.user, document):
        return _json_error('You can only manage your own handouts.', status=403)

    message = get_object_or_404(
        StudyChatMessage,
        pk=msg_id,
        document=document,
    )
    if message.sender != StudyChatMessage.Sender.STUDENT:
        return _json_error('Only your own questions can be edited and re-asked.', status=400)

    new_text = (request.POST.get('message') or '').strip()
    if not new_text:
        error = 'The edited question cannot be empty.'
        if _wants_json(request):
            return _json_error(error)
        messages.warning(request, error)
        return redirect('tupas:study_buddy_detail', doc_id=document.pk)
    if len(new_text) > MAX_CHAT_LENGTH:
        error = 'Keep each question under 2,000 characters.'
        if _wants_json(request):
            return _json_error(error)
        messages.warning(request, error)
        return redirect('tupas:study_buddy_detail', doc_id=document.pk)

    limited = _check_chat_quota(request, document)
    if limited is not None:
        return limited

    detail_url = reverse('tupas:study_buddy_detail', args=[document.pk])
    with transaction.atomic():
        message.message = new_text
        message.save(update_fields=['message'])
        # Remove every later turn so the regenerated reply is not appended
        # after stale follow-ups.
        StudyChatMessage.objects.filter(
            document=document,
            id__gt=message.id,
        ).delete()
        history = list(
            StudyChatMessage.objects.filter(document=document, id__lt=message.id)
        )
        reply = chat_with_tupas_tutor(document.extracted_text, new_text, history)
        StudyChatMessage.objects.create(
            document=document,
            sender=StudyChatMessage.Sender.TUPAS,
            message=str(reply or '')[:8000],
        )

    if _wants_json(request):
        return JsonResponse({
            'ok': True,
            'redirect': f'{detail_url}#chat',
            'message': new_text,
        })
    messages.success(request, 'Question updated. TUPAS has a fresh answer.')
    return redirect(f'{detail_url}#chat')


@student_only
@require_POST
def study_buddy_delete_message(request, doc_id, msg_id):
    """Delete one chat message owned by this student's document."""
    document = get_object_or_404(StudyDocument, pk=doc_id, user=request.user)
    message = get_object_or_404(StudyChatMessage, pk=msg_id, document=document)

    with transaction.atomic():
        # If a student prompt is deleted, also drop the immediately following
        # TUPAS reply so the transcript does not show an orphaned answer.
        following = (
            StudyChatMessage.objects
            .filter(document=document, id__gt=message.id)
            .order_by('id')
            .first()
        )
        follows_reply = (
            message.sender == StudyChatMessage.Sender.STUDENT
            and following is not None
            and following.sender == StudyChatMessage.Sender.TUPAS
        )
        message.delete()
        if follows_reply:
            following.delete()

    if _wants_json(request):
        return JsonResponse({'ok': True})
    messages.success(request, 'Deleted that chat message.')
    return redirect('tupas:study_buddy_detail', doc_id=document.pk)


@student_only
@require_POST
def study_buddy_clear_chat(request, doc_id):
    """Reset the conversation and restore a fresh TUPAS welcome message."""
    document = get_object_or_404(StudyDocument, pk=doc_id, user=request.user)
    with transaction.atomic():
        document.messages.all().delete()
        StudyChatMessage.objects.create(
            document=document,
            sender=StudyChatMessage.Sender.TUPAS,
            message=(
                f'Fresh start! I cleared our chat about â€œ{document.title}â€. '
                'Ask me anything about this handout â€” summarize it, explain a part '
                'in Taglish, list the enumerations, or quiz me.'
            ),
        )
    if _wants_json(request):
        return JsonResponse({'ok': True})
    messages.success(request, 'Chat cleared. A fresh TUPAS greeting is waiting.')
    return redirect('tupas:study_buddy_detail', doc_id=document.pk)


@student_only
@require_POST
def study_buddy_edit_doc(request, doc_id):
    """Rename a handout or fix its extracted text (OCR / PDF typos)."""
    document = get_object_or_404(StudyDocument, pk=doc_id, user=request.user)
    if not can_edit_study_document(request.user, document):
        return _json_error('You can only manage your own handouts.', status=403)

    form = StudyDocumentEditForm(request.POST, instance=document)
    if not form.is_valid():
        first_error = ' '.join(
            error for errors in form.errors.values() for error in errors
        ) or 'Could not save those handout changes.'
        if _wants_json(request):
            return _json_error(first_error)
        messages.error(request, first_error)
        return redirect('tupas:study_buddy_detail', doc_id=document.pk)

    form.save()
    if _wants_json(request):
        return JsonResponse({'ok': True, 'title': document.title})
    messages.success(request, 'Handout details updated.')
    return redirect('tupas:study_buddy_detail', doc_id=document.pk)


@student_only
@require_POST
def study_buddy_delete_doc(request, doc_id):
    """Delete this student's handout (and its chat) and return to the library."""
    document = get_object_or_404(StudyDocument, pk=doc_id, user=request.user)
    if not can_edit_study_document(request.user, document):
        return _json_error('You can only manage your own handouts.', status=403)

    title = document.title
    record_activity(
        request.user,
        ActivityLog.Action.DOCUMENT_DELETE,
        title,
        request,
    )
    if document.pdf_file:
        document.pdf_file.delete(save=False)
    document.delete()
    messages.success(request, f'Deleted â€œ{title}â€ and its Study Buddy chat.')
    return redirect('tupas:study_buddy')


@student_only
@require_POST
def study_buddy_generate_deck(request, doc_id):
    document = get_object_or_404(StudyDocument, pk=doc_id, user=request.user)
    detail_url = reverse('tupas:study_buddy_detail', args=[document.pk])
    form = GenerateDeckFromDocForm(request.POST)

    if not form.is_valid():
        messages.error(request, 'Add a deck title before generating a reviewer from this handout.')
        return redirect(detail_url)

    if not ai_generation_quota(request.user)[0]:
        messages.error(request, ai_quota_message())
        return redirect(detail_url)

    try:
        payload = generate_deck_payload_from_text(
            document.extracted_text,
            limit=int(form.cleaned_data['items_count']),
        )
    except AIError as exc:
        messages.error(request, str(exc))
        return redirect(detail_url)

    total_items = sum(len(items) for items in payload.values())
    if not total_items:
        messages.error(
            request,
            'TUPAS could not extract usable review items from this handout. '
            'Try a cleaner PDF or paste the notes instead.',
        )
        return redirect(detail_url)

    with transaction.atomic():
        subject = form.cleaned_data.get('subject')
        if subject is None:
            subject_name = (
                form.cleaned_data.get('new_subject_name') or 'General Studies'
            ).strip()
            subject = Subject.objects.create(subject_name=subject_name or 'General Studies')

        deck = Deck.objects.create(
            subject=subject,
            title=form.cleaned_data['deck_title'].strip(),
            description=f'Generated from â€œ{document.title}â€.',
            created_by=request.user,
        )
        record_activity(
            request.user,
            ActivityLog.Action.DECK_CREATE,
            deck.title,
            request,
        )
        counts = save_generated_items(deck, payload)
        document.generated_deck = deck
        document.save(update_fields=['generated_deck'])

    shortfalls = coverage_shortfalls(counts, form.cleaned_data['items_count'])
    if shortfalls:
        messages.warning(request, coverage_warning(shortfalls))

    messages.success(
        request,
        f'Created â€œ{deck.title}â€ with {counts["flip_cards"]} flip cards, '
        f'{counts["highlight_notes"]} highlight notes, and '
        f'{counts["enumerations"]} enumeration drills.',
    )
    return redirect('tupas:deck_detail', deck_id=deck.pk)

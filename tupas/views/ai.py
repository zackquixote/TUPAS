"""AI reviewer generation page and slide-photo OCR endpoint."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST
import logging

logger = logging.getLogger(__name__)

from ..ai import (
    AIError,
    extract_text_from_image,
    generate_reviewer_json,
    get_ai_status,
    get_available_providers,
    save_generated_items,
)
from ..forms import AIReviewerForm
from ..models import ActivityLog, Deck, Subject
from ..services.activity import record_activity
from ..services.coverage import coverage_shortfalls, coverage_warning
from ..services.quotas import ai_generation_quota, ai_quota_message
from ..services.rbac import student_only

IMAGE_MIME_TYPES = {
    'jpg': 'image/jpeg',
    'jpeg': 'image/jpeg',
    'png': 'image/png',
    'webp': 'image/webp',
}

MAX_IMAGE_BYTES = 8 * 1024 * 1024


@student_only
def ai_generator(request):
    """Generate reviewer items from raw notes with the configured AI provider."""
    available_providers = get_available_providers()
    ai_status = get_ai_status()

    if request.method == 'POST':
        form = AIReviewerForm(
            request.POST,
            user=request.user,
            available_providers=available_providers,
        )
        if form.is_valid():
            depth = str(form.cleaned_data.get('items_per_type') or '5').upper()
            exhaustive = bool(form.cleaned_data.get('exhaustive')) or depth == 'ALL'
            requested_items = 'ALL' if exhaustive else int(depth)
            if not ai_status['configured'] and not available_providers:
                messages.error(request, ai_status['error'] or 'AI is not configured.')
            elif not ai_generation_quota(request.user)[0]:
                messages.error(request, ai_quota_message())
            else:
                try:
                    payload = generate_reviewer_json(
                        form.cleaned_data['notes'],
                        coverage_mode=('ALL' if exhaustive else requested_items),
                        provider=form.cleaned_data.get('ai_provider') or None,
                        selected_types=form.cleaned_data.get('item_types') or [],
                        exam_style=form.cleaned_data.get('exam_style', 'EXACT'),
                    )

                    selected_deck = form.cleaned_data.get('deck')
                    with transaction.atomic():
                        if selected_deck:
                            deck = selected_deck
                            if form.cleaned_data.get('clear_existing'):
                                if request.user.is_staff:
                                    deck.items.all().delete()
                                else:
                                    messages.warning(
                                        request,
                                        'Only staff accounts can clear an existing deck. '
                                        'The generated items were appended instead.',
                                    )
                        else:
                            subject = form.cleaned_data.get('subject')
                            new_subject_name = (
                                form.cleaned_data.get('new_subject_name')
                                or form.cleaned_data.get('subject_name')
                                or ''
                            ).strip()
                            if new_subject_name and subject is None:
                                subject, _ = Subject.objects.get_or_create(
                                    subject_name=new_subject_name,
                                    defaults={
                                        'target_course': form.cleaned_data.get(
                                            'new_subject_target_course', ''
                                        ).strip(),
                                    },
                                )
                            if subject is None:
                                subject, _ = Subject.objects.get_or_create(subject_name='General Studies')

                            deck = Deck.objects.create(
                                subject=subject,
                                title=form.cleaned_data['new_deck_title'].strip(),
                                description=form.cleaned_data.get('new_deck_description', '').strip(),
                                created_by=request.user,
                            )
                            record_activity(
                                request.user,
                                ActivityLog.Action.DECK_CREATE,
                                deck.title,
                                request,
                            )

                        counts = save_generated_items(deck, payload)
                except AIError as exc:
                    messages.error(request, str(exc))
                else:
                    total = (
                        counts['flip_cards']
                        + counts['highlight_notes']
                        + counts['enumerations']
                    )
                    messages.success(
                        request,
                        f'AI generated {total} review items in “{deck.title}”: '
                        f"{counts['flip_cards']} flip cards, "
                        f"{counts['highlight_notes']} highlight notes, "
                        f"{counts['enumerations']} enumeration drills, and "
                        f"{counts['quiz_choices']} quiz choices.",
                    )
                    shortfalls = coverage_shortfalls(counts, requested_items) if requested_items != 'ALL' else []
                    if shortfalls:
                        messages.warning(request, coverage_warning(shortfalls))
                    from ..services.gamification import grant_badge
                    grant_badge(request.user.profile, 'ai_creator') if getattr(request.user, 'profile', None) else None
                    return redirect('tupas:deck_detail', deck_id=deck.id)
        else:
            logger.warning(
                'AI generator form invalid for %s: %s',
                request.user.username,
                form.errors.get_json_data(),
            )
    else:
        initial = {}
        deck_param = request.GET.get('deck', '')
        if deck_param.isdigit() and Deck.objects.filter(pk=deck_param).exists():
            initial['deck'] = deck_param
        form = AIReviewerForm(
            initial=initial,
            user=request.user,
            available_providers=available_providers,
        )

    existing_subjects = list(
        Subject.objects.values_list('subject_name', flat=True).order_by('subject_name')[:60]
    )

    return render(request, 'tupas/ai_generator.html', {
        'form': form,
        'ai_status': ai_status,
        'available_providers': available_providers,
        'existing_subjects': existing_subjects,
    })


def health(_request):
    # NOTE: do not set Connection: close here. wsgiref rejects hop-by-hop
    # headers in start_response (AssertionError). The 28-byte 200 (instead of
    # a 6 KB DEBUG 404) is what actually stops the prober's broken pipes.
    return JsonResponse({'ok': True, 'app': 'TUPAS'})


@student_only
@require_POST
def extract_image(request):
    """Transcribe a slide photo or whiteboard image into study notes."""
    upload = request.FILES.get('image')
    if upload is None:
        return JsonResponse(
            {'ok': False, 'error': 'Choose a slide photo or whiteboard image first.'},
            status=400,
        )
    if upload.size > MAX_IMAGE_BYTES:
        return JsonResponse(
            {'ok': False, 'error': 'That image is too large. Please use one under 8MB.'},
            status=400,
        )

    extension = upload.name.rsplit('.', 1)[-1].lower() if '.' in upload.name else ''
    mime_type = IMAGE_MIME_TYPES.get(extension)
    if mime_type is None:
        return JsonResponse(
            {'ok': False, 'error': 'Use a JPG, PNG, or WEBP image.'},
            status=400,
        )

    result = extract_text_from_image(upload.read(), mime_type)
    if not result.get('ok'):
        return JsonResponse(
            {
                'ok': False,
                'error': result.get('error') or 'Photo OCR is unavailable right now.',
            },
            status=400,
        )
    return JsonResponse({'ok': True, 'text': str(result.get('text') or '')[:30000]})

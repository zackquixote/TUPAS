"""Deck overview, cram sheet, clone, merge, pin/archive, and import/export."""

import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from ..models import ActivityLog, Deck, EnumerationItem, ExamAttempt, ItemProgress, QuizChoice, ReviewItem, Subject
from ..services.access import can_edit_deck
from ..services.activity import record_activity
from ..services.rbac import student_only
from ..services.cram import build_cram_sheet_context
from ..services.decks import copy_deck, merge_decks
from ..services.text import split_answers


def _last_quiz_ago(user, deck):
    """Return the relative time of the student's last quiz/exam for this deck."""
    from django.utils import timezone as tz

    last = ExamAttempt.objects.filter(user=user, deck=deck).order_by('-date_taken').first()
    if not last or not last.date_taken:
        return None
    delta = tz.now() - last.date_taken
    if delta.days > 0:
        return f'{delta.days} day(s) ago'
    hours = delta.seconds // 3600
    if hours > 0:
        return f'{hours} hour(s) ago'
    minutes = delta.seconds // 60
    if minutes > 0:
        return f'{minutes} minute(s) ago'
    return 'just now'


def _editable_items(deck_items):
    """Serialise deck items for the inline edit modal."""
    return [
        {
            'id': item.id,
            'item_type': item.item_type,
            'prompt_text': item.prompt_text,
            'main_answer': item.main_answer or '',
            'explanation': item.explanation or '',
            'answers': '\n'.join(
                answer.answer_text for answer in item.enum_answers.all()
            ),
            'is_strict_order': item.is_strict_order,
            'quality_flag': item.quality_flag,
        }
        for item in deck_items
    ]


def _dashboard_decks(request):
    """Shared deck queryset for dashboard (search, tag, archived view)."""
    view = (request.GET.get('view') or 'active').strip().lower()
    query = (request.GET.get('q') or '').strip()
    tag = (request.GET.get('tag') or '').strip()

    decks = (
        Deck.objects
        .select_related('subject', 'created_by')
        .annotate(item_count=Count('items'))
    )
    if view == 'archived':
        decks = decks.filter(is_archived=True)
    else:
        view = 'active'
        decks = decks.filter(is_archived=False)

    if query:
        decks = decks.filter(
            Q(title__icontains=query)
            | Q(description__icontains=query)
            | Q(subject__subject_name__icontains=query)
            | Q(tags__icontains=query)
        )
    if tag:
        decks = decks.filter(tags__icontains=tag)

    return decks, {'view': view, 'q': query, 'tag': tag}


@login_required
def deck_detail(request, deck_id):
    deck = get_object_or_404(
        Deck.objects.select_related('subject', 'created_by'),
        pk=deck_id,
    )
    items = deck.items.all()

    type_counts = {
        row['item_type']: row['count']
        for row in items.values('item_type').annotate(count=Count('id'))
    }

    has_main_answer = ~Q(main_answer__isnull=True) & ~Q(main_answer='')
    exam_counts = {
        ExamAttempt.ExamMode.MULTIPLE_CHOICE: items.filter(choices__isnull=False).distinct().count(),
        ExamAttempt.ExamMode.IDENTIFICATION: items.exclude(main_answer__isnull=True).exclude(main_answer='').count(),
        ExamAttempt.ExamMode.ENUMERATION: items.filter(enum_answers__isnull=False).distinct().count(),
        ExamAttempt.ExamMode.MIXED: items.filter(
            Q(choices__isnull=False) | Q(enum_answers__isnull=False) | has_main_answer
        ).distinct().count(),
        ExamAttempt.ExamMode.TRUE_FALSE: items.exclude(main_answer__isnull=True).exclude(main_answer='').count(),
        ExamAttempt.ExamMode.MATCHING: items.exclude(main_answer__isnull=True).exclude(main_answer='').count(),
    }

    deck_progress = ItemProgress.objects.filter(user=request.user, review_item__deck=deck)
    progress_counts = {
        'mastered': deck_progress.filter(mastery_status=ItemProgress.MasteryStatus.MASTERED).count(),
        'needs_review': deck_progress.filter(mastery_status=ItemProgress.MasteryStatus.NEEDS_REVIEW).count(),
        'total_marked': deck_progress.count(),
    }

    deck_items = list(items.prefetch_related('choices', 'enum_answers'))
    can_edit = can_edit_deck(request.user, deck)

    return render(request, 'tupas/deck_detail.html', {
        'deck': deck,
        'total_items': len(deck_items),
        'type_counts': type_counts,
        'exam_counts': exam_counts,
        'progress_counts': progress_counts,
        'recent_attempts': ExamAttempt.objects.filter(user=request.user, deck=deck)[:8],
        'last_quiz_ago': _last_quiz_ago(request.user, deck),
        'deck_items': deck_items,
        'can_edit_deck': can_edit,
        'edit_items': _editable_items(deck_items) if can_edit else [],
        'deck_tags': [tag.strip() for tag in (deck.tags or '').split(',') if tag.strip()],
    })


@student_only
def deck_cram_sheet(request, deck_id):
    """One-page printable cram sheet built from the deck's items."""
    deck = get_object_or_404(Deck.objects.select_related('subject'), pk=deck_id)
    filter_key = 'weak' if request.GET.get('filter') == 'weak' else 'all'
    return render(
        request,
        'tupas/cram_sheet.html',
        build_cram_sheet_context(deck, request.user, filter_key),
    )


@student_only
@require_POST
def clone_deck(request, deck_id):
    """Copy a classmate's deck (items, choices and enumerations) into a new owned deck."""
    source = get_object_or_404(Deck.objects.select_related('subject'), pk=deck_id)
    clone = copy_deck(source, request.user)

    messages.success(
        request,
        f'Cloned “{source.title}” into your decks. Customise it freely.',
    )
    return redirect('tupas:deck_detail', deck_id=clone.pk)


@login_required
@require_POST
def merge_deck(request, deck_id):
    """Merge items from another deck into this one (owner/staff)."""
    target = get_object_or_404(Deck, pk=deck_id)
    if not can_edit_deck(request.user, target):
        raise PermissionDenied('You can only merge into decks you created.')

    source_id = request.POST.get('source_deck') or ''
    if not str(source_id).isdigit():
        messages.error(request, 'Choose a deck to merge from.')
        return redirect('tupas:deck_detail', deck_id=target.id)

    source = get_object_or_404(Deck, pk=int(source_id))
    if source.id == target.id:
        messages.warning(request, 'You cannot merge a deck into itself.')
        return redirect('tupas:deck_detail', deck_id=target.id)

    copied = merge_decks(target, source)
    if copied:
        plural = '' if copied == 1 else 's'
        messages.success(request, f'Merged {copied} new item{plural} from “{source.title}”.')
    else:
        messages.info(request, 'No new items to merge — everything already exists in this deck.')
    return redirect('tupas:deck_detail', deck_id=target.id)


@login_required
@require_POST
def toggle_pin_deck(request, deck_id):
    deck = get_object_or_404(Deck, pk=deck_id)
    if not can_edit_deck(request.user, deck):
        raise PermissionDenied('You can only pin decks you created.')
    deck.is_pinned = not deck.is_pinned
    deck.save(update_fields=['is_pinned'])
    messages.success(
        request,
        f'“{deck.title}” is now {("pinned" if deck.is_pinned else "unpinned")}.',
    )
    return redirect('tupas:dashboard')


@login_required
@require_POST
def toggle_archive_deck(request, deck_id):
    deck = get_object_or_404(Deck, pk=deck_id)
    if not can_edit_deck(request.user, deck):
        raise PermissionDenied('You can only archive decks you created.')
    deck.is_archived = not deck.is_archived
    deck.save(update_fields=['is_archived'])
    messages.success(
        request,
        f'“{deck.title}” is now {("archived" if deck.is_archived else "restored")}.',
    )
    return redirect('tupas:dashboard')


@login_required
def export_deck(request, deck_id):
    """Export a deck as TUPAS JSON or Quizlet-style TSV."""
    deck = get_object_or_404(
        Deck.objects.select_related('subject'),
        pk=deck_id,
    )
    fmt = (request.GET.get('format') or 'json').strip().lower()
    items = list(deck.items.prefetch_related('choices', 'enum_answers'))
    record_activity(
        request.user,
        ActivityLog.Action.EXPORT,
        f'{deck.title} as {fmt.upper()}',
        request,
    )

    if fmt == 'tsv':
        lines = ['Term\tDefinition']
        for item in items:
            term = item.main_answer or item.prompt_text
            definition = item.prompt_text
            if item.item_type == ReviewItem.ItemType.FLIP_CARD and item.main_answer:
                term = item.prompt_text
                definition = item.main_answer
            elif item.item_type == ReviewItem.ItemType.ENUMERATION:
                definition = '\n'.join(
                    answer.answer_text for answer in item.enum_answers.all()
                )
            term = term.replace('\t', ' ').replace('\n', ' ')
            definition = definition.replace('\t', ' ').replace('\n', ' | ')
            lines.append(f'{term}\t{definition}')
        response = HttpResponse('\n'.join(lines), content_type='text/tab-separated-values')
        response['Content-Disposition'] = f'attachment; filename="{deck.title}.tsv"'
        return response

    payload = {
        'title': deck.title,
        'description': deck.description,
        'subject': deck.subject.subject_name,
        'tags': deck.tags,
        'items': [
            {
                'item_type': item.item_type,
                'prompt_text': item.prompt_text,
                'main_answer': item.main_answer or '',
                'explanation': item.explanation,
                'is_strict_order': item.is_strict_order,
                'quality_flag': item.quality_flag,
                'distractors': [
                    choice.choice_text
                    for choice in item.choices.all()
                    if not choice.is_correct
                ],
                'answers': [
                    answer.answer_text for answer in item.enum_answers.all()
                ],
            }
            for item in items
        ],
    }
    response = HttpResponse(
        json.dumps(payload, indent=2, ensure_ascii=False),
        content_type='application/json',
    )
    response['Content-Disposition'] = f'attachment; filename="{deck.title}.json"'
    return response


@student_only
def export_attempts(request, deck_id):
    """Export all quiz/exam attempts for a deck as CSV."""
    deck = get_object_or_404(Deck, pk=deck_id)
    attempts = ExamAttempt.objects.filter(deck=deck, user=request.user).order_by('-date_taken')
    record_activity(
        request.user,
        ActivityLog.Action.EXPORT,
        f'{deck.title} quiz results as CSV',
        request,
    )
    import csv
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="{deck.title}-quiz-results.csv"'
    writer = csv.writer(response)
    writer.writerow(['date_taken', 'exam_mode', 'score', 'total_items', 'percentage'])
    for attempt in attempts:
        writer.writerow([
            attempt.date_taken.strftime('%Y-%m-%d %H:%M'),
            attempt.get_exam_mode_display(),
            attempt.score,
            attempt.total_items,
            str(attempt.percentage),
        ])
    return response


@login_required
def import_deck(request):
    """Import a TUPAS JSON deck or Quizlet TSV (Term<TAB>Definition)."""
    if request.method == 'POST':
        title = (request.POST.get('title') or '').strip()[:200]
        pasted = (request.POST.get('payload') or '').strip()
        upload = request.FILES.get('file')
        raw = ''
        if upload:
            raw = upload.read().decode('utf-8-sig', errors='replace').strip()
        elif pasted:
            raw = pasted

        if not title:
            title = 'Imported deck'
        if not raw:
            messages.error(request, 'Paste JSON/TSV or choose a file first.')
            return redirect('tupas:import_deck')

        subject_name = (request.POST.get('subject_name') or 'Imported').strip() or 'Imported'
        subject, _ = Subject.objects.get_or_create(subject_name=subject_name)

        deck = Deck.objects.create(
            subject=subject,
            title=title,
            description=f'Imported by {request.user.username}.',
            created_by=request.user,
        )
        imported = 0

        # Try TUPAS JSON first.
        parsed = None
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            parsed = None

        if isinstance(parsed, dict) and isinstance(parsed.get('items'), list):
            if not title or title == 'Imported deck':
                deck.title = str(parsed.get('title') or title)[:200]
            if parsed.get('tags'):
                deck.tags = str(parsed['tags'])[:200]
            if parsed.get('description') and deck.description.startswith('Imported'):
                deck.description = str(parsed['description'])[:500]
            deck.save()

            for row in parsed['items'][:200]:
                if not isinstance(row, dict):
                    continue
                item_type = str(row.get('item_type') or 'FLIP_CARD')
                if item_type not in ReviewItem.ItemType.values:
                    item_type = ReviewItem.ItemType.FLIP_CARD
                prompt = str(row.get('prompt_text') or '').strip()
                if not prompt:
                    continue
                item = ReviewItem.objects.create(
                    deck=deck,
                    item_type=item_type,
                    prompt_text=prompt[:5000],
                    main_answer=str(row.get('main_answer') or '')[:255] or None,
                    explanation=str(row.get('explanation') or '')[:5000],
                    is_strict_order=bool(row.get('is_strict_order')),
                    quality_flag=str(row.get('quality_flag') or '')[:20],
                )
                answer = item.main_answer or ''
                if item_type == ReviewItem.ItemType.FLIP_CARD and answer:
                    QuizChoice.objects.create(
                        review_item=item, choice_text=answer, is_correct=True,
                    )
                    for distractor in (row.get('distractors') or [])[:3]:
                        text = str(distractor).strip()[:255]
                        if text and text != answer:
                            QuizChoice.objects.create(
                                review_item=item, choice_text=text, is_correct=False,
                            )
                if item_type == ReviewItem.ItemType.ENUMERATION:
                    answers = row.get('answers') or split_answers(str(row.get('main_answer') or ''))
                    EnumerationItem.objects.bulk_create([
                        EnumerationItem(
                            review_item=item,
                            answer_text=str(text)[:255],
                            order_number=index,
                        )
                        for index, text in enumerate(answers[:20], start=1)
                        if str(text).strip()
                    ])
                imported += 1

        elif parsed is None or isinstance(parsed, list):
            # Quizlet TSV: Term<TAB>Definition, first line may be a header.
            lines = raw.splitlines()
            if lines and lines[0].lower().startswith('term'):
                lines = lines[1:]
            for line in lines[:200]:
                if '\t' not in line:
                    continue
                term, _, definition = line.partition('\t')
                term = term.strip()
                definition = definition.strip()
                if not term:
                    continue
                item = ReviewItem.objects.create(
                    deck=deck,
                    item_type=ReviewItem.ItemType.FLIP_CARD,
                    prompt_text=term[:5000],
                    main_answer=definition[:255] or None,
                )
                if item.main_answer:
                    QuizChoice.objects.create(
                        review_item=item, choice_text=item.main_answer, is_correct=True,
                    )
                    # Auto-generate 3 cheap distractors from other definitions.
                    others = [
                        other.partition('\t')[2].strip()
                        for other in lines
                        if other != line and '\t' in other
                    ]
                    seen = {item.main_answer}
                    for other in others:
                        if other and other not in seen:
                            seen.add(other)
                            QuizChoice.objects.create(
                                review_item=item, choice_text=other[:255], is_correct=False,
                            )
                        if item.choices.count() >= 4:
                            break
                imported += 1

        if imported:
            messages.success(request, f'Imported {imported} item{imported and "s" or ""} into “{deck.title}”.')
            record_activity(
                request.user,
                ActivityLog.Action.DECK_IMPORT,
                f'{deck.title} ({imported} items)',
                request,
            )
            return redirect('tupas:deck_detail', deck_id=deck.id)

        deck.delete()
        messages.error(request, 'Could not read any items from that JSON or TSV.')
        return redirect('tupas:import_deck')

    recent = Deck.objects.filter(created_by=request.user).order_by('-created_at')[:5]
    return render(request, 'tupas/deck_import.html', {'recent_decks': recent})

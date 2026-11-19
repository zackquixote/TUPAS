"""Exam simulator: seeded papers, retake-wrong mode, and weak-spot exams."""

import json
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache

from ..models import ActivityLog, Deck, ExamAttempt, ItemProgress
from ..services.activity import record_activity
from ..services.exams import (
    build_exam_questions,
    clean_exam_seed,
    exam_session_key,
    new_exam_seed,
    parse_retry_ids,
)
from ..services.rbac import student_only
from ..services.gamification import award_xp, exam_xp
from ..services.grading import check_answer_match, grade_enumeration
from ..services.spaced_repetition import schedule_next_review
from ..services.streaks import record_study_day
from ..services.text import normalize

EXAM_MODES = {
    'multiple-choice': ExamAttempt.ExamMode.MULTIPLE_CHOICE,
    'identification': ExamAttempt.ExamMode.IDENTIFICATION,
    'enumeration': ExamAttempt.ExamMode.ENUMERATION,
    'mixed': ExamAttempt.ExamMode.MIXED,
    'true-false': ExamAttempt.ExamMode.TRUE_FALSE,
    'matching': ExamAttempt.ExamMode.MATCHING,
}

ALLOWED_DURATIONS = {0, 5, 10, 15, 30}


def _duration_minutes(request):
    raw = request.GET.get('duration') or request.POST.get('duration') or '0'
    try:
        minutes = int(raw)
    except (TypeError, ValueError):
        return 0
    return minutes if minutes in ALLOWED_DURATIONS else 0


@student_only
@never_cache
def exam_mode(request, deck_id, mode):
    exam_mode_value = EXAM_MODES.get(mode)
    if exam_mode_value is None:
        raise Http404('Unknown exam mode')

    deck = get_object_or_404(Deck.objects.select_related('subject'), pk=deck_id)
    session_key = exam_session_key(deck, mode)
    duration = _duration_minutes(request)

    if request.method == 'POST':
        # One submission per rendered exam: a refreshed POST carries a token
        # the session has already consumed, so no duplicate attempt is saved.
        attempt_token = (request.POST.get('attempt_token') or '').strip()
        expected_token = request.session.get(session_key)
        if not attempt_token or attempt_token != expected_token:
            messages.info(
                request,
                'That exam was already submitted. Open it again to retake it.',
            )
            return redirect('tupas:deck_detail', deck_id=deck.id)
        exam_seed = clean_exam_seed(request.POST.get('exam_seed'))
        exam_filter = (request.POST.get('exam_filter') or '').strip().lower()
        retry_ids = parse_retry_ids(request.POST.get('retry_ids'))
        duration = _duration_minutes(request)
    else:
        attempt_token = uuid4().hex
        request.session[session_key] = attempt_token
        exam_seed = new_exam_seed()
        exam_filter = (request.GET.get('filter') or '').strip().lower()
        retry_ids = parse_retry_ids(request.GET.get('retry_ids'))

    exam_filter = 'weak' if exam_filter == 'weak' else 'all'
    retry_ids_csv = ','.join(str(pk) for pk in retry_ids)

    questions = build_exam_questions(
        deck,
        exam_mode_value,
        seed=exam_seed,
        item_ids=retry_ids or None,
        weak_for=request.user if (exam_filter == 'weak' and not retry_ids) else None,
    )

    if not questions:
        messages.info(request, 'This deck does not have questions for that exam mode yet.')
        return redirect('tupas:deck_detail', deck_id=deck.id)

    started_at = timezone.now()
    context = {
        'deck': deck,
        'exam_mode_key': mode,
        'exam_mode_label': ExamAttempt.ExamMode(exam_mode_value).label,
        'questions': questions,
        'question_count': len(questions),
        'exam_seed': exam_seed,
        'attempt_token': attempt_token,
        'exam_filter': exam_filter,
        'retry_ids_csv': retry_ids_csv,
        'retry_count': len(retry_ids),
        'is_weak_exam': exam_filter == 'weak',
        'duration_minutes': duration,
        'exam_deadline': (
            (started_at + timedelta(minutes=duration)).isoformat()
            if duration else None
        ),
        'show_results': False,
    }

    if request.method != 'POST':
        return render(request, 'tupas/exam_mode.html', context)

    score = 0
    results = []
    details = []

    for question in questions:
        item = question['item']
        question_type = question['type']
        checks = []
        is_typo = False
        statement = item.prompt_text

        if question_type == ExamAttempt.ExamMode.MULTIPLE_CHOICE:
            submitted_id = request.POST.get(f'q_{item.id}')
            selected = next(
                (choice for choice in question['choices'] if str(choice.id) == submitted_id),
                None,
            )
            is_correct = bool(selected and selected.is_correct)
            user_answer = selected.choice_text if selected else ''
            correct_answer = ' / '.join(
                choice.choice_text for choice in question['choices'] if choice.is_correct
            ) or '—'

        elif question_type == ExamAttempt.ExamMode.TRUE_FALSE:
            submitted = (request.POST.get(f'q_{item.id}') or '').strip()
            expected = question.get('answer') or 'True'
            is_correct = submitted.upper() == expected.upper()
            user_answer = submitted or 'No answer'
            correct_answer = expected
            statement = question.get('statement') or item.prompt_text

        elif question_type == ExamAttempt.ExamMode.MATCHING:
            submitted = (request.POST.get(f'q_{item.id}') or '').strip().upper()
            expected_letter = question.get('answer_letter') or ''
            expected = question.get('answer') or ''
            is_correct = bool(expected_letter) and submitted == expected_letter
            user_answer = submitted or 'No answer'
            correct_answer = f'{expected_letter}. {expected}'
            # Also accept the exact text if they typed it instead of the letter.
            if not is_correct and expected:
                is_correct, is_typo = check_answer_match(
                    request.POST.get(f'q_{item.id}', ''), expected,
                )

        elif question_type == ExamAttempt.ExamMode.IDENTIFICATION:
            raw_answer = request.POST.get(f'q_{item.id}', '').strip()
            is_correct, is_typo = check_answer_match(raw_answer, item.main_answer)
            if not item.main_answer:
                is_correct = False
                is_typo = False
            user_answer = raw_answer
            correct_answer = item.main_answer or '—'

        else:
            submitted = [
                request.POST.get(f'q_{item.id}_{slot}', '')
                for slot in question['slots']
            ]
            is_correct, checks = grade_enumeration(item, submitted)
            user_answer = ', '.join(value for value in submitted if value.strip())
            correct_answer = ', '.join(answer.answer_text for answer in question['enum_answers'])

        if is_correct:
            score += 1

        result = {
            'item': item,
            'item_id': item.id,
            'type': question_type,
            'type_label': ExamAttempt.ExamMode(question_type).label,
            'is_correct': is_correct,
            'is_typo_accepted': bool(is_typo),
            'user_answer': user_answer or 'No answer',
            'correct_answer': correct_answer,
            'explanation': item.explanation,
            'checks': checks,
        }
        if question_type == ExamAttempt.ExamMode.TRUE_FALSE:
            result['statement'] = statement
        results.append(result)

        details.append({
            'prompt': (
                question.get('statement')
                or item.prompt_text
            )[:500],
            'submitted': (user_answer or '')[:500],
            'expected': (correct_answer or '')[:500],
            'is_correct': is_correct,
            'is_typo_accepted': bool(
                result.get('is_typo_accepted')
                or any(getattr(c, 'is_typo', False) for c in (checks or []))
            ),
            'explanation': (item.explanation or '')[:1000],
            'item_id': item.id,
            'item_type': item.item_type,
        })

    total_items = len(questions)
    percentage = (
        (Decimal(score) / Decimal(total_items) * Decimal('100')).quantize(Decimal('0.01'))
        if total_items else Decimal('0.00')
    )
    xp_awarded = exam_xp(percentage)
    wrong_item_ids = [
        result['item'].id for result in results if not result['is_correct']
    ]

    with transaction.atomic():
        attempt = ExamAttempt.objects.create(
            user=request.user,
            deck=deck,
            exam_mode=exam_mode_value,
            score=score,
            total_items=total_items,
            percentage=percentage,
            details_json=json.dumps(details),
        )
        # Missed items immediately join the student's Weak Spots queue.
        for item_id in wrong_item_ids:
            progress, _ = ItemProgress.objects.update_or_create(
                user=request.user,
                review_item_id=item_id,
                defaults={
                    'mastery_status': ItemProgress.MasteryStatus.NEEDS_REVIEW,
                },
            )
            schedule_next_review(progress, ItemProgress.MasteryStatus.NEEDS_REVIEW)
    request.session.pop(session_key, None)
    record_study_day(request.user)
    award_xp(request.user, xp_awarded)
    record_activity(
        request.user,
        ActivityLog.Action.EXAM_SUBMIT,
        f'{deck.title} — {percentage}% ({score}/{total_items})',
        request,
    )

    # Post-exam diagnosis: accuracy by item type.
    type_stats = {}
    for result in results:
        stats = type_stats.setdefault(result['type_label'], {'correct': 0, 'total': 0})
        stats['total'] += 1
        if result['is_correct']:
            stats['correct'] += 1
    diagnosis = [
        {
            'type': label,
            'correct': stats['correct'],
            'total': stats['total'],
            'percent': round(stats['correct'] / stats['total'] * 100) if stats['total'] else 0,
        }
        for label, stats in type_stats.items()
    ]

    context.update({
        'show_results': True,
        'results': results,
        'attempt': attempt,
        'score': score,
        'percentage': percentage,
        'wrong_item_ids': wrong_item_ids,
        'retry_ids_csv': ','.join(str(pk) for pk in wrong_item_ids),
        'xp_awarded': xp_awarded,
        'diagnosis': diagnosis,
    })
    messages.success(request, f'Exam saved: {score}/{total_items} correct ({percentage}%).')
    return render(request, 'tupas/exam_mode.html', context)


@student_only
@never_cache
def exam_attempt_detail(request, attempt_id):
    """Replay a saved exam attempt from details_json (owner only)."""
    attempt = get_object_or_404(
        ExamAttempt.objects.select_related('deck', 'deck__subject'),
        pk=attempt_id,
        user=request.user,
    )
    try:
        details = json.loads(attempt.details_json or '[]')
    except (TypeError, ValueError):
        details = []
    if not isinstance(details, list):
        details = []

    correct = sum(1 for row in details if row.get('is_correct'))
    type_stats = {}
    for row in details:
        label = row.get('item_type') or 'ITEM'
        stats = type_stats.setdefault(label, {'correct': 0, 'total': 0})
        stats['total'] += 1
        if row.get('is_correct'):
            stats['correct'] += 1
    diagnosis = [
        {
            'type': label,
            'correct': stats['correct'],
            'total': stats['total'],
            'percent': round(stats['correct'] / stats['total'] * 100) if stats['total'] else 0,
        }
        for label, stats in type_stats.items()
    ]

    return render(request, 'tupas/exam_attempt_detail.html', {
        'attempt': attempt,
        'details': details,
        'correct': correct,
        'diagnosis': diagnosis,
    })

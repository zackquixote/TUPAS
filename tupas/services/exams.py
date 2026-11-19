"""Seeded exam paper construction, retry parsing, and attempt tokens."""

import random
import re
import secrets

from ..models import ExamAttempt, ItemProgress

EXAM_SEED_RE = re.compile(r'^[0-9a-fA-F]{8,64}$')


def exam_session_key(deck, mode):
    return f'tupas:exam-token:{deck.pk}:{mode}'


def new_exam_seed():
    return secrets.token_hex(8)


def clean_exam_seed(value):
    """Accept a hex seed from the form; fall back to a fresh one if tampered with."""
    seed = (value or '').strip()
    if EXAM_SEED_RE.match(seed):
        return seed
    return new_exam_seed()


def parse_retry_ids(value):
    """Parse a comma-separated list of ReviewItem ids from the retry link."""
    ids = []
    for chunk in str(value or '').split(','):
        chunk = chunk.strip()
        if chunk.isdigit():
            ids.append(int(chunk))
    return ids[:200]


def _shuffle_exam(questions, deck, exam_mode, seed):
    """Shuffle questions and MCQ choices with a seed.

    The same seed rebuilds the exact same paper, so a retake varies while
    grading and result rendering stay fully deterministic.
    """
    rng = random.Random(f'{seed}:{deck.pk}:{exam_mode}')
    rng.shuffle(questions)
    for question in questions:
        if (
            question['type'] == ExamAttempt.ExamMode.MULTIPLE_CHOICE
            and len(question['choices']) > 1
        ):
            rng.shuffle(question['choices'])
        if (
            question['type'] == ExamAttempt.ExamMode.TRUE_FALSE
            and question.get('choices')
        ):
            rng.shuffle(question['choices'])
        if (
            question['type'] == ExamAttempt.ExamMode.MATCHING
            and question.get('right_pool')
        ):
            rng.shuffle(question['right_pool'])
    return questions


def _true_false_question(item, rng):
    """Pair ~50% of items with their true answer and ~50% with a distractor.

    Deterministic per item using the item id so a rebuilt paper stays stable
    even before the seeded shuffle.
    """
    truth = item.main_answer or ''
    if not truth:
        return None
    correct = rng.random() < 0.5
    if correct:
        statement = truth
        answer = 'True'
    else:
        # Use a provided wrong choice if available; otherwise negate / shift.
        wrong = None
        for choice in item.choices.all():
            if not choice.is_correct and normalize_text(choice.choice_text) != normalize_text(truth):
                wrong = choice.choice_text
                break
        if not wrong:
            wrong = f'It is NOT true that {truth}'
        statement = wrong
        answer = 'False'
    return {
        'statement': statement,
        'answer': answer,
        'choices': [
            {'letter': 'A', 'text': 'True'},
            {'letter': 'B', 'text': 'False'},
        ],
    }


def normalize_text(value):
    return ' '.join(str(value or '').split()).casefold()


def build_exam_questions(deck, exam_mode, seed=None, item_ids=None, weak_for=None):
    """Create the question set for one exam mode, optionally shuffled by seed.

    ``item_ids`` limits the paper to specific review items (retake-wrong mode);
    ``weak_for`` limits it to that user's NEEDS_REVIEW items.
    """
    items = (
        deck.items
        .prefetch_related('choices', 'enum_answers')
        .order_by('id')
    )
    if item_ids:
        items = items.filter(pk__in=item_ids)
    if weak_for is not None:
        items = items.filter(
            progress_records__user=weak_for,
            progress_records__mastery_status=ItemProgress.MasteryStatus.NEEDS_REVIEW,
        ).distinct()
    questions = []
    rng = random.Random(seed or deck.pk)

    for item in items:
        choices = list(item.choices.all())
        enum_answers = list(item.enum_answers.all())
        question_type = None

        if exam_mode == ExamAttempt.ExamMode.MULTIPLE_CHOICE and choices:
            question_type = ExamAttempt.ExamMode.MULTIPLE_CHOICE
        elif exam_mode == ExamAttempt.ExamMode.IDENTIFICATION and item.main_answer:
            question_type = ExamAttempt.ExamMode.IDENTIFICATION
        elif exam_mode == ExamAttempt.ExamMode.ENUMERATION and enum_answers:
            question_type = ExamAttempt.ExamMode.ENUMERATION
        elif exam_mode == ExamAttempt.ExamMode.TRUE_FALSE and item.main_answer:
            question_type = ExamAttempt.ExamMode.TRUE_FALSE
        elif exam_mode == ExamAttempt.ExamMode.MATCHING and item.main_answer:
            question_type = ExamAttempt.ExamMode.MATCHING
        elif exam_mode == ExamAttempt.ExamMode.MIXED:
            if choices:
                question_type = ExamAttempt.ExamMode.MULTIPLE_CHOICE
            elif enum_answers:
                question_type = ExamAttempt.ExamMode.ENUMERATION
            elif item.main_answer:
                question_type = ExamAttempt.ExamMode.IDENTIFICATION

        if question_type is None:
            continue

        question = {
            'item': item,
            'type': question_type,
            'type_label': ExamAttempt.ExamMode(question_type).label,
            'choices': choices,
            'enum_answers': enum_answers,
            'slots': list(range(1, len(enum_answers) + 1)),
        }

        if question_type == ExamAttempt.ExamMode.TRUE_FALSE:
            tf = _true_false_question(item, rng)
            if not tf:
                continue
            question['statement'] = tf['statement']
            question['answer'] = tf['answer']
            question['choices'] = tf['choices']

        if question_type == ExamAttempt.ExamMode.MATCHING:
            question['left'] = item.prompt_text
            question['answer'] = item.main_answer or ''

        questions.append(question)

    # Build a shared lettered right-column pool for MATCHING.
    if exam_mode == ExamAttempt.ExamMode.MATCHING or any(
        q['type'] == ExamAttempt.ExamMode.MATCHING for q in questions
    ):
        answers = []
        seen = set()
        for q in questions:
            if q['type'] != ExamAttempt.ExamMode.MATCHING:
                continue
            text = (q.get('answer') or '').strip()
            key = normalize_text(text)
            if text and key not in seen:
                seen.add(key)
                answers.append(text)
        rng.shuffle(answers)
        letters = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
        right_pool = [
            {'letter': letters[i], 'text': text}
            for i, text in enumerate(answers[:26])
        ]
        lookup = {normalize_text(item['text']): item['letter'] for item in right_pool}
        for q in questions:
            if q['type'] == ExamAttempt.ExamMode.MATCHING:
                q['right_pool'] = right_pool
                q['answer_letter'] = lookup.get(normalize_text(q.get('answer') or ''), '')

    if seed:
        _shuffle_exam(questions, deck, exam_mode, seed)

    return questions

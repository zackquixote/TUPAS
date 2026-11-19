"""Answer grading for enumeration drills and exams.

Includes typo-tolerant matching (``difflib`` similarity or edit distance
``<= 1`` on words of 5+ characters) while still requiring exact digits.
"""

import re
from difflib import SequenceMatcher

from .text import normalize

SIMILARITY_THRESHOLD = 0.85
FUZZY_MIN_CHARS = 5


class SlotCheck:
    """Per-slot enumeration result.

    Truthy only when the slot is correct, so existing
    ``sum(1 for check in checks if check)`` keeps working.
    """

    def __init__(self, is_correct, is_typo=False, expected=''):
        self.is_correct = bool(is_correct)
        self.is_typo = bool(is_typo and is_correct)
        self.expected = expected or ''

    def __bool__(self):
        return self.is_correct

    def __eq__(self, other):
        if isinstance(other, SlotCheck):
            return self.is_correct == other.is_correct and self.is_typo == other.is_typo
        if isinstance(other, bool):
            return self.is_correct == other
        return NotImplemented

    def __hash__(self):
        return hash((self.is_correct, self.is_typo))

    def __repr__(self):
        return f'SlotCheck(correct={self.is_correct}, typo={self.is_typo})'


def _digits(value):
    return re.findall(r'\d+', str(value or ''))


def edit_distance_leq1(a, b):
    """True when Levenshtein distance between a and b is 0 or 1."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la > lb:
        a, b, la, lb = b, a, lb, la
    i = j = used = 0
    while i < la and j < lb:
        if a[i] == b[j]:
            i += 1
            j += 1
            continue
        used += 1
        if used > 1:
            return False
        if la == lb:
            i += 1
            j += 1
        elif la < lb:
            j += 1
        else:
            i += 1
    used += (lb - j) + (la - i)
    return used <= 1


def check_answer_match(submitted, expected):
    """Typo-tolerant comparison. Returns ``(is_correct, is_typo_accepted)``."""
    submitted_raw = str(submitted or '').strip()
    expected_raw = str(expected or '').strip()
    if not submitted_raw or not expected_raw:
        return False, False

    a = normalize(submitted_raw)
    b = normalize(expected_raw)
    if a == b:
        return True, False

    # Digits must match exactly so dates, counts, and formulas stay strict.
    if _digits(submitted_raw) != _digits(expected_raw):
        return False, False

    # Very short answers stay exact.
    if len(b) < FUZZY_MIN_CHARS and len(a) < FUZZY_MIN_CHARS:
        return False, False

    ratio = SequenceMatcher(None, a, b).ratio()
    if ratio >= SIMILARITY_THRESHOLD:
        return True, True

    # Token-level: allow one edit on any word of 5+ characters.
    a_words = a.split()
    b_words = b.split()
    if len(a_words) != len(b_words):
        return False, False

    edits = 0
    for wa, wb in zip(a_words, b_words):
        if wa == wb:
            continue
        if (
            len(wa) >= FUZZY_MIN_CHARS
            and len(wb) >= FUZZY_MIN_CHARS
            and edit_distance_leq1(wa, wb)
        ):
            edits += 1
            if edits > 1:
                return False, False
            continue
        return False, False

    return edits <= 1, edits > 0


def grade_enumeration(item, submitted):
    """Grade typed enumeration answers. Returns (is_correct, per-slot checks).

    Strict-order items compare slot by slot. Any-order items award each slot
    independently when its text matches an unclaimed expected answer.
    """
    expected_texts = [answer.answer_text for answer in item.enum_answers.all()]
    expected_norm = [normalize(text) for text in expected_texts]
    given = [str(value or '').strip() for value in submitted]

    if item.is_strict_order:
        checks = []
        for index, raw in enumerate(given):
            if index >= len(expected_texts):
                checks.append(SlotCheck(False))
                continue
            is_correct, is_typo = check_answer_match(raw, expected_texts[index])
            checks.append(SlotCheck(is_correct, is_typo, expected_texts[index]))
        while len(checks) < len(expected_texts):
            checks.append(SlotCheck(False))
        is_correct = bool(expected_texts) and len(given) == len(expected_texts) and all(
            check.is_correct for check in checks
        )
        return is_correct, checks

    remaining = dict.fromkeys(expected_norm, 1)  # expected values are unique
    checks = []
    for raw in given:
        value = normalize(raw)
        if not value:
            checks.append(SlotCheck(False))
            continue
        if remaining.get(value, 0) > 0:
            checks.append(SlotCheck(True, False, raw))
            remaining[value] = 0
            continue
        # Fuzzy against unused expected values.
        matched_text = None
        matched_norm = None
        is_typo = False
        for norm, leftover in remaining.items():
            if leftover <= 0:
                continue
            original = expected_texts[expected_norm.index(norm)]
            is_correct, typo = check_answer_match(raw, original)
            if is_correct:
                matched_text = original
                matched_norm = norm
                is_typo = typo
                break
        if matched_norm is not None:
            checks.append(SlotCheck(True, is_typo, matched_text))
            remaining[matched_norm] = 0
        else:
            checks.append(SlotCheck(False))

    is_correct = bool(expected_texts) and len(given) == len(expected_texts) and all(
        check.is_correct for check in checks
    )
    return is_correct, checks

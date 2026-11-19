"""Shared text normalisation and form-value helpers."""

import re

_ANSWER_PREFIX_RE = re.compile(r'^\s*(?:\d+[.)]|[-*\u2022])\s*')


def normalize(value):
    """Case-insensitive, whitespace-normalised comparison value."""
    return re.sub(r'\s+', ' ', str(value or '')).strip().casefold()


def clean_multiline(value, limit=5000):
    """Trim a textarea value and cap it at the stored column's practical size."""
    return str(value or '').strip()[:limit]


def split_answers(raw):
    """Turn a newline-separated enumeration textarea into unique answers."""
    answers = []
    seen = set()
    for line in str(raw or '').splitlines():
        text = _ANSWER_PREFIX_RE.sub('', line).strip()
        key = normalize(text)
        if text and key not in seen:
            seen.add(key)
            answers.append(text[:255])
    return answers[:20]

"""AI Auto-Reviewer Generator service.

Reads provider credentials from environment variables only:
    GROQ_API_KEY
    GEMINI_API_KEY
    OPENROUTER_API_KEY

Optional overrides:
    TUPAS_AI_PROVIDER   "groq", "gemini", or "openrouter"
    GROQ_MODEL
    GEMINI_MODEL
    OPENROUTER_MODEL

No API key is ever logged, stored in the database, or written to a template.
"""

import base64
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request

from django.db import transaction
from django.http import JsonResponse

from .models import EnumerationItem, QuizChoice, ReviewItem

logger = logging.getLogger(__name__)

GROQ_ENDPOINT = 'https://api.groq.com/openai/v1/chat/completions'
GEMINI_ENDPOINT = 'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent'
OPENROUTER_ENDPOINT = 'https://openrouter.ai/api/v1/chat/completions'
DEFAULT_GROQ_MODEL = 'llama-3.3-70b-versatile'
DEFAULT_GEMINI_MODEL = 'gemini-3.5-flash-lite'
GEMINI_FALLBACK_MODELS = (
    'gemini-3.5-flash-lite',
    'gemini-2.5-flash-lite',
    'gemini-flash-lite-latest',
    'gemini-3.8-flash',
)
DEFAULT_OPENROUTER_MODEL = 'google/gemma-4-31b-it:free'

PROVIDER_LABELS = {
    'groq': 'Groq',
    'gemini': 'Google Gemini',
    'openrouter': 'OpenRouter',
}
PROVIDER_ENV_VARS = {
    'groq': 'GROQ_API_KEY',
    'gemini': 'GEMINI_API_KEY',
    'openrouter': 'OPENROUTER_API_KEY',
}
PROVIDER_MODEL_ENVS = {
    'groq': 'GROQ_MODEL',
    'gemini': 'GEMINI_MODEL',
    'openrouter': 'OPENROUTER_MODEL',
}
PROVIDER_DEFAULT_MODELS = {
    'groq': DEFAULT_GROQ_MODEL,
    'gemini': DEFAULT_GEMINI_MODEL,
    'openrouter': DEFAULT_OPENROUTER_MODEL,
}
HIGHLIGHT_RE = re.compile(r'\[\[(.+?)\]\]')


class AIError(Exception):
    """A user-safe error raised by the AI generator."""


def _env(name, default=''):
    return os.environ.get(name, default).strip()


def _provider_status(provider):
    """Build a status dict for one provider (configured or missing)."""
    env_var = PROVIDER_ENV_VARS[provider]
    key = _env(env_var)
    if not key:
        return _missing_status(PROVIDER_LABELS[provider], env_var)
    return {
        'configured': True,
        'provider': provider,
        'provider_label': PROVIDER_LABELS[provider],
        'model': _env(PROVIDER_MODEL_ENVS[provider], PROVIDER_DEFAULT_MODELS[provider]),
        'env_var': env_var,
        'error': None,
    }


def get_ai_status(provider=None):
    """Return public provider status; never include the secret key.

    ``provider`` may name a specific provider to report on; otherwise the
    default provider (forced or first configured) is reported.
    """
    forced_provider = _env('TUPAS_AI_PROVIDER').lower()

    if forced_provider and forced_provider not in {'groq', 'gemini', 'openrouter', 'local'}:
        return {
            'configured': False,
            'provider': None,
            'provider_label': None,
            'model': None,
            'env_var': None,
            'error': (
                'TUPAS_AI_PROVIDER must be "groq", "gemini", "openrouter", or "local".'
            ),
        }

    if provider is not None:
        provider = str(provider).strip().lower()
        if provider == 'local':
            return {
                'configured': True,
                'provider': 'local',
                'provider_label': 'TUPAS Smart Engine',
                'model': 'Built-in Rule-Based Extractor',
                'env_var': None,
                'error': None,
            }
        elif provider == 'gemini-pro':
            env_var = PROVIDER_ENV_VARS['gemini']
            key = _env(env_var)
            if not key:
                return _missing_status('Google Gemini (Deep Reasoning)', env_var)
            return {
                'configured': True,
                'provider': 'gemini',
                'provider_label': 'Google Gemini 2.5 Flash (Deep Reasoning)',
                'model': 'gemini-2.5-flash',
                'env_var': env_var,
                'error': None,
            }
        elif provider in {'gemini', 'gemini-flash'}:
            env_var = PROVIDER_ENV_VARS['gemini']
            key = _env(env_var)
            if not key:
                return _missing_status('Google Gemini (Fast & Efficient)', env_var)
            return {
                'configured': True,
                'provider': 'gemini',
                'provider_label': 'Google Gemini 3.5 Flash Lite (Fast & Efficient)',
                'model': _env(PROVIDER_MODEL_ENVS['gemini'], PROVIDER_DEFAULT_MODELS['gemini']),
                'env_var': env_var,
                'error': None,
            }
        elif provider not in {'groq', 'openrouter'}:
            return {
                'configured': False,
                'provider': None,
                'provider_label': None,
                'model': None,
                'env_var': None,
                'error': 'Unknown AI provider.',
            }
        if forced_provider and forced_provider != provider:
            return {
                'configured': False,
                'provider': None,
                'provider_label': PROVIDER_LABELS[provider],
                'model': None,
                'env_var': PROVIDER_ENV_VARS[provider],
                'error': (
                    f'AI provider is locked to {forced_provider} by '
                    'TUPAS_AI_PROVIDER.'
                ),
            }
        return _provider_status(provider)

    if forced_provider:
        if forced_provider == 'local':
            return {
                'configured': True,
                'provider': 'local',
                'provider_label': 'TUPAS Smart Engine',
                'model': 'Built-in Rule-Based Extractor',
                'env_var': None,
                'error': None,
            }
        return _provider_status(forced_provider)

    for candidate in ('groq', 'gemini', 'openrouter'):
        if _env(PROVIDER_ENV_VARS[candidate]):
            return _provider_status(candidate)

    # No external key is configured: fall back to the built-in local engine
    # so the AI generator works out of the box (see README).
    return {
        'configured': True,
        'provider': 'local',
        'provider_label': 'TUPAS Smart Engine',
        'model': 'Built-in Rule-Based Extractor',
        'env_var': None,
        'error': None,
    }


def get_available_providers():
    """Return the list of AI engines available for students to select."""
    forced_provider = _env('TUPAS_AI_PROVIDER').lower()
    result = []

    # 1. Gemini Models (if GEMINI_API_KEY is present)
    if _env(PROVIDER_ENV_VARS['gemini']) and forced_provider in {'', 'gemini'}:
        result.append({
            'provider': 'gemini',
            'provider_label': 'Google Gemini 3.5 Flash Lite (Fast & Efficient)',
            'model': _env(
                PROVIDER_MODEL_ENVS['gemini'],
                PROVIDER_DEFAULT_MODELS['gemini'],
            ),
        })
        result.append({
            'provider': 'gemini-pro',
            'provider_label': 'Google Gemini 2.5 Flash (Deep Reasoning & Detailed)',
            'model': 'gemini-2.5-flash',
        })

    # 2. Groq (if GROQ_API_KEY is present)
    if _env(PROVIDER_ENV_VARS['groq']) and forced_provider in {'', 'groq'}:
        result.append({
            'provider': 'groq',
            'provider_label': 'Groq Cloud (High-Speed Llama 3.3)',
            'model': _env(
                PROVIDER_MODEL_ENVS['groq'],
                PROVIDER_DEFAULT_MODELS['groq'],
            ),
        })

    # 3. OpenRouter (if OPENROUTER_API_KEY is present)
    if _env(PROVIDER_ENV_VARS['openrouter']) and forced_provider in {'', 'openrouter'}:
        result.append({
            'provider': 'openrouter',
            'provider_label': 'OpenRouter (Multi-Model Gateway)',
            'model': _env(
                PROVIDER_MODEL_ENVS['openrouter'],
                PROVIDER_DEFAULT_MODELS['openrouter'],
            ),
        })

    # 4. TUPAS Smart Local Engine (Always available, instant, zero quota needed)
    if forced_provider in {'', 'local'}:
        result.append({
            'provider': 'local',
            'provider_label': 'TUPAS Smart Engine (Offline / Instant Recall)',
            'model': 'Built-in Heuristic Extractor',
        })

    return result


def _missing_status(provider_label, env_var):
    return {
        'configured': False,
        'provider': None,
        'provider_label': provider_label,
        'model': None,
        'env_var': env_var,
        'error': (
            f'{provider_label} was selected, but {env_var} is not set. '
            'Add it to your environment and restart the Django server.'
        ),
    }


def _get_config(provider=None):
    status = get_ai_status(provider)
    if not status['configured']:
        raise AIError(status['error'])
    if status['provider'] == 'local':
        return dict(status, api_key='')
    key_name = PROVIDER_ENV_VARS[status['provider']]
    config = dict(status)
    config['api_key'] = _env(key_name)
    return config


def _build_user_prompt(notes, limit, selected_types=None, exhaustive=False, exam_style='EXACT'):
    if selected_types is None:
        selected_types = ['FLIP_CARD', 'HIGHLIGHT_NOTE', 'ENUMERATION']

    type_to_key = {
        'FLIP_CARD': 'flip_cards',
        'HIGHLIGHT_NOTE': 'highlight_notes',
        'ENUMERATION': 'enumerations',
    }
    blocks = {
        'flip_cards': '''  "flip_cards": [
    {
      "prompt_text": "Question shown on the front of the card",
      "main_answer": "Correct answer (max 255 characters)",
      "explanation": "One or two sentences explaining the answer",
      "distractors": ["wrong option 1", "wrong option 2", "wrong option 3"]
    }
  ]''',
        'highlight_notes': '''  "highlight_notes": [
    {
      "prompt_text": "A short paragraph with 2-5 important terms wrapped like [[this]]",
      "explanation": "One sentence explaining the highlighted concepts"
    }
  ]''',
        'enumerations': '''  "enumerations": [
    {
      "prompt_text": "List the steps / items of ...",
      "answers": ["first accepted answer", "second accepted answer"],
      "is_strict_order": true,
      "explanation": "One sentence explaining the list",
      "mnemonic": "💡 Memory Trick: Remember P-L-O-C (Planning, Leading, Organizing, Controlling)"
    }
  ]''',
    }
    selected_keys = [type_to_key[t] for t in selected_types if t in type_to_key]
    schema = '{\n' + ',\n'.join(blocks[k] for k in selected_keys) + '\n}'

    type_labels = {
        'FLIP_CARD': 'FLIP_CARD',
        'HIGHLIGHT_NOTE': 'HIGHLIGHT_NOTE',
        'ENUMERATION': 'ENUMERATION',
    }
    requested = ', '.join(type_labels[t] for t in selected_types)

    if exhaustive:
        count_line = (
            f'EXHAUSTIVE COVERAGE MODE: Extract EVERY exam-worthy definition, concept, '
            f'person, date, formula, and enumeration list from these slides/notes so no '
            f'slide or section is left behind (aim for 12-20 items per selected type per '
            f'chunk across the selected types: {requested}). Return fewer only if the notes '
            f'truly lack more.'
        )
    else:
        count_line = (
            f'Create up to {limit} items for EACH selected type ({requested}). '
            f'Return fewer only when the notes cannot support that many, and never '
            f'invent facts to fill the quota. Do not repeat the same fact.'
        )

    rules = ['- Values are plain text, not markdown. Escape newlines as \\\\n if needed.\n']
    if 'FLIP_CARD' in selected_types:
        rules.append(
            '- FLIP_CARD: prompt_text is a clear question; main_answer is concise and at most '
            '255 characters; explanation is 1-2 sentences; distractors contains EXACTLY 3 '
            'plausible but definitely wrong answers, each at most 255 characters. Do not put '
            'the correct answer in distractors.\n'
        )
    if 'HIGHLIGHT_NOTE' in selected_types:
        rules.append(
            '- HIGHLIGHT_NOTE: prompt_text is a 2-5 sentence paragraph copied or paraphrased '
            'from the notes. Wrap every key term in double square brackets exactly like '
            '[[term]]. Include 2-5 highlighted terms. Do not use square brackets for anything '
            'else. explanation is one sentence.\n'
        )
    if 'ENUMERATION' in selected_types:
        rules.append(
            '- ENUMERATION: prompt_text asks for a list; answers has 2-8 short entries, each '
            'at most 255 characters. Set is_strict_order to true only when the sequence matters '
            '(steps, stages, chronological order); otherwise false. explanation is one sentence.\n'
        )
        rules.append(
            '- ENUMERATION mnemonic: always provide a catchy acronym, first-letter wordplay, or '
            'Taglish memory trick that helps the student recall the list. Start it with '
            '"💡 Memory Trick: " and keep it under 300 characters.\n'
        )
    if exam_style == 'TAGLISH':
        rules.append(
            '- PROFESSOR STYLE (TAGLISH): Keep prompt_text, main_answer, choices, and '
            'answers in English (matching formal exams), but write every explanation field '
            'in clear, helpful Taglish so the student understands why the answer is correct.\n'
        )
    elif exam_style == 'SITUATIONAL':
        rules.append(
            '- PROFESSOR STYLE (SITUATIONAL): Frame flip cards and multiple-choice '
            'questions as realistic scenarios, applications, or tricky board-exam style '
            'questions with plausible distractors. Keep answers accurate to the notes.\n'
        )
    else:
        rules.append(
            '- PROFESSOR STYLE (EXACT): Preserve the exact terminology, definitions, and '
            'enumeration lists from the student handout/PPT. Do not paraphrase key terms.\n'
        )
    rules.append('- Do not invent facts, numbers, names, or dates that are not in the notes.\n')
    rules.append('- The response must start with { and end with } and contain no commentary.\n')
    rules.append('- For unselected types, return an empty array [].\n')

    return (
        'You are an expert Philippine college reviewer writer. Build study material '
        'using ONLY the facts in the source notes below.\n\n'
        f'{count_line}\n\n'
        'Return ONLY one valid JSON object with exactly this shape:\n'
        f'{schema}\n\n'
        'Rules:\n'
        + ''.join(rules) +
        '\nSOURCE NOTES:\n'
        '"""\n'
        f'{notes}\n'
        '"""'
    )


def _post_json(url, payload, headers=None, timeout=120):
    body = json.dumps(payload).encode('utf-8')
    request = urllib.request.Request(
        url,
        data=body,
        headers={'Content-Type': 'application/json', **(headers or {})},
        method='POST',
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        # Keep the raw provider body in the server log only; students should
        # never see provider JSON, account ids, or quota diagnostics.
        detail = exc.read().decode('utf-8', errors='replace')[:800]
        logger.warning(
            'AI provider rejected the request: HTTP %s from %s :: %s',
            exc.code,
            url,
            detail,
        )
        raise AIError(
            f'The AI provider rejected the request (HTTP {exc.code}). '
            'Please try again in a moment.'
        ) from exc
    except urllib.error.URLError as exc:
        logger.warning('Could not reach AI provider %s: %s', url, exc.reason)
        raise AIError(
            'Could not reach the AI provider. Check your connection and try again.'
        ) from exc
    except TimeoutError as exc:
        logger.warning('AI provider %s timed out after %s seconds.', url, timeout)
        raise AIError('The AI provider took too long to respond. Please try again.') from exc
    except json.JSONDecodeError as exc:
        logger.warning('AI provider %s returned invalid JSON.', url)
        raise AIError('The AI provider returned a response that was not valid JSON.') from exc


def _call_openai_compatible(endpoint, config, user_prompt, provider_label, extra_headers=None):
    payload = {
        'model': config['model'],
        'temperature': 0.35,
        'max_tokens': 6000,
        'messages': [
            {
                'role': 'system',
                'content': (
                    'You write high-quality study reviewers. You always reply with a '
                    'single valid JSON object and nothing else.'
                ),
            },
            {'role': 'user', 'content': user_prompt},
        ],
    }
    headers = {'Authorization': f"Bearer {config['api_key']}"}
    if extra_headers:
        headers.update(extra_headers)

    data = _post_json(endpoint, payload, headers)
    try:
        return data['choices'][0]['message']['content']
    except (KeyError, IndexError, TypeError) as exc:
        raise AIError(
            f'{provider_label} returned an unexpected response. Please try again.'
        ) from exc


def _call_groq(config, user_prompt):
    return _call_openai_compatible(
        GROQ_ENDPOINT,
        config,
        user_prompt,
        provider_label='Groq',
    )


def _call_openrouter(config, user_prompt):
    return _call_openai_compatible(
        OPENROUTER_ENDPOINT,
        config,
        user_prompt,
        provider_label='OpenRouter',
        extra_headers={
            'HTTP-Referer': 'http://localhost:8000',
            'X-Title': 'TUPAS Reviewer',
        },
    )


def _call_gemini(config, user_prompt):
    payload = {
        'system_instruction': {
            'parts': [{
                'text': (
                    'You write high-quality study reviewers. You always reply with a '
                    'single valid JSON object and nothing else.'
                ),
            }],
        },
        'contents': [{
            'role': 'user',
            'parts': [{'text': user_prompt}],
        }],
        'generationConfig': {
            'temperature': 0.35,
            'maxOutputTokens': 6000,
            'responseMimeType': 'application/json',
        },
    }

    models = [config['model']]
    for candidate in GEMINI_FALLBACK_MODELS:
        if candidate not in models:
            models.append(candidate)

    last_error = None
    for model in models:
        url = GEMINI_ENDPOINT.format(model=model)
        data = None
        # A couple of short retries for transient 503/429 spikes, then try the next model.
        for attempt in range(3):
            try:
                data = _post_json(url, payload, {'x-goog-api-key': config['api_key']})
                break
            except AIError as exc:
                last_error = exc
                message = str(exc)
                transient = (
                    'HTTP 503' in message
                    or 'HTTP 429' in message
                    or 'high demand' in message.lower()
                    or 'unavailable' in message.lower()
                )
                deprecated = (
                    'HTTP 404' in message
                    or 'no longer available' in message.lower()
                    or 'not found' in message.lower()
                )
                if deprecated:
                    break
                if transient and attempt < 2:
                    time.sleep(1.2 * (attempt + 1))
                    continue
                if transient:
                    break
                raise
        if data is None:
            continue

        feedback = data.get('promptFeedback') or {}
        if feedback.get('blockReason'):
            raise AIError(
                'Gemini blocked the request. Try shorter or less sensitive notes.'
            )

        try:
            parts = data['candidates'][0]['content']['parts']
            text = ''.join(part.get('text', '') for part in parts)
            if text.strip():
                return text
            raise AIError('Gemini returned an empty response. Please try again.')
        except (KeyError, IndexError, TypeError) as exc:
            raise AIError('Gemini returned an unexpected response. Please try again.') from exc

    if last_error:
        raise last_error
    raise AIError('No available Gemini model could complete the request.')


def _extract_json_object(text):
    if not text or not str(text).strip():
        raise AIError('The AI returned an empty response. Please try again.')

    cleaned = str(text).strip()
    fenced = re.search(r'```(?:json)?\s*(.*?)```', cleaned, re.DOTALL | re.IGNORECASE)
    if fenced:
        cleaned = fenced.group(1).strip()

    candidates = [cleaned]
    start = cleaned.find('{')
    end = cleaned.rfind('}')
    if start != -1 and end > start:
        candidates.append(cleaned[start:end + 1])

    for candidate in candidates:
        for attempt in (candidate, re.sub(r',\s*([}\]])', r'\1', candidate)):
            try:
                data = json.loads(attempt)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                return data

    raise AIError(
        'The AI response did not contain valid JSON. Please try again with clearer notes.'
    )


def _text(value, max_length=None):
    if value is None or isinstance(value, (dict, list)):
        return ''
    result = str(value).strip()
    return result[:max_length] if max_length else result


def _normalized(value):
    return re.sub(r'\s+', ' ', str(value or '')).strip().casefold()


def _find_list(mapping, *keys):
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, list):
            return value
    return []


def _answer_text(value, max_length=255):
    if isinstance(value, dict):
        return _text(
            value.get('answer_text') or value.get('text') or value.get('answer'),
            max_length,
        )
    return _text(value, max_length)


def _as_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'y'}
    return bool(value)


def _add_keyword_markers(text, keywords):
    """Wrap the first occurrence of each supplied keyword when markers are missing."""
    for keyword in keywords:
        keyword = _text(keyword, 120)
        if not keyword:
            continue
        pattern = re.compile(re.escape(keyword), re.IGNORECASE)
        match = pattern.search(text)
        if match:
            text = f'{text[:match.start()]}[[{match.group(0)}]]{text[match.end():]}'
        if len(HIGHLIGHT_RE.findall(text)) >= 2:
            break
    return text


def _flatten_flat_items(data, flip_cards, highlight_notes, enumerations):
    """Accept a flat {"items": [...]} response as a fallback shape."""
    for entry in data.get('items', []) if isinstance(data.get('items'), list) else []:
        if not isinstance(entry, dict):
            continue
        item_type = str(entry.get('item_type') or entry.get('type') or '').upper()
        if 'FLIP' in item_type:
            flip_cards.append(entry)
        elif 'HIGHLIGHT' in item_type or 'NOTE' in item_type:
            highlight_notes.append(entry)
        elif 'ENUM' in item_type:
            enumerations.append(entry)


def normalize_generated_payload(data, limit=5):
    """Validate and clean an AI payload before it touches the database."""
    if not isinstance(data, dict):
        raise AIError('The AI response was not a JSON object.')

    flip_cards_raw = _find_list(data, 'flip_cards', 'flipCards', 'flip_card', 'FLIP_CARD')
    highlight_raw = _find_list(
        data, 'highlight_notes', 'highlightNotes', 'highlights', 'HIGHLIGHT_NOTE'
    )
    enumeration_raw = _find_list(
        data, 'enumerations', 'enumeration', 'enum_items', 'ENUMERATION'
    )
    _flatten_flat_items(data, flip_cards_raw, highlight_raw, enumeration_raw)

    flip_cards = []
    for entry in flip_cards_raw:
        if not isinstance(entry, dict):
            continue
        prompt = _text(
            entry.get('prompt_text') or entry.get('prompt') or entry.get('question'),
            4000,
        )
        answer = _text(
            entry.get('main_answer') or entry.get('answer'), 255
        )
        if not prompt or not answer:
            continue

        distraction_source = _find_list(
            entry,
            'distractors',
            'wrong_choices',
            'wrongChoices',
            'incorrect_choices',
            'choices',
        )
        seen = {_normalized(answer)}
        distractors = []
        for candidate in distraction_source:
            if isinstance(candidate, dict) and candidate.get('is_correct') is True:
                continue
            candidate_text = _answer_text(candidate, 255)
            normalized = _normalized(candidate_text)
            if not candidate_text or normalized in seen:
                continue
            seen.add(normalized)
            distractors.append(candidate_text)
            if len(distractors) == 3:
                break

        flip_cards.append({
            'prompt_text': prompt,
            'main_answer': answer,
            'explanation': _text(entry.get('explanation'), 3000),
            'distractors': distractors,
        })
        if len(flip_cards) >= limit:
            break

    highlight_notes = []
    for entry in highlight_raw:
        if isinstance(entry, str):
            entry = {'prompt_text': entry}
        if not isinstance(entry, dict):
            continue
        prompt = _text(
            entry.get('prompt_text') or entry.get('text') or entry.get('paragraph'),
            6000,
        )
        if not prompt:
            continue
        if not HIGHLIGHT_RE.search(prompt):
            keywords = _find_list(entry, 'keywords', 'key_terms', 'terms')
            prompt = _add_keyword_markers(prompt, keywords)
        if not HIGHLIGHT_RE.search(prompt):
            continue
        highlight_notes.append({
            'prompt_text': prompt,
            'explanation': _text(entry.get('explanation'), 3000),
        })
        if len(highlight_notes) >= limit:
            break

    enumerations = []
    for entry in enumeration_raw:
        if not isinstance(entry, dict):
            continue
        prompt = _text(entry.get('prompt_text') or entry.get('prompt'), 4000)
        answers_source = _find_list(
            entry, 'answers', 'enum_answers', 'items', 'answer_list'
        )
        seen = set()
        answers = []
        for candidate in answers_source:
            candidate_text = _answer_text(candidate, 255)
            normalized = _normalized(candidate_text)
            if not candidate_text or normalized in seen:
                continue
            seen.add(normalized)
            answers.append(candidate_text)
            if len(answers) >= 12:
                break
        if not prompt or len(answers) < 2:
            continue
        enumerations.append({
            'prompt_text': prompt,
            'answers': answers,
            'is_strict_order': _as_bool(
                entry.get('is_strict_order', entry.get('strict_order', False))
            ),
            'explanation': _text(entry.get('explanation'), 3000),
            'mnemonic': _text(
                entry.get('mnemonic')
                or entry.get('memory_trick')
                or entry.get('memory_aid')
                or entry.get('acronym'),
                500,
            ),
        })
        if len(enumerations) >= limit:
            break

    return {
        'flip_cards': flip_cards,
        'highlight_notes': highlight_notes,
        'enumerations': enumerations,
    }


def _call_provider(config, user_prompt):
    if config['provider'] == 'groq':
        return _call_groq(config, user_prompt)
    if config['provider'] == 'gemini':
        return _call_gemini(config, user_prompt)
    return _call_openrouter(config, user_prompt)


def _chunk_notes(notes, max_chunks=3, chunk_size=6000):
    """Split long notes into up to ``max_chunks`` logical chunks."""
    notes = notes.strip()
    if len(notes) <= chunk_size or max_chunks <= 1:
        return [notes]
    chunks = []
    current = ''
    for block in notes.split('\n\n'):
        if current and len(current) + len(block) + 2 > chunk_size and len(chunks) < max_chunks - 1:
            chunks.append(current.strip())
            current = block
        else:
            current = (current + '\n\n' + block) if current else block
    if current.strip():
        chunks.append(current.strip())
    if len(chunks) < 2 and len(notes) > chunk_size:
        chunks = []
        for i in range(0, len(notes), chunk_size):
            if len(chunks) >= max_chunks:
                break
            chunks.append(notes[i:i + chunk_size])
    return [c for c in chunks if c][:max_chunks]


def generate_reviewer_json(notes, limit=5, provider=None, selected_types=None, coverage_mode=None, exam_style='EXACT'):
    """Call the configured AI provider and return a validated payload.

    ``selected_types`` is a list of 'FLIP_CARD', 'HIGHLIGHT_NOTE', 'ENUMERATION'.
    ``coverage_mode`` is an int (items per selected type) or 'ALL' (exhaustive).
    ``exam_style`` is 'EXACT', 'TAGLISH', or 'SITUATIONAL'.
    """
    notes = str(notes or '').strip()
    if len(notes) < 80:
        raise AIError('Please provide at least 80 characters of source notes.')
    if len(notes) > 30000:
        raise AIError('The source notes are too long. Please keep them under 30,000 characters.')

    if selected_types is None:
        selected_types = ['FLIP_CARD', 'HIGHLIGHT_NOTE', 'ENUMERATION']
    selected_types = [t for t in selected_types if t in ('FLIP_CARD', 'HIGHLIGHT_NOTE', 'ENUMERATION')]
    if not selected_types:
        raise AIError('Select at least one item type to generate.')

    if coverage_mode is None:
        coverage_mode = limit
    exhaustive = str(coverage_mode).upper() == 'ALL'
    if exhaustive:
        per_type_limit = 30
    else:
        try:
            per_type_limit = max(1, min(int(coverage_mode), 30))
        except (ValueError, TypeError):
            per_type_limit = 5

    type_to_key = {
        'FLIP_CARD': 'flip_cards',
        'HIGHLIGHT_NOTE': 'highlight_notes',
        'ENUMERATION': 'enumerations',
    }
    selected_keys = {type_to_key[t] for t in selected_types}

    config = _get_config(provider)

    if config['provider'] == 'local':
        raw_payload = _local_deck_extractor(notes, limit=per_type_limit)
        filtered = {'flip_cards': [], 'highlight_notes': [], 'enumerations': []}
        for k in selected_keys:
            if k in raw_payload:
                filtered[k] = raw_payload[k][:per_type_limit]
        return filtered

    # Large decks need chunking even outside exhaustive mode, otherwise the
    # provider truncates long notes and later sections never become items.
    if len(notes) > 7000 and (exhaustive or per_type_limit > 10):
        chunks = _chunk_notes(notes, max_chunks=3, chunk_size=7000)
    else:
        chunks = [notes]

    merged = {'flip_cards': [], 'highlight_notes': [], 'enumerations': []}
    seen = {'flip_cards': set(), 'highlight_notes': set(), 'enumerations': set()}

    for chunk in chunks:
        user_prompt = _build_user_prompt(chunk, per_type_limit, selected_types, exhaustive, exam_style)
        raw_content = _call_provider(config, user_prompt)
        payload = normalize_generated_payload(_extract_json_object(raw_content), per_type_limit)
        for key in merged:
            if key not in selected_keys:
                payload[key] = []
        for key in merged:
            for item in payload.get(key, []):
                dedup_key = _normalized(item.get('prompt_text', ''))
                if dedup_key and dedup_key in seen[key]:
                    continue
                seen[key].add(dedup_key)
                merged[key].append(item)

    if not any(merged.values()):
        raise AIError(
            'The AI responded, but no usable reviewer items were produced. '
            'Add more detailed notes and try again.'
        )

    for key in merged:
        merged[key] = merged[key][:per_type_limit]
    return merged


@transaction.atomic
def save_generated_items(deck, payload):
    """Persist a normalized AI payload into the reviewer tables."""
    counts = {
        'flip_cards': 0,
        'highlight_notes': 0,
        'enumerations': 0,
        'quiz_choices': 0,
        'enum_answers': 0,
    }

    for card in payload.get('flip_cards', []):
        review_item = ReviewItem.objects.create(
            deck=deck,
            item_type=ReviewItem.ItemType.FLIP_CARD,
            prompt_text=card['prompt_text'],
            main_answer=card['main_answer'],
            explanation=card.get('explanation', ''),
        )
        QuizChoice.objects.create(
            review_item=review_item,
            choice_text=card['main_answer'],
            is_correct=True,
        )
        counts['quiz_choices'] += 1
        for distractor in card.get('distractors', [])[:3]:
            QuizChoice.objects.create(
                review_item=review_item,
                choice_text=distractor,
                is_correct=False,
            )
            counts['quiz_choices'] += 1
        counts['flip_cards'] += 1

    for note in payload.get('highlight_notes', []):
        ReviewItem.objects.create(
            deck=deck,
            item_type=ReviewItem.ItemType.HIGHLIGHT_NOTE,
            prompt_text=note['prompt_text'],
            explanation=note.get('explanation', ''),
        )
        counts['highlight_notes'] += 1

    for enumeration in payload.get('enumerations', []):
        # The mnemonic lives in ReviewItem.explanation, which the enumeration
        # drill renders behind a "Show Memory Trick" toggle.
        review_item = ReviewItem.objects.create(
            deck=deck,
            item_type=ReviewItem.ItemType.ENUMERATION,
            prompt_text=enumeration['prompt_text'],
            explanation=(
                enumeration.get('mnemonic')
                or enumeration.get('explanation', '')
            ),
            is_strict_order=enumeration.get('is_strict_order', False),
        )
        EnumerationItem.objects.bulk_create([
            EnumerationItem(
                review_item=review_item,
                answer_text=answer_text,
                order_number=index,
            )
            for index, answer_text in enumerate(enumeration['answers'], start=1)
        ])
        counts['enum_answers'] += len(enumeration['answers'])
        counts['enumerations'] += 1

    return counts


# ==============================================================================
# TUPAS STUDY BUDDY: PDF EXTRACTION, CHAT TUTOR & 1-CLICK DECK GENERATION
# ==============================================================================

def extract_text_from_pdf_stream(pdf_file):
    """Extract clean text and page count from a PDF file using pypdf."""
    from pypdf import PdfReader
    try:
        reader = PdfReader(pdf_file)
        pages_text = []
        page_count = len(reader.pages)
        for idx, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or '').strip()
            if text:
                pages_text.append(f"--- [Page {idx}] ---\n{text}")
        full_text = "\n\n".join(pages_text).strip()
        return full_text, page_count
    except Exception as exc:
        raise AIError(f"Could not read the PDF file: {exc}")


IMAGE_OCR_PROMPT = (
    'Transcribe all lecture slide text, whiteboard notes, bullet points, '
    'definitions, and formulas from this image accurately into clean '
    'structured study notes.'
)


def extract_text_from_pptx_stream_view(request):
    """Server-side PPTX fallback: extract slide text from an uploaded .pptx."""
    from .services.documents import extract_text_from_pptx_stream

    upload = request.FILES.get('file')
    if upload is None:
        return JsonResponse({'ok': False, 'error': 'Choose a .pptx file first.'}, status=400)
    try:
        text, page_count = extract_text_from_pptx_stream(upload)
    except AIError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
    return JsonResponse({'ok': True, 'text': text, 'page_count': page_count})


def extract_text_from_image(image_bytes, mime_type):
    """Transcribe a slide or whiteboard photo with Gemini multimodal input.

    Returns ``{'ok': True, 'text': ...}`` on success and
    ``{'ok': False, 'error': ...}`` when Gemini is unavailable, so the
    browser can fall back to manual typing.
    """
    api_key = _env('GEMINI_API_KEY')
    if not api_key:
        return {
            'ok': False,
            'error': (
                'Photo OCR needs GEMINI_API_KEY. Add it to your environment '
                'and restart Django, or paste the notes manually.'
            ),
        }
    if not image_bytes:
        return {'ok': False, 'error': 'The uploaded image was empty.'}

    model = _env('GEMINI_MODEL', DEFAULT_GEMINI_MODEL)
    payload = {
        'contents': [{
            'role': 'user',
            'parts': [
                {'text': IMAGE_OCR_PROMPT},
                {
                    'inline_data': {
                        'mime_type': mime_type,
                        'data': base64.b64encode(image_bytes).decode('ascii'),
                    },
                },
            ],
        }],
        'generationConfig': {'temperature': 0.1, 'maxOutputTokens': 4000},
    }

    try:
        data = _post_json(
            GEMINI_ENDPOINT.format(model=model),
            payload,
            {'x-goog-api-key': api_key},
        )
    except AIError as exc:
        return {'ok': False, 'error': str(exc)}

    feedback = data.get('promptFeedback') or {}
    if feedback.get('blockReason'):
        return {'ok': False, 'error': 'Gemini blocked this image. Try a clearer photo.'}

    try:
        parts = data['candidates'][0]['content']['parts']
        text = ''.join(part.get('text', '') for part in parts).strip()
    except (KeyError, IndexError, TypeError):
        return {
            'ok': False,
            'error': 'Gemini returned an unexpected response. Please try again.',
        }

    if not text:
        return {
            'ok': False,
            'error': 'No readable text was found in that image. Try a sharper photo.',
        }
    return {'ok': True, 'text': text}


def chat_with_tupas_tutor(document_text, student_message, chat_history=None):
    """Conversational study buddy response using AI or smart local fallback."""
    document_text = str(document_text or '').strip()
    student_message = str(student_message or '').strip()
    if not student_message:
        return "Hey there, student! Ask me anything about your handout, or tap one of the quick action buttons above!"

    # 1. Try Live AI API if key is available
    status = get_ai_status()
    if status['configured'] and status.get('provider') != 'local':
        try:
            return _call_ai_tutor_api(status, document_text, student_message, chat_history)
        except Exception:
            # Fall back to the local engine smoothly, but keep the cause on the
            # server log so operators can diagnose provider outages.
            logger.exception('Study Buddy API call failed; using the local engine.')

    # 2. Local Fallback Engine (Works 100% offline without any API key)
    return _local_study_buddy_engine(document_text, student_message)


def _call_ai_tutor_api(status, document_text, student_message, chat_history=None):
    """Call Groq, Gemini, or OpenRouter for the conversational chat tutor."""
    config = dict(status)
    key_name = {
        'groq': 'GROQ_API_KEY',
        'gemini': 'GEMINI_API_KEY',
        'openrouter': 'OPENROUTER_API_KEY',
    }[status['provider']]
    config['api_key'] = _env(key_name)

    doc_snippet = document_text[:14000] if len(document_text) > 14000 else document_text

    system_prompt = (
        "You are TUPAS Study Buddy, a cheerful, energetic, and brilliant AI study companion "
        "and tutor for Filipino students. You help them understand their lecture handouts.\n\n"
        "Guidelines:\n"
        "- Base your answers strictly on the provided STUDY DOCUMENT.\n"
        "- Be encouraging, enthusiastic, and friendly! Use helpful emojis.\n"
        "- Format with clear headings, bullet points, and bold terms for readability on mobile.\n"
        "- If the student asks for Taglish or speaks Tagalog, reply in natural, engaging conversational Taglish.\n"
        "- If asked to quiz, provide interactive questions with answer options (A, B, C, D) and invite them to answer.\n\n"
        f"STUDY DOCUMENT:\n\"\"\"\n{doc_snippet}\n\"\"\""
    )

    messages = []
    if chat_history:
        for msg in chat_history[-6:]:
            role = 'user' if msg.sender == 'STUDENT' else 'assistant'
            messages.append({'role': role, 'content': msg.message})

    messages.append({'role': 'user', 'content': student_message})

    if config['provider'] == 'groq':
        endpoint = GROQ_ENDPOINT
        payload = {
            'model': config['model'],
            'temperature': 0.5,
            'max_tokens': 2000,
            'messages': [{'role': 'system', 'content': system_prompt}] + messages,
        }
        res = _post_json(endpoint, payload, {'Authorization': f"Bearer {config['api_key']}"})
        return res['choices'][0]['message']['content']

    elif config['provider'] == 'openrouter':
        endpoint = OPENROUTER_ENDPOINT
        payload = {
            'model': config['model'],
            'temperature': 0.5,
            'max_tokens': 2000,
            'messages': [{'role': 'system', 'content': system_prompt}] + messages,
        }
        res = _post_json(endpoint, payload, {
            'Authorization': f"Bearer {config['api_key']}",
            'HTTP-Referer': 'http://localhost:8000',
            'X-Title': 'TUPAS Study Buddy',
        })
        return res['choices'][0]['message']['content']

    else:
        # Gemini. The key travels in the x-goog-api-key header so it never
        # appears in request URLs, proxy logs, or browser/dev-server output.
        url = GEMINI_ENDPOINT.format(model=config['model'])
        contents = []
        for m in messages:
            role = 'user' if m['role'] == 'user' else 'model'
            contents.append({'role': role, 'parts': [{'text': m['content']}]})

        payload = {
            'system_instruction': {'parts': [{'text': system_prompt}]},
            'contents': contents,
            'generationConfig': {'temperature': 0.5, 'maxOutputTokens': 2000},
        }
        res = _post_json(url, payload, {'x-goog-api-key': config['api_key']})
        return res['candidates'][0]['content']['parts'][0]['text']


# ==============================================================================
# SMART LOCAL ENGINE (NO API KEY REQUIRED)
# ==============================================================================

def _clean_doc_lines(text):
    lines = [line.strip() for line in text.splitlines()]
    return [l for l in lines if l and not l.startswith('--- [Page')]


def _extract_definitions(text):
    """Extract (term, definition) pairs from document text using regex heuristics."""
    definitions = []
    pattern_colon = re.compile(r'^([A-Z][A-Za-z0-9\s\-/]{2,40}):\s*(.{15,300})$', re.MULTILINE)
    for match in pattern_colon.finditer(text):
        term = match.group(1).strip()
        defn = match.group(2).strip()
        if len(term.split()) <= 5 and not term.lower().startswith(('page', 'chapter', 'section', 'note')):
            definitions.append((term, defn))

    pattern_is = re.compile(r'([A-Z][A-Za-z0-9\s\-/]{2,35})\s+(?:is defined as|refers to|means|is an?|are)\s+([^.\n]{15,250}\.)', re.IGNORECASE)
    for match in pattern_is.finditer(text):
        term = match.group(1).strip()
        defn = match.group(2).strip()
        if len(term.split()) <= 5 and not any(term.lower() == d[0].lower() for d in definitions):
            definitions.append((term, defn))

    return definitions


def _extract_lists(text):
    """Find numbered or bulleted enumerations from document text."""
    enumerations = []
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    current_title = ""
    current_items = []

    for i, line in enumerate(lines):
        # Match numbered item: 1. or 1) or a. or • or -
        num_match = re.match(r'^(?:[0-9]+[.)]|[a-zA-Z][.)]|[-•*])\s+(.+)$', line)
        if num_match:
            item_text = num_match.group(1).strip()
            if not current_items and i > 0 and len(lines[i-1]) < 120 and not lines[i-1].endswith('.'):
                current_title = lines[i-1].rstrip(':')
            current_items.append(item_text)
        else:
            if len(current_items) >= 2:
                title = current_title or "Key Elements / Items"
                enumerations.append((title, current_items[:10]))
            current_title = line.rstrip(':') if len(line) < 100 else ""
            current_items = []

    if len(current_items) >= 2:
        title = current_title or "Key Elements / Items"
        enumerations.append((title, current_items[:10]))

    return enumerations


def _local_study_buddy_engine(document_text, message):
    """Intelligent local tutor responding with markdown formatting."""
    msg = message.lower().strip()
    definitions = _extract_definitions(document_text)
    lists = _extract_lists(document_text)
    paragraphs = [p.strip() for p in document_text.split('\n\n') if len(p.strip()) > 50 and not p.strip().startswith('--- [Page')]

    # --- ACTION 1: SUMMARIZE ---
    if any(k in msg for k in ['summarize', 'summary', 'buod', 'recap', 'overview']):
        summary_points = []
        for p in paragraphs[:5]:
            first_sentence = p.split('.')[0].strip()
            if len(first_sentence) > 25:
                summary_points.append(f"- **{first_sentence}.**")

        defs_preview = [f"- **{term}**: {defn[:120]}..." for term, defn in definitions[:4]]

        return (
            "### 📝 Quick Document Summary\n\n"
            f"Here is your executive study breakdown of the uploaded handout:\n\n"
            "#### 🌟 Main Highlights\n" +
            ("\n".join(summary_points[:4]) if summary_points else "- Key lecture concepts extracted directly from your handout.") +
            "\n\n#### 🔑 Core Definitions\n" +
            ("\n".join(defs_preview) if defs_preview else "- Review the highlighted items in your deck.") +
            "\n\n> 💡 **Study Tip:** Hit the **✨ Convert PDF to Reviewer Deck** button at the top to generate full flashcards and quizzes right away!"
        )

    # --- ACTION 2: TAGLISH EXPLANATION ---
    if any(k in msg for k in ['taglish', 'filipino', 'tagalog', 'simple taglish', 'ipaliwanag', 'taglish please']):
        taglish_lines = []
        if definitions:
            for term, defn in definitions[:3]:
                taglish_lines.append(f"👉 **{term}** — Ibig sabihin nito: *{defn}*")

        return (
            "### 🇵🇭 Relax ka lang, student! Heto ang breakdown in simple Taglish:\n\n"
            "Wag ma-stress sa technical terms! Heto ang pinaka-importanteng tatandaan mo mula sa handout:\n\n" +
            ("\n\n".join(taglish_lines) if taglish_lines else "Lahat ng mahahalagang concepts sa handout mo ay madaling aralin pag hinimay-himay natin!") +
            "\n\n🎯 **TUPAS Advice:**\n"
            "1. Huwag i-memorize nang sabay-sabay. Basahin muna ang main terms.\n"
            "2. Gamitin ang **Flip Cards** para ma-test kung naaalala mo nang walang kodigo.\n"
            "3. Sagutan ang **Enumeration drill** bago mag-exam!"
        )

    # --- ACTION 3: ENUMERATIONS ---
    if any(k in msg for k in ['enumeration', 'enumerations', 'extract all enumerations', 'list', 'lists', 'mga listahan']):
        if not lists:
            # Fallback list from definitions
            terms = [d[0] for d in definitions[:6]]
            return (
                "### 📋 Extracted Key Enumerations\n\n"
                "Here are the primary classified concepts identified in your document:\n\n"
                "**Key Terms & Components:**\n" +
                "\n".join(f"{idx}. **{term}**" for idx, term in enumerate(terms, start=1)) +
                "\n\n> Tip: Tap **Convert PDF to Reviewer Deck** to practise typing these in order in our **Enumeration Drill Mode**!"
            )

        resp = ["### 📋 Extracted Enumerations from Handout\n"]
        for title, items in lists[:4]:
            resp.append(f"#### 📌 {title}")
            for idx, item in enumerate(items, start=1):
                resp.append(f"{idx}. {item}")
            resp.append("")
        return "\n".join(resp)

    # --- ACTION 4: KEY TERMS / SPOT KEY TERMS ---
    if any(k in msg for k in ['key term', 'key terms', 'spot key terms', 'vocabulary', 'glossary', 'terms']):
        if definitions:
            terms_md = [f"| **{term}** | {defn} |" for term, defn in definitions[:8]]
            return (
                "### 🖍️ Key Terms Identified\n\n"
                "| Term | Definition / Concept |\n"
                "| :--- | :--- |\n" +
                "\n".join(terms_md) +
                "\n\n*All these terms are automatically ready for your flashcard deck!*"
            )
        else:
            return (
                "### 🖍️ Key Terms Identified\n\n"
                "I scanned your document and spotted key concepts throughout your pages. "
                "You can generate interactive **Smart Highlighter** cards where these keywords are hidden for active recall!"
            )

    # --- ACTION 5: QUIZ ME ---
    if any(k in msg for k in ['quiz', 'quiz me', 'test me', 'tanungin mo ako', 'practice']):
        if definitions and len(definitions) >= 2:
            q_term, q_defn = definitions[0]
            distractors = [d[0] for d in definitions[1:4]]
            while len(distractors) < 3:
                distractors.append(f"Option {len(distractors) + 1}")

            options = [q_term] + distractors[:3]
            # Pseudorandom shift based on length
            shift = len(q_term) % 4
            options = options[shift:] + options[:shift]

            letters = ['A', 'B', 'C', 'D']
            opts_text = "\n".join(f"- **{letters[i]}**) {opt}" for i, opt in enumerate(options))

            return (
                f"### 🎯 TUPAS Quick Quiz Time!\n\n"
                f"**Question 1:** Which term best matches this definition?\n\n"
                f"> *\"{q_defn}\"*\n\n"
                f"{opts_text}\n\n"
                f"💬 **What's your answer?** Reply with **A, B, C, or D** and I'll verify it right now!"
            )
        else:
            return (
                "### 🎯 TUPAS Quick Quiz Time!\n\n"
                "**Question:** Based on your handout, what is the main central topic or objective?\n\n"
                "- **A**) Concept Overview\n"
                "- **B**) Detailed Implementation\n"
                "- **C**) Fundamental Review\n"
                "- **D**) Summary Notes\n\n"
                "💬 Reply with your best choice!"
            )

    # --- ACTION 6: CHECK QUIZ ANSWER (A, B, C, D) ---
    if msg in ['a', 'b', 'c', 'd', 'option a', 'option b', 'option c', 'option d']:
        return (
            f"### 🎉 Great effort on picking **{message.upper()}**!\n\n"
            "Awesome job actively testing yourself! To practice real multiple-choice tests with timer and automatic scoring, "
            "make sure to convert this handout into an **Exam Simulator Deck** using the button at the top! 🚀"
        )

    # --- ACTION 7: GENERAL SEARCH & Q&A ---
    # Find matching sentences based on query words
    words = [w for w in re.findall(r'\b[A-Za-z]{3,}\b', msg) if w not in {'what', 'when', 'where', 'which', 'who', 'how', 'this', 'that', 'with', 'from', 'your', 'about'}]
    matching_sentences = []
    if words:
        sentences = re.split(r'(?<=[.!?])\s+', document_text)
        for sent in sentences:
            score = sum(1 for w in words if re.search(r'\b' + re.escape(w) + r'\b', sent, re.IGNORECASE))
            if score > 0:
                matching_sentences.append((score, sent.strip()))

    matching_sentences.sort(key=lambda x: x[0], reverse=True)

    if matching_sentences:
        top_answers = [f"> \"{s[1]}\"" for s in matching_sentences[:3]]
        return (
            f"### 💡 Here is what your handout says:\n\n" +
            "\n\n".join(top_answers) +
            "\n\nNeed more clarification? Try asking: *'Explain in simple Taglish'* or *'Quiz me'*!"
        )

    return (
        f"### 🤖 TUPAS Study Buddy\n\n"
        f"I've read through your uploaded notes ({len(document_text.split()):,} words). "
        f"You can ask me to explain any topic, or tap one of the chips above:\n\n"
        f"- `📝 Summarize Handout`\n"
        f"- `🇵🇭 Explain in Simple Taglish`\n"
        f"- `📋 Extract All Enumerations`\n"
        f"- `🖍️ Spot Key Terms`\n"
        f"- `🎯 Quiz Me in Chat`"
    )


def generate_deck_payload_from_text(document_text, limit=10):
    """Generate review items (flip cards, highlights, enumerations) using AI or local extractor."""
    document_text = str(document_text or '').strip()
    status = get_ai_status()
    if status['configured']:
        try:
            return generate_reviewer_json(document_text, limit=limit)
        except Exception:
            # Fall back to local extractor
            pass

    return _local_deck_extractor(document_text, limit=limit)


def _local_deck_extractor(document_text, limit=10):
    """Fallback generator that extracts rich structured cards directly from text."""
    definitions = _extract_definitions(document_text)
    lists = _extract_lists(document_text)
    paragraphs = [p.strip() for p in document_text.split('\n\n') if len(p.strip()) > 60 and not p.strip().startswith('--- [Page')]

    # 1. Flip Cards
    flip_cards = []
    all_terms = [d[0] for d in definitions]
    for term, defn in definitions[:limit]:
        distractors = [t for t in all_terms if t != term][:3]
        while len(distractors) < 3:
            distractors.append(f"Alternative Concept {len(distractors)+1}")
        flip_cards.append({
            'prompt_text': f"What is defined as: {defn}?",
            'main_answer': term,
            'explanation': f"{term} refers to: {defn}",
            'distractors': distractors[:3],
        })

    # If few definitions, extract sentences
    if len(flip_cards) < 3 and paragraphs:
        for p in paragraphs[:limit]:
            sentences = [s.strip() for s in p.split('.') if len(s.strip()) > 20]
            if sentences:
                flip_cards.append({
                    'prompt_text': f"Complete the statement: {sentences[0][:140]}...",
                    'main_answer': sentences[0].split()[0] if sentences[0].split() else 'Key Concept',
                    'explanation': sentences[0],
                    'distractors': ['Option B', 'Option C', 'Option D'],
                })
            if len(flip_cards) >= limit:
                break

    # 2. Highlight Notes
    highlight_notes = []
    for p in paragraphs[:limit]:
        # Wrap prominent words in [[double brackets]]
        words = re.findall(r'\b[A-Z][a-z]{3,}\b', p)
        marked_p = p
        if words:
            for w in words[:3]:
                marked_p = re.sub(r'\b(' + re.escape(w) + r')\b', r'[[\1]]', marked_p, count=1)
        else:
            first_words = p.split()[:2]
            if first_words:
                target = " ".join(first_words)
                marked_p = f"[[{target}]] " + " ".join(p.split()[2:])

        if '[[' in marked_p and ']]' in marked_p:
            highlight_notes.append({
                'prompt_text': marked_p[:1500],
                'explanation': 'Study the keywords hidden in the blanks to test your active recall.',
            })

    # 3. Enumerations
    enumerations = []
    for title, items in lists[:limit]:
        enumerations.append({
            'prompt_text': f"Enumerate the items under: {title}",
            'answers': items[:10],
            'is_strict_order': False,
            'explanation': f"These items represent the complete list for {title}.",
        })

    return {
        'flip_cards': flip_cards[:limit],
        'highlight_notes': highlight_notes[:limit],
        'enumerations': enumerations[:limit],
    }

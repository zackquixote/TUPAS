import re

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()

KEYWORD_RE = re.compile(r'\[\[(.+?)\]\]')


def _escaped_with_breaks(value):
    return escape(str(value)).replace('\n', '<br>')


@register.filter
def highlight_prompt(value):
    """Render [[keywords]] as safe <mark> elements for Smart Highlighter mode."""
    if not value:
        return ''

    value = str(value)
    parts = []
    last_index = 0

    for match in KEYWORD_RE.finditer(value):
        if match.start() > last_index:
            parts.append(_escaped_with_breaks(value[last_index:match.start()]))

        keyword = match.group(1).strip()
        if keyword:
            escaped_keyword = escape(keyword)
            parts.append(
                '<mark class="tupas-highlight" data-answer="{answer}" '
                'tabindex="0" role="button" aria-label="Reveal hidden keyword">'
                '{label}</mark>'.format(
                    answer=escaped_keyword,
                    label=escaped_keyword,
                )
            )

        last_index = match.end()

    if last_index < len(value):
        parts.append(_escaped_with_breaks(value[last_index:]))

    return mark_safe(''.join(parts))


@register.filter
def cram_note(value):
    """Render [[keywords]] as bold, underlined terms for the printable cram sheet."""
    if not value:
        return ''

    text = escape(str(value))
    text = KEYWORD_RE.sub(
        lambda match: '<strong><u>{}</u></strong>'.format(match.group(1).strip()),
        text,
    )
    return mark_safe(text.replace('\n', '<br>'))


@register.filter
def strip_highlights(value):
    """Remove [[ ]] markers while keeping the keyword text, for exam prompts."""
    if not value:
        return ''
    text = KEYWORD_RE.sub(lambda match: match.group(1).strip(), str(value))
    return mark_safe(_escaped_with_breaks(text))

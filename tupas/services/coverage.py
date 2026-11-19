"""Report how many requested items the AI actually produced.

TUPAS never invents facts to fill a quota, so the honest signal is a shortfall
report rather than pretending a handout was fully covered.
"""

COVERAGE_LABELS = {
    'flip_cards': 'flip cards',
    'highlight_notes': 'highlight notes',
    'enumerations': 'enumeration drills',
}


def coverage_shortfalls(counts, requested):
    """Return human-readable shortfalls for each item type, if any."""
    requested = max(0, int(requested or 0))
    return [
        f"{counts.get(key, 0)} of {requested} {label}"
        for key, label in COVERAGE_LABELS.items()
        if counts.get(key, 0) < requested
    ]


def coverage_warning(shortfalls):
    return (
        'Coverage check: TUPAS returned ' + ', '.join(shortfalls) + '. '
        'It never pads with invented facts, so the handout may not contain that '
        'many distinct items, or the AI response was cut short. Open the deck, '
        'compare it with your handout, and add anything missing manually.'
    )

"""razzle.formats — the presentation-format vocabulary (razzle's analogue of a venue).

Fixed, not per-project: a deck is authored for one of these formats, and the format's minutes drive
the slide budget at 1 slide/minute. WHICH format(s) a project's deck targets is the per-project
choice (the venue-analogue selection), settled where the deck stage opens — this module only defines
what the formats ARE.
"""

from __future__ import annotations

# format name → talk length in minutes (None = not a timed talk).
FORMATS: dict[str, int | None] = {
    "longtalk": 18,
    "shorttalk": 11,
    "lecture": 45,
    "poster": None,      # one board, not a slide count — poster mode is a separate shape (TODO)
}

SLIDES_PER_MINUTE = 1.0


def minutes(fmt: str) -> int | None:
    return FORMATS.get(fmt)


def slide_budget(fmt: str) -> int | None:
    """The number of slides to aim for, from the format's minutes at SLIDES_PER_MINUTE. None for a
    poster (or an unknown format) — the caller decides the fallback."""
    m = FORMATS.get(fmt)
    return None if m is None else max(1, round(m * SLIDES_PER_MINUTE))

"""Shared text segmentation for the redline engine and the guards.

`sentence_units` is the ONE sentence splitter: the redline's sentencewise differ,
the minimal-edit guard, and the comment-span anchoring all measure in its units,
so they must agree on where a sentence ends.
"""

from __future__ import annotations

import re


def sentence_units(text: str) -> list[str]:
    """Split into sentence units, each carrying its trailing whitespace, so that
    ``"".join(sentence_units(t)) == t``.

    Losslessness is the point: it lets a diff preserve an unchanged sentence byte-for-byte,
    so its [@citekey] tags and its equations survive a revision untouched.
    """
    if not text:
        return []
    toks = re.split(r"(?<=[.!?])(\s+)", text)
    units: list[str] = []
    i = 0
    while i < len(toks):
        unit = toks[i]
        if i + 1 < len(toks):  # the captured whitespace separator after this sentence
            unit += toks[i + 1]
        if unit:
            units.append(unit)
        i += 2
    return units

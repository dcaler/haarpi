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


def copyedit_notes(original: str, suggestion: str, *,
                   max_words: int = 6, max_churn: float = 0.34) -> list[tuple[str, str]]:
    """A correction, said as a correction — ``[(the offending words, what to say)]``.

    A suggested fix used to arrive as the author's whole sentence, restated, with the change
    buried inside it: fifty-five words to report a missing "t", twenty-five to report an
    apostrophe. The author read it as the tool vomiting their own text back at them, which is
    exactly what it was. A proposal must POINT, not restate.

    Word-level, so each change is its own note and can be anchored on the words it concerns.
    An insertion has no offending words to anchor on, so it hangs off the word that follows
    it (or, failing that, the one before).

    Two limits, because a proofreader that has started rewriting must be stopped rather than
    reported. ``max_churn``: if more of the sentence changed than this, the "correction" is a
    rewrite and nothing is offered at all — the author's prose is not the tool's to redraft,
    and a diff between a sentence and its replacement is a blob, not a note. ``max_words``: a
    single note longer than this is an edit, not a typo, and gets dropped on its own.
    """
    import difflib

    a, b = original.split(), suggestion.split()
    if not a:
        return []
    ops = difflib.SequenceMatcher(None, a, b).get_opcodes()
    churn = sum(max(i2 - i1, j2 - j1) for tag, i1, i2, j1, j2 in ops if tag != "equal")
    if churn > max_churn * len(a):
        return []
    out: list[tuple[str, str]] = []
    for tag, i1, i2, j1, j2 in ops:
        old, new = " ".join(a[i1:i2]), " ".join(b[j1:j2])
        if tag == "equal" or max(i2 - i1, j2 - j1) > max_words:
            continue
        if tag == "replace":
            out.append((old, new))
        elif tag == "delete":
            out.append((old, "(cut)"))
        elif tag == "insert":
            after = " ".join(a[i2:i2 + 2])          # anchor on what follows the gap
            before = " ".join(a[max(0, i1 - 2):i1])
            anchor = after or before
            if anchor:
                out.append((anchor, f'insert: "{new}"'))
    return out

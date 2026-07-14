"""The reviser may read the author's sentences. It may not write them.

An authored span reaches the model as ⟦a:N⟧ with a legend. The sentinel guards — already
written, already enforcing this for equations — do the rest: an edit that loses the span,
or quietly retypes it as its own prose, never reaches the document.
"""

from __future__ import annotations

import json

from raconteur import guards
from raconteur import redline_revise as rr

from test_revise_adversary import ScriptedBrain, _call

AUTHORED = {"⟦a:1⟧": "The author typed this sentence by hand."}
PARA = "Grounded claim [@smith2020]. ⟦a:1⟧ Third stays."


def _edits(obj):
    return json.dumps(obj)


def test_dropping_the_authors_span_fails_closed():
    """The model rewrites the paragraph and loses the author's sentence. Nothing is written."""
    brain = ScriptedBrain(
        _edits({"1": "Rewritten claim [@smith2020].", "2": "My own version of their point."}),
        _edits({"1": "Rewritten claim [@smith2020].", "2": "Still my own version."}),
    )
    text, outcome = _call(brain, PARA, anchored={0}, authored=AUTHORED)
    assert text is None and outcome == "skipped"


def test_the_guard_names_the_offence():
    findings = guards.dropped_sentinels(PARA, "Rewritten claim [@smith2020]. Mine now.")
    assert findings, "losing an authored span must be a finding"


def test_rewriting_the_tool_prose_around_the_span_is_allowed():
    """The paragraph is not frozen — only the author's sentence is."""
    brain = ScriptedBrain(
        _edits({"1": "A tighter grounded claim [@smith2020]."}),
        "OK",
    )
    text, outcome = _call(brain, PARA, anchored={0}, authored=AUTHORED)
    assert outcome == "edited"
    assert "⟦a:1⟧" in text, "the author's span survives, in place"
    assert "A tighter grounded claim" in text


def test_a_copyedit_is_collected_and_never_applied():
    """A typo in the author's text is a remark, not an edit."""
    proposed: dict[str, str] = {}
    brain = ScriptedBrain(
        _edits({"1": "A tighter grounded claim [@smith2020].",
                "copyedits": {"a:1": "The author typed this sentence by hand, correctly."}}),
        "OK",
    )
    text, outcome = _call(brain, PARA, anchored={0}, authored=AUTHORED,
                          copyedits=proposed)
    assert outcome == "edited"
    assert proposed == {"⟦a:1⟧": "The author typed this sentence by hand, correctly."}
    # the suggestion is NOT in the text that gets written
    assert "correctly" not in text
    assert "⟦a:1⟧" in text


def test_a_copyedit_naming_a_span_that_does_not_exist_is_dropped():
    edits, copyedits, errors = rr._parse_sentence_edits(
        _edits({"1": "x", "copyedits": {"a:9": "invented"}}), 3, AUTHORED)
    assert copyedits == {} and not errors


# ── the guard must not reject a draft for obeying its instructions ───────────

def test_a_draft_carrying_the_authors_placeholder_is_not_an_invention():
    """The bug that killed three of five beats in the 2026-07-14 re-cut.

    The generation guard compared the draft against the beat's ACCEPTED PROSE — where the
    author's sentence is spelled out in words and no placeholder exists — so every ⟦a:1⟧ the
    draft had been ORDERED to carry read as one it had invented. Rejected, retried, rejected,
    beat left untouched. And the write path, comparing against the serialized paragraph,
    went on to report "0 refused": the feature silently disabled itself on precisely the
    beats it exists for.
    """
    from raconteur import onepager as op

    spans = {"⟦a:1⟧": "The author typed this by hand."}
    draft = "Fresh tool prose. ⟦a:1⟧ And more tool prose."

    accepted_prose = "Old tool prose. The author typed this by hand."
    assert op._beat_problems(draft, spans, accepted_prose, set()), \
        "this is the bug, pinned: against the prose, obedience looks like invention"

    serialized = "Old tool prose. ⟦a:1⟧"
    assert op._beat_problems(draft, spans, serialized, set()) == [], \
        "against the serialized paragraph, the obedient draft passes"


def test_a_draft_that_drops_the_authors_span_is_still_refused():
    from raconteur import onepager as op

    spans = {"⟦a:1⟧": "The author typed this by hand."}
    problems = op._beat_problems("I rewrote it my way.", spans, "Old prose. ⟦a:1⟧", set())
    assert problems and "missing" in problems[0]


def test_a_draft_that_invents_a_placeholder_is_still_refused():
    from raconteur import onepager as op

    spans = {"⟦a:1⟧": "The author typed this by hand."}
    problems = op._beat_problems("Prose. ⟦a:1⟧ More. ⟦a:7⟧", spans, "Old. ⟦a:1⟧", set())
    assert problems and "not a real placeholder" in problems[0]


def test_a_draft_that_drops_a_citation_is_still_refused():
    from raconteur import onepager as op

    problems = op._beat_problems("Rewritten with no source.", {},
                                 "Old prose [@setzler2022].", {"setzler2022"})
    assert problems and "setzler2022" in problems[0]

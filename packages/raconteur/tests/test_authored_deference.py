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

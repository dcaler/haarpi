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


# ── the span kept AND retyped: every sentinel guard passes, the beat is ruined ─

ECHOED = {"⟦a:1⟧": "However, by re-expressing its visual output in audio we can uncover "
                   "new applications of generative segregation."}


def test_keeping_the_span_and_retyping_it_is_caught():
    """The 2026-07-14 21:41 re-cut: the author read his own sentences twice, back to back.

    The draft keeps ⟦a:1⟧ exactly once — dropped, duplicated and invented all pass — and
    ALSO writes out what it contains, in the prose beside it. Expanded, the sentence prints
    twice. Nothing in the sentinel guards can see this: they count placeholders.
    """
    draft = ("However, by re-expressing its visual output in audio we can uncover new "
             "applications of generative segregation. ⟦a:1⟧ And so we sonify it.")
    assert guards.dropped_sentinels("Old. ⟦a:1⟧", draft) == []
    assert guards.invented_sentinels("Old. ⟦a:1⟧", draft) == []
    findings = guards.echoed_spans(draft, ECHOED)
    assert findings, "the author's own sentence, retyped beside itself, must be caught"
    assert "retyped" in findings[0].imperative


def test_the_echo_guard_survives_repunctuation_and_case():
    """It is the WORDS that were stolen. A capital and a comma do not launder them."""
    draft = "however by RE-EXPRESSING its visual output in audio — we can uncover new. ⟦a:1⟧"
    assert guards.echoed_spans(draft, ECHOED)


def test_writing_around_the_span_is_not_an_echo():
    """The whole point of span deference: the prose beside the sentence stays fair game."""
    draft = ("Segregation is shown visually and almost never heard. ⟦a:1⟧ We then aggregate "
             "the audio over many runs.")
    assert guards.echoed_spans(draft, ECHOED) == []


def test_sharing_the_topics_vocabulary_is_not_an_echo():
    """Author and tool write about the same paper in the same words. That is the topic
    talking, not the pen — and a guard that cannot tell the difference is unusable."""
    draft = "Generative segregation in audio is our subject. ⟦a:1⟧ The output is sonic."
    assert guards.echoed_spans(draft, ECHOED) == []


def test_a_short_span_is_never_echo_checked():
    """Below five words a repeat proves nothing, and a false refusal costs a whole beat."""
    assert guards.echoed_spans("The model settles here. ⟦a:1⟧", {"⟦a:1⟧": "in C-minor"}) == []


def test_the_echo_is_refused_at_the_beat():
    from raconteur import onepager as op

    draft = ("However, by re-expressing its visual output in audio we can uncover new "
             "applications of generative segregation. ⟦a:1⟧")
    problems = op._beat_problems(draft, ECHOED, "Old prose. ⟦a:1⟧", set())
    assert problems and any("retyped" in p for p in problems)


# ── the copyedit pass: every beat has its own ⟦a:1⟧ ──────────────────────────

class _Echo:
    """A brain that returns whatever it was primed with."""

    def __init__(self, payload):
        self.payload = payload
        self.seen = ""

    def coordinator(self, prompt, **_):
        self.seen = prompt
        return self.payload


def test_the_copyedit_legend_does_not_collide_across_beats():
    """Sentinels are numbered per PARAGRAPH: Motivation, Gap and Key result(s) each have an
    ⟦a:1⟧. Flattened into one legend they collapsed, the last beat silently won, and the
    correction to Motivation's typo was diffed against the Beethoven span from Key
    result(s) — two unrelated sentences — and anchored, as a blob, on the wrong paragraph.
    That is comments 50, 51 and 52 of the delivered one-pager.
    """
    from raconteur import onepager as op

    authored = {
        "Motivation": {"⟦a:1⟧": "The Schelling model has dates to 1971 and predates CSS."},
        "Key result(s)": {"⟦a:1⟧": " – the opening bars of Beethoven's 5th in C-minor –"},
    }
    brain = _Echo(json.dumps({"S1": "The Schelling model dates to 1971 and predates CSS."}))
    notes = op._copyedit_notes(brain, authored)

    assert "S1" in brain.seen and "S2" in brain.seen, "each span needs a label of its own"
    assert notes == [("has", "(cut)")], notes    # "has dates" → "dates": one stray word
    assert not any("Beethoven" in old for old, _ in notes), \
        "the correction must not land on another beat's span"


def test_a_copyedit_that_rewrites_rather_than_corrects_is_dropped():
    """A proofreader that has started editing is stopped, not reported. His prose is his."""
    from raconteur import onepager as op

    authored = {"Gap": {"⟦a:1⟧": "Communicating segregation outcomes is dominated by "
                                 "visual media."}}
    brain = _Echo(json.dumps({"S1": "Visual media overwhelmingly dominate how the outcomes "
                                    "of segregation are communicated to audiences."}))
    assert op._copyedit_notes(brain, authored) == []


def test_a_real_typo_still_gets_through():
    from raconteur import onepager as op

    authored = {"Motivation": {"⟦a:1⟧": "This can encourage new though patterns and engage "
                                        "new audiences."}}
    brain = _Echo(json.dumps({"S1": "This can encourage new thought patterns and engage "
                                    "new audiences."}))
    assert op._copyedit_notes(brain, authored) == [("though", "thought")]


# ── a figure shortfall is SOFT: it retries the beat, it must never abandon it ─

def test_a_missing_figure_is_not_an_integrity_breach():
    """The 2026-07-14 skip cascade, pinned. The Key result(s) beat failed the figure guard,
    was rejected WHOLE, and the three 'define this' asks that lived in it died with it. A
    figure the model could not caption is a quality shortfall, not a hole where the author's
    words were — so it stays out of the fatal gate that decides whether to abandon a beat.
    """
    from raconteur import onepager as op

    draft = "The landscape settles at moderate tolerance."     # answers fine, writes 0 figures
    assert op._beat_integrity_problems(draft, {}, "Old prose.", set()) == [], \
        "no citation, span, or echo broken — nothing here may abandon the beat"
    figs = op._beat_figure_problems(draft, expect_figures=2)
    assert figs, "the two missing figures ARE caught — as a soft problem"
    # the combined view still carries both, for callers that want the whole list
    assert op._beat_problems(draft, {}, "Old prose.", set(), expect_figures=2) == figs


def test_a_dropped_citation_is_still_a_fatal_integrity_breach():
    from raconteur import onepager as op

    problems = op._beat_integrity_problems("Rewritten with no source.", {},
                                           "Old prose [@setzler2022].", {"setzler2022"})
    assert problems and "setzler2022" in problems[0]


def test_retyping_an_authored_span_is_still_a_fatal_integrity_breach():
    from raconteur import onepager as op

    draft = ("However, by re-expressing its visual output in audio we can uncover new "
             "applications of generative segregation. ⟦a:1⟧")
    problems = op._beat_integrity_problems(draft, ECHOED, "Old prose. ⟦a:1⟧", set())
    assert problems and any("retyped" in p for p in problems)

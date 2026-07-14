"""What the re-cut is told, and what it must never be told.

A resolved comment is history, not an instruction. Re-fired as one, a settled
"this is actually pretty coherent" caused the Approach beat to be rewritten whole —
losing a citation on the way — to add a phrase that was already in the paragraph.
"""

from __future__ import annotations

import pytest
from haarpi.testing import write_commented_docx

from raconteur import onepager as op

HUMAN = "D. Cale Reeves"

_BEATS = [
    "A New Title — one-pager",
    "Motivation — Cities sort, and the stakes are high.",
    "Gap — The intersection remains underexamined.",
    "Approach — We implement an agent-based simulation of chord agents.",
]


@pytest.fixture()
def annotated(tmp_path):
    return write_commented_docx(tmp_path / "rev.docx", _BEATS, [
        # settled: the reviewer already accepted this beat
        {"cid": "1", "author": HUMAN, "text": "this is coherent, leave it", "anchor": 3,
         "done": True},
        # live: an open ask on the Gap beat
        {"cid": "2", "author": HUMAN, "text": "define 'underexamined'", "anchor": 2},
        # the tool answered it once and the reviewer left it open
        {"cid": "3", "author": "raconteur", "text": "We defined it inline.", "parent": "2"},
        # ...and said more, in the thread, as the protocol asks
        {"cid": "4", "author": HUMAN, "text": "no, define it in the text", "parent": "2"},
    ])


def test_a_resolved_comment_never_reaches_a_beat(annotated):
    _, notes, general = op._annotation_brief(annotated)
    blob = "\n".join(notes.values()) + general
    assert "leave it" not in blob
    assert "Approach" not in notes, "a beat whose only comment is settled is not briefed"


def test_an_open_ask_reaches_its_beat(annotated):
    _, notes, _ = op._annotation_brief(annotated)
    assert "Gap" in notes and "define 'underexamined'" in notes["Gap"]


def test_the_reviewers_thread_reply_travels_with_the_ask(annotated):
    _, notes, _ = op._annotation_brief(annotated)
    assert "no, define it in the text" in notes["Gap"]


def test_an_ask_the_tool_already_failed_is_marked_as_such(annotated):
    """Not a fresh task — a task the tool got wrong. The reviewer should not have to
    type 'this STILL needs a definition' for the tool to notice."""
    _, notes, _ = op._annotation_brief(annotated)
    assert "ALREADY ANSWERED THIS ONCE" in notes["Gap"]
    assert "We defined it inline." in notes["Gap"]


def test_a_first_ask_is_not_dressed_up_as_a_failure(tmp_path):
    doc = write_commented_docx(tmp_path / "first.docx", _BEATS, [
        {"cid": "1", "author": HUMAN, "text": "define 'underexamined'", "anchor": 2},
    ])
    _, notes, _ = op._annotation_brief(doc)
    assert "ALREADY ANSWERED" not in notes["Gap"]


# ── an ask without its anchor is a riddle ────────────────────────────────────

@pytest.fixture()
def pinned(tmp_path):
    """Three words, pinned to two. Without the pin they mean nothing at all."""
    return write_commented_docx(tmp_path / "pinned.docx", _BEATS, [
        {"cid": "1", "author": HUMAN, "text": "define this", "anchor": 2,
         "on": "underexamined"},
    ])


def test_the_ask_carries_the_words_it_was_pinned_to(pinned):
    """The reviewer wrote "define this" three times on the 2026-07-14 one-pager and got no
    definitions, because the brief said only "define this". The tool was never told what
    "this" was — and neither, later, was the adversary asked to check the answer, which is
    how it came to complain that the text "fails to define the term Key result(s)". It was
    reading the heading, for want of anything else."""
    assert op.anchor_words(pinned) == {"1": "underexamined"}

    _, notes, _ = op._annotation_brief(pinned)
    assert "underexamined" in notes["Gap"], "the brief must say what 'this' is"
    assert "define this" in notes["Gap"]


def test_a_whole_paragraph_anchor_is_not_quoted_back_as_an_anchor(annotated):
    """A comment covering the whole paragraph points at nothing. Quoting it back into a brief
    that already carries the paragraph is the tool reciting the author's text at him — the
    same defect as the copyedit blobs, arriving from the other direction."""
    assert op.anchor_words(annotated) == {}
    _, notes, _ = op._annotation_brief(annotated)
    assert notes["Gap"].startswith("- define 'underexamined'"), notes["Gap"]


# ── the reply says the verdict, either way ───────────────────────────────────

class _Verdict:
    def __init__(self, line):
        self.line = line
        self.seen = ""

    def coordinator(self, prompt, **_):
        self.seen = prompt
        return self.line


ASKS = {"7": {"id": "7", "text": "define this"}}
BEAT_OF = {"7": "Gap"}
BEFORE = {"Gap": "The intersection remains underexamined."}
AFTER = {"Gap": "Tonal negotiation — chords trading places under a tolerance rule — "
                "is underexamined."}
OUT = {"Gap": "rewritten"}


def test_a_satisfied_verdict_actually_says_it_was_satisfied():
    """It never did. `SATISFIED:` was stripped and the quotation behind it shipped alone, so
    the one ask the tool believed it had met reached the reviewer as a bare fragment of his
    own document — comment 48, which read as the tool saying nothing at all."""
    brain = _Verdict('SATISFIED: "Tonal negotiation — chords trading places under a '
                     'tolerance rule"')
    replies = op._verify_replies(brain, ASKS, BEAT_OF, BEFORE, AFTER, OUT)
    assert replies["7"].startswith("Addressed — ")
    assert "Tonal negotiation" in replies["7"]


def test_an_unsatisfied_verdict_says_what_is_missing():
    brain = _Verdict("NOT SATISFIED: the term is used again but never defined")
    replies = op._verify_replies(brain, ASKS, BEAT_OF, BEFORE, AFTER, OUT)
    assert replies["7"] == "NOT addressed — the term is used again but never defined"


def test_the_adversary_is_told_what_the_ask_was_pinned_to():
    brain = _Verdict("NOT SATISFIED: still undefined")
    op._verify_replies(brain, ASKS, BEAT_OF, BEFORE, AFTER, OUT,
                       on={"7": "underexamined"})
    assert "underexamined" in brain.seen


def test_the_adversary_is_told_when_the_anchored_text_was_struck():
    """Deleting the sentence someone asked you to explain is not an explanation."""
    brain = _Verdict("NOT SATISFIED: deleted, not defined")
    op._verify_replies(brain, ASKS, BEAT_OF, BEFORE, AFTER, OUT,
                       on={"7": "underexamined"}, deleted={"7"})
    assert "DELETED the text the comment was anchored to" in brain.seen

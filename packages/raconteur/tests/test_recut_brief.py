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

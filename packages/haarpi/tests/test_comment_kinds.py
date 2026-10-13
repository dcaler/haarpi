"""One vocabulary, read two ways: the classifier prompt and the reviewer's guide.

The deliverable tells the reviewer which phrasings to use; the classifier is prompted with the
phrasings it should recognise. Written separately they drift, and a reviewer following the
guide gets a comment sorted into the wrong rework. Both are generated from comment_kinds.
"""

from __future__ import annotations

import hashlib

import pytest

from haarpi import comment_kinds as ck
from haarpi import planner


def _flat(s: str) -> str:
    return " ".join(s.lower().split())


def test_the_narrative_prompt_is_unchanged_by_being_generated():
    """The narrative classifier prompt was hand-written; generating it from the table must not
    change a byte, or every narrative review's sorting changes with this refactor. If you edit
    a narrative rule on purpose, update the hash."""
    prompt = ck.decompose_prompt(ck.LITREVIEW_NARRATIVE)
    assert hashlib.sha1(prompt.encode()).hexdigest() == "4ad3af278e80787637e5eb3a6dcee8fb0b69fea4"
    assert planner._DECOMPOSE_PROMPT == prompt


@pytest.mark.parametrize("vocab", [ck.LITREVIEW_NARRATIVE, ck.LITREVIEW_FACTS],
                         ids=["narrative", "facts"])
def test_every_phrasing_the_guide_offers_is_one_the_classifier_is_told(vocab):
    for k in vocab.kinds:
        for t in k.triggers:
            core = _flat(t.replace("…", "").strip().rstrip("."))
            assert core in _flat(k.rule), f"{k.name}: the guide offers {t!r}, the prompt never says it"


def test_the_guide_lists_every_kind_with_what_it_costs():
    rows = ck.phrasing_table(ck.LITREVIEW_FACTS)
    body = "\n".join(rows)
    for name in ck.LITREVIEW_FACTS.names:
        assert f"**{name}**" in body
    assert "about 2.5 h" in body                       # the topic re-score, before it is asked for


def test_the_release_guide_is_haarpis_frame_around_the_agents_checklist():
    out = "\n".join(ck.release_guide(ck.LITREVIEW_FACTS, ["Check A.", "Check B."],
                                     "A holds.", "typos"))
    assert out.startswith("## Before you release this document")
    assert "1. Check A.\n2. Check B." in out and "**Release when** A holds." in out
    assert ck.HOW_TO_RELEASE in out and "*Not reasons to hold the release:* typos" in out
    assert "| **fact** |" in out


def test_a_section_ask_on_a_fact_base_becomes_a_topic():
    """The deterministic section floor still catches "a section on X" whatever the model said;
    on a fact base the same ask is a new topic."""
    tasks = planner._normalise_tasks([{"comments": [1], "need": "edit"}],
                                     ["we need a section on archival bias"], fact_base=True)
    assert [t["need"] for t in tasks] == ["topic"] and tasks[0]["serves"] == []


def test_the_fact_kinds_are_not_second_guessed_by_the_cite_floor():
    tasks = planner._normalise_tasks(
        [{"comments": [1], "need": "pdf"}, {"comments": [2], "need": "topic_scope", "topic": "T7",
                                             "query": "gaps in surviving records only"}],
        ["I've fixed the PDF, it's now in Zotero", "T7 is too broad"], fact_base=True)
    assert [t["need"] for t in tasks] == ["pdf", "topic_scope"] and tasks[1]["topic"] == 7

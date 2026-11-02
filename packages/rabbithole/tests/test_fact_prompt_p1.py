"""P1: the question-aware, claim-first extraction prompt under trial in the extraction bake-off.

FirmPathways' P0 facts came back as definitions, context-free details and quotes copied back
(Aleti 2017's "Given the current formulation, the use of crossover has no benefits…", 2026-11-02).
P1 tells the model what the review asks, has it state the claim before finding the passage, and
makes each fact say what it is about, how it was shown and when it holds.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from rabbithole import facts
from rabbithole.models import Candidate

_CFG = dict(topic="", focus="", questions=["How do cleantech firms navigate public funding?"],
            topics=["Sequencing of funding sources", {"name": "The NK model", "covers": "K"}])


def _cfg(**kw):
    return SimpleNamespace(**{**_CFG, "fact_prompt": "p1", **kw})


def test_p1_tells_the_model_the_question_and_the_numbered_topics():
    sys = facts.p1_system(_cfg())
    assert "Q1. How do cleantech firms" in sys and "2. The NK model — K" in sys
    assert "Never copy the quote as the claim" in sys and '"nothing"' in sys


def test_p1_reads_claims_with_their_context_and_keeps_topic_numbers_in_range():
    raw = json.dumps([{"claim": "In SBSE test generation, crossover adds nothing.", "domain": "SBSE",
                       "method": "landscape analysis", "conditions": "", "bears_on": [2, 9, "x"],
                       "role": "theme", "kind": "finding", "page": 3, "quote": "q"}])
    fs, nothing = facts._parse_facts_p1(raw, "aleti2017", 2)
    assert nothing == "" and fs[0].domain == "SBSE" and fs[0].bears_on == [2]
    assert fs[0].text.startswith("In SBSE")


def test_p1_may_answer_nothing_with_a_reason():
    fs, nothing = facts._parse_facts_p1('[{"nothing": "A protein-folding paper."}]', "k", 2)
    assert fs == [] and nothing == "A protein-folding paper."


def test_a_p1_nothing_is_cached_as_final_and_apart_from_p0(tmp_path, monkeypatch):
    monkeypatch.setattr(facts, "_MIN_TEXT_CHARS", 0)
    calls = []

    class B:
        def coordinator(self, prompt, system="", **kw):
            calls.append(kw.get("think"))
            return '[{"nothing": "off the question"}]'
    corpus = [Candidate(title="T", year=2020, fulltext="[p.1]\nSome text.")]
    paths = SimpleNamespace(work=tmp_path)
    assert facts.extract(B(), corpus, _cfg(fact_think=True), paths, None, {0: "k2020"}) == {"k2020": []}
    assert calls == [True]                                   # no retry, thinking as asked
    cached = list((tmp_path / "facts").glob("k2020.p1-*.json"))
    assert len(cached) == 1 and json.loads(cached[0].read_text())["nothing"]
    facts.extract(B(), corpus, _cfg(), paths, None, {0: "k2020"})
    assert len(calls) == 1                                   # read from the cache
    facts.extract(B(), corpus, _cfg(topics=["Other"]), paths, None, {0: "k2020"})
    assert len(calls) == 2                                   # new topics: P1 re-extracts


def test_p0_is_untouched_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(facts, "_MIN_TEXT_CHARS", 0)
    seen = []

    class B:
        def coordinator(self, prompt, system="", **kw):
            seen.append((system, kw.get("think")))
            return "[]"
    facts.extract(B(), [Candidate(title="T", year=2020, fulltext="[p.1]\nx")],
                  SimpleNamespace(topic="t", focus="a"), SimpleNamespace(work=tmp_path), None,
                  {0: "k"})
    assert "never return an empty array" in seen[0][0] and seen[0][1] is False

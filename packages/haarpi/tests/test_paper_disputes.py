"""A reviewer disputes a claim in the paper; the claim is traced to its fact, and the error is
put where it belongs — the paper's sentence, or the fact itself (the litreview re-reads it).

Only `haarpi next` may reach across a stage boundary, so the cross-stage route lives here.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from haarpi import comment_kinds as ck
from haarpi import planner, project
from haarpi.testing import write_commented_docx

HUMAN = "D. Cale Reeves"


class _Brain:
    def __init__(self, reply):
        self.reply, self.calls = reply, 0

    def __call__(self, *a, **k):
        return self

    def coordinator(self, *a, **k):
        self.calls += 1
        return self.reply


def _snapshot(root):
    lit = root / "litReview"
    (lit / "output").mkdir(parents=True)
    (lit / "work" / "facts" / "scores").mkdir(parents=True)
    (lit / "litrev.yaml").write_text("project_name: P\ntopic: t\nreport_format: facts\n")
    blob = {"topics": [{"name": "Validation", "serves": [1]}],
            "facts": [{"id": "grimm2006#1", "citekey": "grimm2006", "verified": True,
                       "page": 3, "scores": [3], "quote": "twelve patterns at three scales",
                       "text": "Pattern-oriented validation compared 12 patterns across 3 "
                               "scales of the model."}],
            "themes": [{"item": 1, "name": "Patterns", "fact_ids": ["grimm2006#1"]}],
            "sources": {"grimm2006": {"zotero_key": "G1", "label": "Grimm et al., 2006"}}}
    (lit / "output" / "P_litreview.facts.json").write_text(json.dumps(blob))
    (lit / "work" / "facts.json").write_text(json.dumps(blob))
    for d in ("", "scores"):
        (lit / "work" / "facts" / d / "grimm2006.json").write_text("{}")
    return lit


def _manuscript(root, sentence, comment):
    paper = root / "paper"
    paper.mkdir()
    return write_commented_docx(
        paper / "261020_P_ra_DCR.docx", ["A Title", sentence],
        [{"cid": "1", "author": HUMAN, "text": comment, "anchor": 1}])


_M = SimpleNamespace(stages={"litreview": {"dir": "litReview"}})


def _disputes(tmp_path, monkeypatch, sentence, comment, reply):
    _snapshot(tmp_path)
    markup = _manuscript(tmp_path, sentence, comment)
    brain = _Brain(reply)
    monkeypatch.setattr("haarpi.brain.Brain", brain)
    check = {"unresolved": [{"id": "1", "author": HUMAN, "text": comment}]}
    return planner._paper_disputes(tmp_path, _M, markup, check, {}), markup, brain


def test_a_changed_number_is_the_papers_error_without_asking(tmp_path, monkeypatch):
    got, _, brain = _disputes(
        tmp_path, monkeypatch,
        "Pattern-oriented validation compared 15 patterns across 3 scales [@grimm2006].",
        "wrong number", '{"dispute": true, "where": "source"}')
    assert [(d["where"], d["fact_id"]) for d in got] == [("paper", "grimm2006#1")]
    assert brain.calls == 0


def test_a_faithful_sentence_puts_the_fact_in_question(tmp_path, monkeypatch):
    got, _, _ = _disputes(
        tmp_path, monkeypatch,
        "Pattern-oriented validation compared 12 patterns across 3 scales [@grimm2006].",
        "this isn't what the source says", '{"dispute": true, "where": "source"}')
    assert got[0]["where"] == "source" and got[0]["quote"] == "twelve patterns at three scales"


def test_a_comment_that_is_not_a_dispute_is_left_to_the_tiers(tmp_path, monkeypatch):
    got, _, _ = _disputes(
        tmp_path, monkeypatch,
        "Pattern-oriented validation compared 12 patterns across 3 scales [@grimm2006].",
        "tighten this", '{"dispute": false}')
    assert got == []


def test_a_source_already_re_read_is_not_re_read_again(tmp_path, monkeypatch):
    project.record_plan(tmp_path, {"type": "plan", "disputes": [
        {"comment": "this isn't what the source says", "citekey": "grimm2006",
         "where": "source"}]})
    got, _, _ = _disputes(
        tmp_path, monkeypatch,
        "Pattern-oriented validation compared 12 patterns across 3 scales [@grimm2006].",
        "this isn't what the source says", '{"dispute": true, "where": "source"}')
    assert got[0]["where"] == "paper" and "already re-read" in got[0]["note"]


def test_a_disputed_fact_is_sent_back_and_the_revise_is_told(tmp_path, monkeypatch):
    got, markup, _ = _disputes(
        tmp_path, monkeypatch,
        "Pattern-oriented validation compared 12 patterns across 3 scales [@grimm2006].",
        "this isn't what the source says", '{"dispute": true, "where": "source"}')
    lines = planner._apply_disputes(tmp_path, _M, markup, got)
    blob = json.loads((markup.parent / planner.DISPUTES_FILE).read_text())
    assert blob["markup"] == markup.name and blob["disputes"][0]["where"] == "source"
    facts = tmp_path / "litReview" / "work" / "facts"
    assert not (facts / "grimm2006.json").exists()                 # moved aside to re-read
    assert (facts / "old_rework" / "grimm2006.json").exists()
    assert any("re-extracting Grimm et al., 2006" in ln for ln in lines)
    resp = json.loads((tmp_path / "litReview" / "work" / "comment_responses.json").read_text())
    assert resp["markup"].startswith("the paper (") and resp["responses"][0]["kind"] == "fact"


def test_a_chain_may_pass_through_another_stages_gate():
    assert planner._step_of("paper", "litreview:next").command == "haarpi next"
    assert planner._step_of("paper", "litreview:report").command == "haarpi rabbithole report"


def test_the_guide_is_never_read_as_an_ask():
    text = ck.guide_text(ck.PAPER_MANUSCRIPT, ["Check."])
    assert ck.is_guide(text) and not ck.is_guide("add a section on X")


@pytest.mark.parametrize("kind", ck.PAPER_MANUSCRIPT.kinds, ids=lambda k: k.name)
def test_every_paper_phrasing_is_one_the_gate_reads(kind):
    """The guide's phrasings must be ones the paper gate is told about: the tier prompt for
    the tiers, the dispute rule — and its deterministic floor — for disputes."""
    flat = lambda s: " ".join(s.lower().split())
    for t in kind.triggers:
        if kind.name == "dispute":
            assert flat(t) in flat(ck.DISPUTE_RULE)
            assert planner._DISPUTE_FLOOR.search(t), t
        else:
            assert flat(t) in flat(planner.STAGE_PROMPTS["paper"]), t

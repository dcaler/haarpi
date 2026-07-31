"""`haarpi next` decompose → sequence → instruct (litreview general solution).

The gate no longer collapses a whole annotation set into one tier and runs a canned chain.
It enumerates a per-comment task list, BUILDS the chain those tasks require, and steers each
verb with what its tasks need. These pin the two pure pieces (decompose normalisation, the
task→chain derivation) and the instruct hand-off, including the elephantRoom-shaped set that
motivated the change.
"""

from __future__ import annotations

import pytest

from haarpi import planner


# ── chain_from_tasks: the derivation is BUILT from the tasks, not looked up ────

def _task(need, query="", comments=("c",)):
    return {"comments": list(comments), "need": need, "query": query}


def test_all_edits_is_a_cosmetic_in_place_chain():
    built = planner.chain_from_tasks([_task("edit"), _task("edit")])
    assert built["steps"] == ["revise", "comment"]
    assert built["tier"] == "cosmetic"
    assert built["gather_topics"] == []


def test_one_sources_task_prepends_a_steered_gather():
    built = planner.chain_from_tasks([_task("sources", "household distributional equity"),
                                      _task("edit")])
    assert built["steps"] == ["gather", "collect", "revise", "comment"]
    assert built["tier"] == "gap_fill"
    assert built["gather_topics"] == ["household distributional equity"]
    assert built["section_focus"] == []


def test_a_section_routes_the_redraft_to_report():
    built = planner.chain_from_tasks([_task("section", "supply-chain reshoring")])
    assert built["steps"] == ["gather", "collect", "report", "comment"]
    assert built["section_focus"] == ["supply-chain reshoring"]
    assert built["tier"] == "gap_fill"


def test_redirect_is_redirection_and_redrafts_with_report():
    built = planner.chain_from_tasks([_task("redirect", "reframe around energy justice")])
    assert built["tier"] == "redirection"
    assert "report" in built["steps"] and "gather" in built["steps"]


def test_ingest_prepends_the_ingest_verb():
    built = planner.chain_from_tasks([_task("ingest"), _task("edit")])
    assert built["steps"] == ["ingest", "revise", "comment"]
    assert built["tier"] == "cosmetic"


def test_elephantroom_shaped_set_steers_gather_at_every_theme():
    """Three broad new themes (two 'sources', one 'section') plus two in-place edits: the gather
    is steered at all three queries, the redraft is `report` (a section is present), and the two
    edits ride along — the exact set that fell through the old whole-set classifier."""
    tasks = [_task("sources", "household distributional equity of ABM impacts"),
             _task("sources", "consumption smoothing, savings drawdown, innovation"),
             _task("section", "supply-chain reshoring and domestic production"),
             _task("edit"), _task("edit")]
    built = planner.chain_from_tasks(tasks)
    assert built["steps"] == ["gather", "collect", "report", "comment"]
    assert built["gather_topics"] == [
        "household distributional equity of ABM impacts",
        "consumption smoothing, savings drawdown, innovation",
        "supply-chain reshoring and domestic production"]
    assert built["section_focus"] == ["supply-chain reshoring and domestic production"]
    assert built["tier"] == "gap_fill"


# ── _normalise_tasks: every comment lands in exactly one task ──────────────────

def test_unknown_need_degrades_to_edit():
    tasks = planner._normalise_tasks([{"comments": [1], "need": "nonsense"}], ["a"])
    assert tasks == [{"comments": ["a"], "need": "edit", "query": ""}]


def test_a_forgotten_comment_is_swept_into_a_trailing_edit():
    tasks = planner._normalise_tasks(
        [{"comments": [1], "need": "sources", "query": "x"}], ["a", "b", "c"])
    assert tasks[0]["need"] == "sources"
    assert tasks[-1] == {"comments": ["b", "c"], "need": "edit", "query": ""}


def test_a_comment_claimed_twice_keeps_its_first_task():
    tasks = planner._normalise_tasks(
        [{"comments": [1], "need": "sources", "query": "x"},
         {"comments": [1], "need": "edit"}], ["a"])
    assert tasks == [{"comments": ["a"], "need": "sources", "query": "x"}]


def test_no_comments_is_no_tasks():
    assert planner._normalise_tasks([], []) == []


# ── decompose: brain output → tasks, with a safe fallback ──────────────────────

class _FakeBrain:
    def __init__(self, reply):
        self._reply = reply

    def __call__(self, *a, **k):
        return self

    def coordinator(self, *a, **k):
        return self._reply


def _patch_brain(monkeypatch, reply):
    monkeypatch.setattr("haarpi.brain.Brain", _FakeBrain(reply))


def test_decompose_parses_a_task_list(monkeypatch):
    _patch_brain(monkeypatch,
                 '{"tasks": [{"comments": [1], "need": "sources", "query": "topic X"}, '
                 '{"comments": [2], "need": "edit", "query": ""}]}')
    tasks = planner.decompose([{"text": "add more on X"}, {"text": "tighten this"}], {})
    assert [t["need"] for t in tasks] == ["sources", "edit"]
    assert tasks[0]["query"] == "topic X"
    assert tasks[0]["comments"] == ["add more on X"]


def test_decompose_falls_back_to_a_single_edit_on_bad_json(monkeypatch):
    _patch_brain(monkeypatch, "the model said something unparseable")
    tasks = planner.decompose([{"text": "one"}, {"text": "two"}], {})
    assert tasks == [{"comments": ["one", "two"], "need": "edit", "query": ""}]


# ── instruct: the steering config hand-off to rabbitHole ───────────────────────

def test_steering_writes_a_gap_config_with_the_queries(monkeypatch):
    calls = {}

    def fake_gap(directory, plan, extra_focus=""):
        calls.update(directory=directory, topics=plan.get("gather_topics"),
                     extra_focus=extra_focus)

        class _P:  # mimic a Path with a .name
            name = "litrev_2.yaml"
        return _P()

    import rabbithole.plan as rhplan
    monkeypatch.setattr(rhplan, "_write_gap_config", fake_gap)

    built = planner.chain_from_tasks([_task("sources", "topic A"),
                                      _task("section", "topic B")])
    name = planner._write_litreview_steering("/proj", built)
    assert name == "litrev_2.yaml"
    assert calls["topics"] == ["topic A", "topic B"]
    assert calls["extra_focus"] == "topic B"          # the section query is the report focus


def test_steering_is_a_noop_when_nothing_needs_sources():
    built = planner.chain_from_tasks([_task("edit")])
    assert planner._write_litreview_steering("/proj", built) is None

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
    assert built["steps"] == ["revise", "mindmap", "comment"]
    assert built["tier"] == "cosmetic"
    assert built["gather_topics"] == []


def test_one_sources_task_prepends_a_steered_gather():
    built = planner.chain_from_tasks([_task("sources", "household distributional equity"),
                                      _task("edit")])
    # New sources are audited (word-sense filter) then EMBEDDED (`build`) before revise — which
    # loads a cached corpus and no longer embeds. build sits immediately before revise.
    assert built["steps"] == ["gather", "collect", "audit", "build", "revise", "mindmap", "comment"]
    assert built["tier"] == "gap_fill"
    assert built["gather_topics"] == ["household distributional equity"]
    assert built["section_focus"] == []


def test_a_section_redrafts_with_revise_which_grafts_it_in():
    """A section ask is drafted and spliced in BY `revise`, at the comment that asked for it;
    only a redirect earns a whole-document `report`.

    `revise` reads a cached corpus, so a chain that changed the corpus embeds with `build`
    first."""
    built = planner.chain_from_tasks([_task("section", "supply-chain reshoring")])
    assert built["steps"] == ["gather", "collect", "audit", "build", "revise",
                              "mindmap", "comment"]
    assert "report" not in built["steps"], "a section must not cost a whole-document re-draft"
    assert built["section_focus"] == ["supply-chain reshoring"]
    assert built["tier"] == "gap_fill"


def test_redirect_is_redirection_and_redrafts_with_report():
    built = planner.chain_from_tasks([_task("redirect", "reframe around energy justice")])
    assert built["tier"] == "redirection"
    assert "report" in built["steps"] and "gather" in built["steps"]
    assert "build" not in built["steps"]         # report embeds inline


def test_ingest_gets_collect_audit_and_build_before_revise():
    """Reviewer-supplied references not yet in Zotero are fetched (`ingest`), the human finalises
    any the fetch missed (`collect`), the changed corpus is audited, then embedded (`build`)."""
    built = planner.chain_from_tasks([_task("ingest"), _task("edit")])
    assert built["steps"] == ["ingest", "collect", "audit", "build", "revise", "mindmap", "comment"]
    assert built["tier"] == "cosmetic"


def test_elephantroom_shaped_set_steers_gather_at_every_theme():
    """Three broad new themes (two 'sources', one 'section') plus two in-place edits: the gather
    is steered at all three queries, and the redraft is a single `revise` that answers all five
    in one pass — the section grafted in at its own anchor, the edits redlined in place. The
    exact set that fell through the old whole-set classifier, and then through the verb that
    replaced it."""
    tasks = [_task("sources", "household distributional equity of ABM impacts"),
             _task("sources", "consumption smoothing, savings drawdown, innovation"),
             _task("section", "supply-chain reshoring and domestic production"),
             _task("edit"), _task("edit")]
    built = planner.chain_from_tasks(tasks)
    assert built["steps"] == ["gather", "collect", "audit", "build", "revise",
                              "mindmap", "comment"]
    assert built["gather_topics"] == [
        "household distributional equity of ABM impacts",
        "consumption smoothing, savings drawdown, innovation",
        "supply-chain reshoring and domestic production"]
    assert built["section_focus"] == ["supply-chain reshoring and domestic production"]
    assert built["tier"] == "gap_fill"


# ── cite: papers already in Zotero need only embedding, not a fetch ────────────

def test_cite_is_build_then_revise_with_no_fetch():
    """The reviewer says the papers are already in Zotero. Embed-and-cite: a lone `build` before
    revise, with NO ingest/gather/collect (nothing to fetch) and NO audit (the reviewer named
    them, so deference forbids quarantining)."""
    built = planner.chain_from_tasks([_task("cite"), _task("edit")])
    assert built["steps"] == ["build", "revise", "mindmap", "comment"]
    assert built["tier"] == "cosmetic"
    for verb in ("ingest", "gather", "collect", "audit"):
        assert verb not in built["steps"]


def test_cite_does_not_double_build_on_a_gather_chain():
    """A sources chain already builds after collect; a co-occurring `cite` must not add a second
    `build`."""
    built = planner.chain_from_tasks([_task("cite"), _task("sources", "topic")])
    assert built["steps"].count("build") == 1
    assert built["steps"] == ["gather", "collect", "audit", "build", "revise", "mindmap", "comment"]


def test_a_revise_redraft_never_reads_an_unembedded_corpus():
    """The embedding contract: any chain whose corpus CHANGED (collect present) or that cites
    reviewer-added papers must `build` immediately before a `revise` re-draft — never leave
    revise to read a corpus `build` has not touched. `report` chains are exempt (embed inline)."""
    for tasks in ([_task("sources", "q")], [_task("ingest")], [_task("cite")],
                  [_task("cite"), _task("ingest")]):
        steps = planner.chain_from_tasks(tasks)["steps"]
        if steps[-2] == "revise":
            assert steps[steps.index("revise") - 1] == "build"
        # and audit, when present, sits right after collect
        if "audit" in steps:
            assert steps[steps.index("collect") + 1] == "audit"


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


# ── the deterministic floor: an explicit new-section ask cannot be filed as "sources" ──

def test_an_explicit_section_ask_is_promoted_even_when_the_model_said_sources():
    """The 8B coordinator filed 'a section on X' under sources (→ revise → decline). The floor
    promotes it to a section task, so the chain grafts the strand in instead."""
    text = "It would be great to have a section identifying regular milestones."
    tasks = planner._normalise_tasks(
        [{"comments": [1], "need": "sources", "query": "milestones"}], [text])
    assert [t["need"] for t in tasks] == ["section"]
    assert tasks[0]["query"] == text                 # the comment's own words steer the graft
    assert planner.chain_from_tasks(tasks)["steps"] == \
        ["gather", "collect", "audit", "build", "revise", "mindmap", "comment"]


def test_an_edit_of_an_existing_section_is_not_promoted():
    """'this section' is the structure that's already there — an edit, not a new strand."""
    tasks = planner._normalise_tasks(
        [{"comments": [1], "need": "edit"}], ["This section is unclear — tighten it."])
    assert [t["need"] for t in tasks] == ["edit"]


def test_promotion_splits_a_mixed_task_and_preserves_coverage():
    tasks = planner._normalise_tasks(
        [{"comments": [1, 2], "need": "edit"}],
        ["Please add a section on lifecycle milestones.", "Also fix this typo."])
    assert sorted(t["need"] for t in tasks) == ["edit", "section"]
    assert sorted(c for t in tasks for c in t["comments"]) == \
        ["Also fix this typo.", "Please add a section on lifecycle milestones."]


# ── the deterministic cite floor: "already in Zotero, cite them" is never a fetch ──

def test_already_in_zotero_is_promoted_to_cite_even_when_the_model_said_ingest():
    """The exact DRvehicle misroute: the 8B coordinator filed 'cite Doblinger and Howell' under
    `ingest` (→ a fetch that drops author-only mentions → 'none found'). The floor promotes it to
    `cite`, so the chain embeds the already-collected papers (`build → revise`) instead."""
    text = "I've added Doblinger 2019 and Howell 2017 to the zotero collection. cite them"
    tasks = planner._normalise_tasks([{"comments": [1], "need": "ingest", "query": ""}], [text])
    assert [t["need"] for t in tasks] == ["cite"]
    assert planner.chain_from_tasks(tasks)["steps"] == ["build", "revise", "mindmap", "comment"]


def test_a_genuine_ingest_is_not_stolen_by_the_cite_floor():
    """The floor requires an explicit 'in Zotero / the collection / the library' assertion, so a
    real fetch request keeps its `ingest` (and its collect/build) rather than being demoted."""
    for text in ("cite Smith 2020 here",
                 "add @foo2019 and these references: Bar 2021",
                 "please incorporate the attached reference list"):
        tasks = planner._normalise_tasks(
            [{"comments": [1], "need": "ingest", "query": ""}], [text])
        assert [t["need"] for t in tasks] == ["ingest"], text


def test_a_section_ask_outranks_the_cite_floor():
    """Section (rule 2) runs before cite (rule 3): 'add a section on X; the papers are already in
    zotero' is a section task, not a cite task — the review's structure change wins."""
    text = "Add a section on grants versus loans — the papers are already in the zotero collection."
    tasks = planner._normalise_tasks([{"comments": [1], "need": "edit"}], [text])
    assert [t["need"] for t in tasks] == ["section"]


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

    import rabbithole.steering as rhsteer
    monkeypatch.setattr(rhsteer, "_write_gap_config", fake_gap)

    built = planner.chain_from_tasks([_task("sources", "topic A"),
                                      _task("section", "topic B")])
    name = planner._write_litreview_steering("/proj", built)
    assert name == "litrev_2.yaml"
    assert calls["topics"] == ["topic A", "topic B"]
    assert calls["extra_focus"] == "topic B"          # the section query is the graft focus


def test_steering_is_a_noop_when_nothing_needs_sources():
    built = planner.chain_from_tasks([_task("edit")])
    assert planner._write_litreview_steering("/proj", built) is None


def test_only_a_redirect_can_cost_a_whole_document_redraft():
    """The wave-4 promise. `report` re-plans and regenerates every section, discarding the
    reviewer's comment threads with it, so a single `section` ask must never reach it."""
    for need in ("edit", "sources", "section", "cite", "ingest"):
        built = planner.chain_from_tasks([_task(need, "a topic")])
        assert "report" not in built["steps"], f"{need!r} must not trigger a full re-draft"
    assert "report" in planner.chain_from_tasks([_task("redirect", "new direction")])["steps"]


def test_a_section_alongside_an_edit_costs_neither_of_them():
    """The set used to be redrafted by its heaviest need, so the lighter asks beside it were
    silently dropped: first `report` discarded the edits AND the threads, then `graft` kept the
    threads but still could not carry an edit. One `revise` now answers both in kind."""
    built = planner.chain_from_tasks([_task("section", "reshoring"), _task("edit")])
    assert "revise" in built["steps"]
    assert "report" not in built["steps"] and "graft" not in built["steps"]


def test_a_section_ask_survives_an_adjective_and_the_prompts_own_vocabulary():
    """The deterministic floor was narrower than the vocabulary _DECOMPOSE_PROMPT invites, so
    "add an entire section" and "an entire theme" both fell through to the 8B model."""
    for text in ["I'd like to add an entire section on household impacts",
                 "another important section on reshoring",
                 "I'd like an entire theme on distributional equity",
                 "consumption smoothing needs its own sub-topic",
                 "a whole new strand on supply chains"]:
        tasks = planner._normalise_tasks(
            [{"comments": [1], "need": "edit", "query": ""}], [text])
        assert [t["need"] for t in tasks] == ["section"], text


def test_ordinary_talk_about_an_existing_section_is_still_an_edit():
    for text in ["this section is unclear — tighten it",
                 "the section on clubs is too long",
                 "in section 3 you say the opposite"]:
        tasks = planner._normalise_tasks(
            [{"comments": [1], "need": "edit", "query": ""}], [text])
        assert [t["need"] for t in tasks] == ["edit"], text

"""The methods review as a stage in the ladder.

A project can need two literature reviews, because one anchor cannot serve two questions.
They are separate STAGES rather than two chains of one stage for a reason worth stating: a
stage mints a release, and only a release can be an `inputs` edge. "Minting the design should
mean the methods review is done" is a sentence you can only write if both are stages.

Venues are the other shape — one deliverable forked late, for different audiences. Two reviews
share nothing but a Zotero collection: different anchors, different corpora, different Chroma
stores, different readers downstream. They were never one thing.
"""

from __future__ import annotations

import pytest

from haarpi import planner, project
from rabbithole import config as rh


def test_the_methods_review_is_a_stage():
    spec = project.DEFAULT_STAGES["methodsreview"]
    assert spec["tool"] == "rabbithole"
    assert spec["inputs"] == ["litreview"]          # it needs the substantive review first
    assert spec["infix"] == "methodsreview"          # its own deliverable, not litreview's


def test_the_stage_dir_is_the_review_kind_dir():
    """These diverged once — REVIEW_KINDS was renamed and the stage's literal was not, and a
    migration then persisted the stale value into a project manifest."""
    assert project.DEFAULT_STAGES["methodsreview"]["dir"] == rh.REVIEW_KINDS["methods"].dir


def test_the_two_literature_stages_share_steps_and_tiers():
    assert planner.STAGE_STEPS["methodsreview"] is planner.STAGE_STEPS["litreview"]
    assert planner.STAGE_TIERS["methodsreview"] is planner.STAGE_TIERS["litreview"]


def test_the_infixes_cannot_collide():
    a = project.DEFAULT_STAGES["litreview"]["infix"]
    b = project.DEFAULT_STAGES["methodsreview"]["infix"]
    c = project.DEFAULT_STAGES["build"]["infix"]
    assert len({a, b, c}) == 3, "litreview, methodsreview and raster's `methods` are distinct"


# ── who does what to which ───────────────────────────────────────────────────

def test_a_methods_command_names_its_review():
    """`haarpi rabbithole gather methods` — who, what, which. It matches the board title
    the planner writes for the same task, so the command and the title say one thing."""
    got = planner._scoped(planner.STAGE_STEPS["methodsreview"]["gather"].command,
                          "methodsreview")
    assert got == "haarpi rabbithole gather methods"


def test_the_substantive_chain_is_untouched():
    """Every existing project's commands must not move."""
    for verb in ("gather", "collect", "build", "report", "audit"):
        cmd = planner.STAGE_STEPS["litreview"][verb].command
        assert planner._scoped(cmd, "litreview") == cmd


def test_the_review_token_matches_the_venue_token():
    """The command's last word and the title's venue slot are the same token, or a board
    reading `rabbithole gather methods 1` would run something else."""
    assert planner.STAGE_REVIEW["methodsreview"] == planner.STAGE_VENUE["methodsreview"]


def test_a_non_rabbithole_command_is_left_alone():
    assert planner._scoped("haarpi next", "methodsreview") == "haarpi next"


# ── opt-in ───────────────────────────────────────────────────────────────────

def test_a_project_without_the_config_has_no_methods_review(tmp_path):
    """Most projects need one review. A stage that opened for all of them would queue a
    gather nobody asked for, so its config's presence IS the opt-in."""
    import yaml
    (tmp_path / "haarpi.yaml").write_text(yaml.safe_dump(
        {"name": "X", "short_title": "X", "brief": "b"}))
    m = project.load_manifest(tmp_path)
    assert project.has_methods_review(tmp_path, m) is False


def test_the_config_turns_the_stage_on(tmp_path):
    import yaml
    (tmp_path / "haarpi.yaml").write_text(yaml.safe_dump(
        {"name": "X", "short_title": "X", "brief": "b"}))
    m = project.load_manifest(tmp_path)
    d = tmp_path / m.stages["methodsreview"]["dir"]
    d.mkdir()
    (d / "methodsreview.yaml").write_text("{}")
    assert project.has_methods_review(tmp_path, m) is True


def test_an_empty_directory_is_not_an_opt_in(tmp_path):
    import yaml
    (tmp_path / "haarpi.yaml").write_text(yaml.safe_dump(
        {"name": "X", "short_title": "X", "brief": "b"}))
    m = project.load_manifest(tmp_path)
    (tmp_path / m.stages["methodsreview"]["dir"]).mkdir()
    assert project.has_methods_review(tmp_path, m) is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

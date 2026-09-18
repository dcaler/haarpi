"""The methods review — a rung inside the design stage, not a stage of its own.

It was tried as a stage twice, and both placements were wrong. Before `design`, nothing could
spur the search: the ramus conversation is what works out which methodological families the
approach needs. After `design`, the search had nothing to be scoped by. It is one conversation
interrupted by the search it calls for:

    ramus init    -> scope          the framework + METHODS_SCOPE.md
    rabbitHole    -> methodsreview  gather/collect/build/report against that scope
    ramus design  -> prereg         binds the methods, plans the study
"""

from __future__ import annotations

import pytest

from haarpi import planner, project
from rabbithole import config as rh


def test_the_ladder_has_six_stages_again():
    assert list(project.DEFAULT_STAGES) == ["litreview", "design", "build",
                                            "experiments", "paper", "deck"]
    assert "methodsreview" not in project.DEFAULT_STAGES


def test_design_climbs_three_rungs():
    assert planner._DESIGN_LADDER == ("scope", "methodsreview", "")
    assert planner.has_rungs("design") and planner.has_rungs("paper")
    assert not planner.has_rungs("build")


def test_the_scope_rung_queues_the_methods_search():
    assert planner.DESIGN_LADDER["scope"] == [
        "methods_gather", "methods_collect", "methods_build", "methods_report", "comment"]


def test_the_methods_review_rung_queues_the_second_conversation():
    assert planner.DESIGN_LADDER["methodsreview"] == ["design_bind", "comment"]


def test_the_two_ramus_sessions_are_distinct_verbs():
    """`plan` was the obvious name for the second and is taken twice already — `raster plan`
    and `rayleigh plan` both mean "plan against something already built", the opposite of
    this."""
    steps = planner.STAGE_STEPS["design"]
    assert steps["design_session"].command == "haarpi ramus init"
    assert steps["design_bind"].command == "haarpi ramus design"
    assert steps["design_session"].attended and steps["design_bind"].attended


def test_the_methods_steps_name_the_review():
    """Same rabbitHole verbs as a literature review, pointed at the methods one by name."""
    steps = planner.STAGE_STEPS["design"]
    for name, verb in (("methods_gather", "gather"), ("methods_collect", "collect"),
                       ("methods_build", "build"), ("methods_report", "report")):
        assert steps[name].command == f"haarpi rabbithole {verb} methods"


def test_the_design_stage_still_mints_the_prereg():
    """raster's build target is unchanged: the rung with no word in its chain."""
    assert project.DEFAULT_STAGES["design"]["infix"] == "prereg"
    assert planner._DESIGN_LADDER[-1] == ""


def test_a_scope_markup_is_recognised_as_its_rung(tmp_path):
    from pathlib import Path as _P
    got = planner._deliverable_of(_P("260917_X_scope_ra_DCR.docx"), "X", "design")
    assert got == "scope"


def test_a_prereg_markup_is_the_final_rung(tmp_path):
    from pathlib import Path as _P
    assert planner._deliverable_of(_P("260917_X_prereg_ra_DCR.docx"), "X", "design") == ""


def test_the_scope_brief_is_what_seeds_the_methods_config(tmp_path):
    """The brief names the families; the config inverts the anchor onto them and EXCLUDES the
    project's own subject matter. Getting that backwards is what made five rounds of steering
    fail to find sequence-analysis methodology."""
    import yaml
    (tmp_path / "haarpi.yaml").write_text(yaml.safe_dump(
        {"name": "X", "short_title": "X", "brief": "firm funding pathways"}))
    m = project.load_manifest(tmp_path)
    d = tmp_path / m.stages["design"]["dir"] / "designdocs"
    d.mkdir(parents=True, exist_ok=True)
    (d / project.METHODS_SCOPE).write_text(
        "Optimal matching and sequence distance measures\nCluster validity indices")

    planner._seed_methods_config(tmp_path, m)
    md = tmp_path / rh.REVIEW_KINDS["methods"].dir
    cfg = rh.load_project(md)
    assert "Optimal matching" in cfg.domain_anchor
    assert "methodological contribution" in cfg.exclude_topics
    assert cfg.target_max <= 30, "a methods review is small by design"


def test_seeding_never_overwrites_an_edited_config(tmp_path):
    import yaml
    (tmp_path / "haarpi.yaml").write_text(yaml.safe_dump(
        {"name": "X", "short_title": "X", "brief": "b"}))
    m = project.load_manifest(tmp_path)
    d = tmp_path / m.stages["design"]["dir"] / "designdocs"
    d.mkdir(parents=True, exist_ok=True)
    (d / project.METHODS_SCOPE).write_text("families")
    md = tmp_path / rh.REVIEW_KINDS["methods"].dir
    md.mkdir(parents=True, exist_ok=True)
    (md / "methodsreview.yaml").write_text("project_name: MINE\n")

    planner._seed_methods_config(tmp_path, m)
    assert (md / "methodsreview.yaml").read_text() == "project_name: MINE\n"


def test_no_brief_seeds_nothing(tmp_path):
    import yaml
    (tmp_path / "haarpi.yaml").write_text(yaml.safe_dump(
        {"name": "X", "short_title": "X", "brief": "b"}))
    m = project.load_manifest(tmp_path)
    planner._seed_methods_config(tmp_path, m)
    assert rh.latest_project_file(tmp_path / rh.REVIEW_KINDS["methods"].dir) is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

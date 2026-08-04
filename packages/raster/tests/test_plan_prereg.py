"""`raster plan` reads the committed design prereg as its build target (post-split).

The design stage (rayleigh) commits an analytical approach + a "Data infrastructure required"
handoff; raster builds to satisfy it. These pin that the plan session is pointed at the prereg.
"""

from importlib.resources import files

from raster.plan import PLAN_PROMPT


def test_plan_prompt_points_at_the_committed_prereg():
    p = PLAN_PROMPT.lower()
    assert "prereg" in p
    assert "design/" in PLAN_PROMPT
    assert "build target" in p
    assert "experiments.yaml" in PLAN_PROMPT
    assert "fall back to the brief" in p        # graceful for standalone raster projects


def test_planning_playbook_makes_the_prereg_the_build_target():
    tmpl = (files("raster") / "templates" / "PLANNING.md.tmpl").read_text().lower()
    assert "the build target" in tmpl
    assert "data infrastructure" in tmpl and "handoff to raster" in tmpl
    assert "outranks the brief" in tmpl

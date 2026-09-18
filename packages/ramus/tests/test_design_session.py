"""`ramus design` — the session that binds the methods once their literature is in hand.

It exists because a prereg written before the methodology was read carries working choices
rather than bound ones: one project's had twenty [FIXED]/[PROVISIONAL] markers and a "methods
addendum" promising to settle them later. The sources are here by this rung, so nothing should
still be provisional when it ends.
"""

from __future__ import annotations

import types

import pytest
import yaml

from haarpi import planner, project
from ramus import design as rd


def _project(tmp_path, *, framework=True, scope=True, methods_released=False):
    (tmp_path / "haarpi.yaml").write_text(yaml.safe_dump(
        {"name": "X", "short_title": "X", "brief": "b"}))
    m = project.load_manifest(tmp_path)
    dd = tmp_path / m.stages["design"]["dir"] / "designdocs"
    dd.mkdir(parents=True, exist_ok=True)
    if framework:
        (dd / "EXPERIMENTS.md").write_text("# framework\n")
    if scope:
        (dd / project.METHODS_SCOPE).write_text("optimal matching; cluster validity")
    if methods_released:
        from rabbithole import config as rh
        out = tmp_path / rh.REVIEW_KINDS["methods"].dir / "output"
        out.mkdir(parents=True, exist_ok=True)
        (out / "260918_X_methodsreview.md").write_text("released")
    return types.SimpleNamespace(dir=str(tmp_path), no_launch=True, model="")


# ── the verb the planner queues must exist ───────────────────────────────────

def test_the_command_the_planner_queues_is_a_real_verb():
    """The planner queued `haarpi ramus design` while ramus had only `init` — the same shape
    as the `rayleigh init` references that survived the split, and the reason to check the
    board's commands against the CLI rather than against the figure."""
    from ramus.cli import main
    queued = planner.STAGE_STEPS["design"]["design_bind"].command
    assert queued == "haarpi ramus design"
    verb = queued.split()[-1]
    with pytest.raises(SystemExit) as e:      # --help exits 0 for a verb that exists
        main([verb, "--help"])
    assert e.value.code == 0


# ── it refuses rather than binding against nothing ───────────────────────────

def test_it_refuses_while_the_methods_review_is_outstanding(tmp_path, capsys):
    """Binding methodological choices without the literature is precisely what this rung
    exists to prevent, so it must not merely warn."""
    args = _project(tmp_path, methods_released=False)
    assert rd.run_design(args) == 1
    assert "has not been released" in capsys.readouterr().out


def test_it_runs_once_the_methods_review_is_released(tmp_path):
    args = _project(tmp_path, methods_released=True)
    assert rd.run_design(args) == 0          # --no-launch: prints the documents and stops


def test_it_refuses_without_a_framework_to_bind(tmp_path, capsys):
    args = _project(tmp_path, framework=False, methods_released=True)
    assert rd.run_design(args) == 1
    assert "ramus init" in capsys.readouterr().out


def test_it_refuses_outside_a_project(tmp_path, capsys):
    args = types.SimpleNamespace(dir=str(tmp_path), no_launch=True, model="")
    assert rd.run_design(args) == 1
    assert "haarpi.yaml" in capsys.readouterr().out


# ── what the session is told ─────────────────────────────────────────────────

def test_the_brief_forbids_leaving_anything_provisional():
    assert "NOTHING MAY REMAIN PROVISIONAL" in rd.DESIGN_PROMPT
    assert "working choice" in rd.DESIGN_PROMPT


def test_the_brief_demands_a_source_and_an_objection_per_choice():
    """A choice with no citation is not bound; a citation with no objection named has not been
    read carefully."""
    assert "cite the source it rests on" in rd.DESIGN_PROMPT
    assert "standing objection" in rd.DESIGN_PROMPT


def test_the_brief_names_the_three_documents_it_reads():
    for doc in ("EXPERIMENTS.md", "METHODS_SCOPE.md", "methodsReview/output/"):
        assert doc in rd.DESIGN_PROMPT


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

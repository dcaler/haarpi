"""`rayleigh plan` — the EXPERIMENTS stage (executable experiments), post-split.

`init` designs the analytical framework upstream of the code (in design/); `plan` runs AFTER
raster has built the tooling and authors the EXECUTABLE experiments into results/ — exactly where
conduct/queue read the spec from. These pin the non-interactive mechanics (--no-launch).
"""

from __future__ import annotations

import types

from rayleigh import plan as rplan
from rayleigh.config import Config


def _args(root, **kw):
    base = dict(dir=str(root), name="demo", brief="study X in Y", no_launch=True)
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_plan_authors_executable_spec_into_results(tmp_path, monkeypatch):
    monkeypatch.setattr(rplan, "load_config", lambda create=True: Config())
    rc = rplan.run_plan(_args(tmp_path))
    assert rc == 0
    results = tmp_path / "results"
    # the executable spec + progress live in results/, where conduct/queue read them
    assert (results / "designdocs" / "experiments.yaml").is_file()
    assert (results / "designdocs" / "PROGRESS.md").is_file()
    assert (results / "designdocs" / "PLANNING.md").is_file()
    assert (results / "rayleigh.yaml").is_file()
    assert (results / "data").is_dir() and (results / "figures").is_dir()
    assert not (tmp_path / "design").exists()          # plan is the experiments stage, not design


def test_plan_playbook_targets_the_built_tooling_and_prereg(tmp_path, monkeypatch):
    monkeypatch.setattr(rplan, "load_config", lambda create=True: Config())
    rplan.run_plan(_args(tmp_path))
    playbook = (tmp_path / "results" / "designdocs" / "PLANNING.md").read_text().lower()
    assert "executable experiments" in playbook
    assert "run_adapter" in playbook
    assert "raster-built" in playbook or "built tooling" in playbook
    assert "trace to a prereg question" in playbook or "fulfil the committed" in playbook


def test_plan_prompt_reads_prereg_and_built_code_then_hands_to_conduct():
    p = rplan.PLAN_PROMPT.lower()
    assert "prereg" in p and "built code" in p
    assert "experiments.yaml" in rplan.PLAN_PROMPT
    assert "rayleigh queue" in p or "rayleigh conduct" in p     # the handoff

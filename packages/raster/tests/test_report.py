"""Tests for `raster report` — the Methods Digest handoff to raconteur. All offline:
the command is mechanical (no LLM), so these fabricate a project tree and assert the
digest faithfully lifts the aim/contracts/goldens and lands in the ra* revision chain."""

import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from raster.config import Config
from raster.report import (build_report, default_report_path, design_sections,
                           frozen_claims, module_contracts, run_report)
from raster.spec import Project

SPEC = {
    "meta": {"package": "abm", "project": "PostIneq", "language": "python",
             "workers": {"strong": "qwen3.6:27b", "worker": "llama3.2"}},
    "modules": [
        {"id": "P0", "name": "test-infra", "tasks": [
            {"id": "P0.M1", "title": "Freeze M1", "worker": "strong",
             "deliverables": ["tests/test_grid.py"]},
        ]},
        {"id": "M1", "name": "grid", "tasks": [
            {"id": "M1.T1", "title": "Sugarscape grid", "worker": "worker",
             "deliverables": ["abm/grid.py", "tests/test_grid.py"],
             "spec": "N×N torus of sugar cells with regrowth.",
             "checkpoint": "regrowth rate matches the calibrated GROWTH constant",
             "unit_test": {"file": "tests/test_grid.py", "cmd": "pytest -q tests/test_grid.py"}},
        ], "gate": {"id": "G1", "spec": "grid + agents compose into a step",
                    "integration_test": {"file": "tests/gate_m1.py", "cmd": "pytest -q tests/gate_m1.py"}}},
    ],
}

DESIGN_MD = """# PostIneq — Design

> One-liner: a Sugarscape-3 ABM.

## 1. Aim
<!-- skeleton comment to strip -->
Measure the inequality frontier under redistribution.

## 2. The spine
A discrete-time agent loop over a sugar torus.

## 6. Appendix
Scratch notes not relevant to methods.
"""


def make_project(tmp_path) -> Project:
    code = tmp_path / "code"
    (code / "abm").mkdir(parents=True)
    dd = code / "designdocs"
    dd.mkdir()
    (dd / "DESIGN.md").write_text(DESIGN_MD)
    (dd / "tasks.yaml").write_text("meta: {}\n")     # presence only; spec passed in-memory
    tests = code / "tests"
    tests.mkdir()
    (tests / "test_grid.py").write_text(
        '"""Grid regrowth holds the calibrated frontier.\n\nlonger body ignored."""\n'
        "GROWTH = 1\n"
        "GINI_TARGET = 0.83\n"
        "helper_lower = 5\n")     # non-UPPER: excluded
    return Project(root=tmp_path, code=code, cfg=Config(),
                   ry={"project": "PostIneq", "package": "abm",
                       "description": "a Sugarscape-3 ABM", "brief": "build a redistribution ABM"},
                   spec=SPEC)


def test_design_sections_strips_comments_and_splits():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        code = Path(d) / "code"
        (code / "designdocs").mkdir(parents=True)
        (code / "designdocs" / "DESIGN.md").write_text(DESIGN_MD)
        secs = dict(design_sections(code))
    assert "skeleton comment" not in secs["1. Aim"]
    assert "inequality frontier" in secs["1. Aim"]
    assert set(secs) == {"1. Aim", "2. The spine", "6. Appendix"}


def test_module_contracts_excludes_p0_and_test_deliverables():
    mods = module_contracts(SPEC)
    assert [m["id"] for m in mods] == ["M1"]           # P0 dropped
    t = mods[0]["tasks"][0]
    assert t["deliverables"] == ["abm/grid.py"]        # tests/ deliverable dropped
    assert t["checkpoint"].startswith("regrowth rate")
    assert mods[0]["gate_test"] == "tests/gate_m1.py"


def test_frozen_claims_lifts_docline_and_upper_constants(tmp_path):
    project = make_project(tmp_path)
    claims = dict((rel, (doc, consts)) for rel, doc, consts in frozen_claims(project.code))
    doc, consts = claims["test_grid.py"]
    assert doc == "Grid regrowth holds the calibrated frontier."
    assert "GROWTH = 1" in consts and "GINI_TARGET = 0.83" in consts
    assert not any("helper_lower" in c for c in consts)   # lowercase name excluded


def test_build_report_assembles_all_sections(tmp_path):
    project = make_project(tmp_path)
    md = build_report(project, today=date(2026, 7, 7))
    assert "# PostIneq — Methods Digest" in md
    assert "a Sugarscape-3 ABM" in md                 # description banner
    assert "## 1. Aim" in md and "inequality frontier" in md
    assert "Appendix" not in md                        # non-methods section filtered out
    assert "### M1 — grid" in md and "abm/grid.py" in md
    assert "regrowth rate matches" in md               # checkpoint surfaced
    assert "GINI_TARGET = 0.83" in md                  # frozen golden surfaced
    assert "strong=`qwen3.6:27b`" in md                # provenance
    assert "build a redistribution ABM" in md          # original brief


def test_default_report_path_is_code_output_ra_chain(tmp_path):
    project = make_project(tmp_path)
    p = default_report_path(project, today=date(2026, 7, 7))
    assert p == project.code / "output" / "260707_postineq_methods_ra.md"
    assert p.parent == project.code / "output"          # the build stage's output dir


def test_run_report_writes_to_code_output(tmp_path, monkeypatch, capsys):
    project = make_project(tmp_path)
    monkeypatch.setattr("raster.report.load_project", lambda d: project)
    args = SimpleNamespace(dir=str(tmp_path), out=None, dry_run=False)
    rc = run_report(args)
    assert rc == 0
    written = list((project.code / "output").glob("*_methods_ra.md"))
    assert len(written) == 1
    assert "Methods Digest" in written[0].read_text()
    # the workspace root stays clean — the digest is a stage deliverable
    assert not list(tmp_path.glob("*_methods_ra.md"))


def test_run_report_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    project = make_project(tmp_path)
    monkeypatch.setattr("raster.report.load_project", lambda d: project)
    args = SimpleNamespace(dir=str(tmp_path), out=None, dry_run=True)
    rc = run_report(args)
    assert rc == 0
    assert not list(tmp_path.glob("*_methods_ra.md"))
    assert "Methods Digest" in capsys.readouterr().out

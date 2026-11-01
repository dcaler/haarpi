"""`ramus init` — the DESIGN stage (preregistration), post-split.

init now authors into `design/` (its own directory, upstream of build), reads the litReview +
brief rather than finished `code/`, and renders a `prereg` docx the haarpi gate mints. These pin
the non-interactive mechanics (the interactive Claude session isn't launched under --no-launch).
"""

from __future__ import annotations

import types

import pytest

from ramus import init as rinit
from rayleigh.config import Config


def _args(root, **kw):
    base = dict(dir=str(root), name="demo", brief="study X in Y", new_cycle=False,
                no_launch=True)
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_init_authors_into_design_not_results(tmp_path, monkeypatch):
    # no code/ present — design is upstream of the build, so init must not require it
    monkeypatch.setattr(rinit, "load_config", lambda: Config())
    rc = rinit.run_init(_args(tmp_path))
    assert rc == 0
    design = tmp_path / "design"
    assert (design / "designdocs" / "PLANNING.md").is_file()
    assert (design / "designdocs" / "EXPERIMENTS.md").is_file()
    assert (design / "ramus.yaml").is_file()
    assert (design / "output").is_dir()                 # where the minted prereg lands
    assert not (tmp_path / "results").exists()          # conduct's dir is not init's job
    # init authors the FRAMEWORK only — the executable spec is `rayleigh plan`'s job
    assert not (design / "designdocs" / "experiments.yaml").exists()
    assert not (design / "designdocs" / "PROGRESS.md").exists()


def test_planning_playbook_is_new_domain(tmp_path, monkeypatch):
    monkeypatch.setattr(rinit, "load_config", lambda: Config())
    rinit.run_init(_args(tmp_path))
    playbook = (tmp_path / "design" / "designdocs" / "PLANNING.md").read_text()
    assert "research questions" in playbook.lower()
    assert "analytical approach" in playbook.lower()
    assert "upstream of the code" in playbook.lower()
    assert "raster builds it after" in playbook.lower()


def test_render_prereg_makes_a_gate_ready_docx(tmp_path):
    """render_prereg turns the design doc into a chain-named prereg docx haarpi recognises."""
    pytest.importorskip("haarpi.render")
    from haarpi import render as hrender
    if not hrender.check_pandoc():
        pytest.skip("pandoc not available")

    design = tmp_path / "design"
    (design / "designdocs").mkdir(parents=True)
    (design / "designdocs" / "EXPERIMENTS.md").write_text(
        "# Prereg\n\n## Research questions\n\nQ1: does X drive Y?\n\n"
        "## Analytical approach\n\nTransfer entropy over the chain.\n")
    doc = rinit.render_prereg(design, Config(), "demo", "260804")
    assert doc is not None and doc.is_file()
    assert doc.name == "260804_demo_prereg_ra.docx"     # {cycle}_{short}_{rung}_{ra}
    scope = rinit.render_prereg(design, Config(), "demo", "260804", rung="scope")
    assert scope.name == "260804_demo_scope_ra.docx"
    from haarpi import planner
    from pathlib import Path as _P
    assert planner._deliverable_of(_P(scope.name), "demo", "design") == "scope"
    assert planner._deliverable_of(_P(doc.name), "demo", "design") == ""

    # haarpi parses it as design-stage markup (infix prereg), not a release
    from haarpi import naming
    parsed = naming.parse(doc, "demo")
    assert parsed is not None
    _ds, chain, _ext = parsed
    assert "prereg" in [c.lower() for c in chain]
    assert not naming.is_release(chain)                 # a draft, awaiting the mint


def test_the_command_line_gives_init_every_option_it_reads(tmp_path, monkeypatch):
    """The split from rayleigh dropped --name/--brief from the parser while run_init kept
    reading them, so a bare `haarpi ramus init` died on AttributeError before doing anything."""
    from ramus import cli
    monkeypatch.setattr(rinit, "load_config", lambda: Config())
    assert cli.main(["init", "-C", str(tmp_path), "--no-launch"]) == 0
    assert (tmp_path / "design" / "ramus.yaml").is_file()


def test_init_renders_the_scope_rung_on_exit(tmp_path, monkeypatch):
    """init renders a SCOPE: rendered as `prereg`, the gate read DigiPros' framework as the
    finished preregistration and held it behind a methods review only a scope mint starts."""
    import yaml
    (tmp_path / "haarpi.yaml").write_text(yaml.safe_dump({"name": "D", "short_title": "Dp"}))
    monkeypatch.setattr(rinit, "load_config", lambda: Config())
    monkeypatch.setattr(rinit.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(rinit, "launch_session", lambda *a, **k: 0)
    seen = {}
    monkeypatch.setattr(rinit, "render_prereg",
                        lambda d, c, short, cycle, rung="prereg": seen.update(
                            short=short, rung=rung))
    assert rinit.run_init(_args(tmp_path, no_launch=False)) == 0
    assert seen == {"short": "Dp", "rung": "scope"}       # the manifest's short title, too


def test_design_renders_the_prereg_on_exit(tmp_path, monkeypatch):
    import yaml
    from ramus import design as rdesign
    (tmp_path / "haarpi.yaml").write_text(yaml.safe_dump({"name": "D", "short_title": "Dp"}))
    docs = tmp_path / "design" / "designdocs"
    docs.mkdir(parents=True)
    (docs / "EXPERIMENTS.md").write_text("# bound\n")
    (tmp_path / "design" / "ramus.yaml").write_text("cycle: '261101'\n")
    monkeypatch.setattr(rdesign, "load_config", lambda: Config())
    monkeypatch.setattr(rdesign.hproject, "methods_review_pending", lambda r, m: False)
    monkeypatch.setattr(rdesign.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(rdesign.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(returncode=0))
    seen = {}
    monkeypatch.setattr(rinit, "render_prereg",
                        lambda d, c, short, cycle, rung="prereg": seen.update(
                            short=short, cycle=cycle, rung=rung))
    assert rdesign.run_design(types.SimpleNamespace(dir=str(tmp_path), no_launch=False,
                                                    model="")) == 0
    assert seen == {"short": "Dp", "cycle": "261101", "rung": "prereg"}

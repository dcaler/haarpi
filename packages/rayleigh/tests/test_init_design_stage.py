"""`rayleigh init` — the DESIGN stage (preregistration), post-split.

init now authors into `design/` (its own directory, upstream of build), reads the litReview +
brief rather than finished `code/`, and renders a `prereg` docx the haarpi gate mints. These pin
the non-interactive mechanics (the interactive Claude session isn't launched under --no-launch).
"""

from __future__ import annotations

import types

import pytest

from rayleigh import init as rinit
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
    assert (design / "rayleigh.yaml").is_file()
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
    assert doc.name == "260804_demo_prereg_ra.docx"     # {cycle}_{short}_prereg_{ra}

    # haarpi parses it as design-stage markup (infix prereg), not a release
    from haarpi import naming
    parsed = naming.parse(doc, "demo")
    assert parsed is not None
    _ds, chain, _ext = parsed
    assert "prereg" in [c.lower() for c in chain]
    assert not naming.is_release(chain)                 # a draft, awaiting the mint

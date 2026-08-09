"""razzle CLI — `razzle deck` (gather + author) and `razzle render` (spec → .pptx)."""

from __future__ import annotations

import json

import pytest

from haarpi import figure, project
from razzle import assets, cli

_HAS_MASTER = assets.master_pptx("default") is not None


def _fixture(root):
    project.save_manifest(project.Manifest(name="demo", short_title="demo", brief="x"), root)
    figure.write_figure(root, "demo", figure.stage_dag(project.DEFAULT_STAGES), render_svg=False)


def test_deck_no_launch_gathers_and_scaffolds(tmp_path, capsys):
    _fixture(tmp_path)
    rc = cli.main(["deck", "--dir", str(tmp_path), "--format", "shorttalk", "--no-launch"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "shorttalk" in out and "~11 slides" in out          # sized to the format
    assert (tmp_path / "slides" / "shorttalk").is_dir()


def test_deck_rejects_unknown_format(tmp_path):
    _fixture(tmp_path)
    assert cli.main(["deck", "--dir", str(tmp_path), "--format", "keynote", "--no-launch"]) == 2


@pytest.mark.skipif(not _HAS_MASTER, reason="neutral house master absent")
def test_render_builds_pptx_from_a_spec(tmp_path):
    _fixture(tmp_path)
    fmt_dir = tmp_path / "slides" / "shorttalk"
    fmt_dir.mkdir(parents=True)
    (fmt_dir / "spec.json").write_text(json.dumps([
        {"role": "title", "title": "T", "subtitle": "S"},
        {"role": "figure", "title": "F", "figure": "stageLadder"}]))
    assert cli.main(["render", "--dir", str(tmp_path), "--format", "shorttalk"]) == 0
    from pptx import Presentation
    from haarpi import naming
    pptx = fmt_dir / naming.major_name("demo", "pptx", infix="deck")   # {date}_demo_deck_ra.pptx
    assert pptx.is_file() and len(Presentation(str(pptx)).slides) == 2


def test_render_without_a_spec_errors(tmp_path):
    _fixture(tmp_path)
    assert cli.main(["render", "--dir", str(tmp_path), "--format", "longtalk"]) == 1

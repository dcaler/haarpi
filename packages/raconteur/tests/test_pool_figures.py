"""raconteur consumes the shared figure pool (haarpi.figure) as a third figure source.

The framework schematic / experiment DAG the pipeline produced surface as author-origin figures the
outline places — no hand-maintained figures.yaml entry needed. Empty outside a HAARPi project.
"""

from __future__ import annotations

from haarpi import figure, project
from raconteur import context


def test_load_pool_figures_surfaces_conceptual_figures(tmp_path):
    project.save_manifest(project.Manifest(name="demo", short_title="demo", brief="x"), tmp_path)
    figure.write_figure(tmp_path, "demo", figure.stage_dag(project.DEFAULT_STAGES))

    figs = context.load_pool_figures(tmp_path)
    assert len(figs) == 1
    f = figs[0]
    assert f.origin == "author"                                  # conceptual → author-placed
    assert f.caption == "The HAARPi stage pipeline."             # read from the source header
    assert f.path.startswith("figures/") and (tmp_path / f.path).is_file()


def test_load_pool_figures_empty_outside_a_project(tmp_path):
    assert context.load_pool_figures(tmp_path) == []             # no haarpi.yaml above

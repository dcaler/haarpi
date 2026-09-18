#!/usr/bin/env python3
"""Rebuild every stage panel and the stitched drill-down, from source.

Run from anywhere: `python figures/panels/build.py`. Deterministic — the panels are laid
out arithmetically, not by a graph-layout engine, so the same source gives the same SVG.
"""
from __future__ import annotations
import importlib, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT, FIGS = HERE / "out", HERE.parent
def _panels_in_ladder_order():
    """Panels left-to-right in the order the LADDER runs, not the order filenames sort.

    Sorting by name put `stage2b_` before `stage2_`, and before that put the methods review
    immediately after the literature review — which draws the ordering that was tried and
    rejected, where nothing could spur the methods search. The drawing must not be able to
    contradict `project.DEFAULT_STAGES`; reading the order from it is the only way to be sure.
    """
    import importlib, sys
    sys.path.insert(0, str(HERE))
    by_stage = {}
    for fp in HERE.glob("stage[0-9]*_*.py"):
        mod = importlib.import_module(fp.stem)
        by_stage.setdefault(getattr(mod, "STAGE", fp.stem), fp.stem)
    from haarpi import project
    ordered = [by_stage.pop(s) for s in project.DEFAULT_STAGES if s in by_stage]
    return ordered + sorted(by_stage.values())      # anything unknown, last and stable


STAGES = _panels_in_ladder_order()


def cairosvg_png(src: Path, dst: Path, width: int) -> None:
    import cairosvg
    cairosvg.svg2png(url=str(src), write_to=str(dst), output_width=width)


def main() -> int:
    sys.path.insert(0, str(HERE))
    from _emitter import render
    OUT.mkdir(exist_ok=True)
    svgs = []
    for name in STAGES:
        target = OUT / f"{name}.svg"
        mod = importlib.import_module(name)
        render(str(target), rows_spec=mod.ROWS, spine=mod.SPINE, lane=mod.LANE,
                   arts=mod.ARTS, makes=mod.MAKES, band=mod.BAND, **getattr(mod, "OPTS", {}))
        svgs.append(str(target))

    # the flow map: a different shape, same visual language, same build
    import paperinflow as pf
    from _emitter import render_flow
    render_flow(str(FIGS / "paperInflow.svg"), sources=pf.SOURCES, sections=pf.SECTIONS,
                edges=pf.EDGES, digests=pf.DIGESTS, structure=pf.STRUCTURE, title=pf.TITLE,
                enclosure=pf.ENCLOSURE)
    cairosvg_png(FIGS / "paperInflow.svg", FIGS / "paperInflow.png", 2600)

    from _stitch import stitch
    combined = FIGS / "agentDrilldown.svg"
    stitch(svgs, combined)
    cairosvg_png(combined, FIGS / "agentDrilldown.png", 5400)
    print(f"  {FIGS / 'agentDrilldown.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

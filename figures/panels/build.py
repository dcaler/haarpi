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
STAGES = sorted(p.stem for p in HERE.glob("stage[0-9]_*.py"))


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

    from _stitch import stitch
    combined = FIGS / "agentDrilldown.svg"
    stitch(svgs, combined)
    import cairosvg
    cairosvg.svg2png(url=str(combined), write_to=str(FIGS / "agentDrilldown.png"),
                     output_width=5400)
    print(f"  {FIGS / 'agentDrilldown.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

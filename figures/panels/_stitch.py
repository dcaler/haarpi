#!/usr/bin/env python3
"""Compose the six stage panels into one figure.

Each panel is laid out on its own and nested here as an <svg> with its own viewBox, so its
internal coordinates are untouched — nothing to recompute, nothing to get subtly wrong. The
hand-offs are drawn ON TOP: a stage's last release drops to a bus below the panels, runs
right, and rises into the next stage's header. That is the thing a stitched figure says
which six separate pictures cannot — that a release is what unlocks the stage after it.
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path

GAP, MARGIN, HEAD = 26, 22, 96
INK, MINT = "#334155", "#4f46e5"
HANDOFF = [("the review",      "unlocks the design"),
           ("the prereg",      "unlocks the build"),
           ("the methods digest", "unlocks the experiments"),
           ("the findings",    "unlock the paper"),
           ("the submission",  "unlocks the deck")]

def panel(path):
    txt = Path(path).read_text(encoding="utf-8")
    body = txt[txt.index(">", txt.index("<svg")) + 1: txt.rindex("</svg>")]
    a = json.loads(Path(path).with_suffix(".anchors.json").read_text())
    return body, a

def stitch(paths, out):
    panels = [panel(p) for p in paths]
    ph = max(a["height"] for _b, a in panels)
    bus = HEAD + ph + 46                       # the hand-off bus, clear of every panel
    W = sum(a["width"] for _b, a in panels) + GAP * (len(panels) - 1) + MARGIN * 2
    H = bus + 40
    S = [f'<?xml version="1.0" encoding="UTF-8" standalone="no"?>',
         f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:.0f}pt" height="{H:.0f}pt" '
         f'viewBox="0 0 {W:.0f} {H:.0f}">',
         f'<rect width="100%" height="100%" fill="#ffffff"/>',
         f'<text x="{W/2:.0f}" y="42" text-anchor="middle" font-family="Helvetica,Arial,sans-serif" '
         f'font-size="27" font-weight="bold" fill="#0f172a">'
         f'Inside each agent — the process every stage runs, and who acts at each step</text>',
         f'<text x="{W/2:.0f}" y="68" text-anchor="middle" font-family="Helvetica,Arial,sans-serif" '
         f'font-size="12.5" fill="#334155">'
         f'amber = the Human’s step · indigo = the agent alone · purple = HAARPi, the '
         f'conductor · green = a clean gate  —  '
         f'solid = the work moves on · dashed grey = the step produces that artifact · '
         f'dashed purple = the revision cycle</text>']
    xs = []
    x = MARGIN
    for body, a in panels:
        xs.append(x)
        S.append(f'<svg x="{x:.1f}" y="{HEAD}" width="{a["width"]:.1f}" height="{a["height"]:.1f}" '
                 f'viewBox="0 0 {a["width"]:.1f} {a["height"]:.1f}" overflow="visible">{body}</svg>')
        x += a["width"] + GAP

    # the hand-offs, drawn over the panels
    for i in range(len(panels) - 1):
        (_b0, a0), (_b1, a1) = panels[i], panels[i + 1]
        x0 = xs[i] + a0["spine_cx"]
        y0 = HEAD + a0["exit_y"]
        x1 = xs[i + 1] + a1["spine_x"] - 18
        y1 = HEAD + a1["header_cy"]
        S.append(f'<path d="M{x0:.1f},{y0:.1f} V{bus:.1f} H{x1:.1f} V{y1:.1f} '
                 f'H{xs[i+1] + a1["spine_x"] - 9:.1f}" fill="none" stroke="{MINT}" '
                 f'stroke-width="2"/>')
        S.append(f'<polygon points="{xs[i+1]+a1["spine_x"]:.1f},{y1:.1f} '
                 f'{xs[i+1]+a1["spine_x"]-9:.1f},{y1-4.5:.1f} '
                 f'{xs[i+1]+a1["spine_x"]-9:.1f},{y1+4.5:.1f}" fill="{MINT}"/>')
        lab_a, lab_b = HANDOFF[i]
        S.append(f'<text x="{(x0+x1)/2:.1f}" y="{bus-8:.1f}" text-anchor="middle" '
                 f'font-family="Helvetica,Arial,sans-serif" font-size="11" font-weight="bold" '
                 f'fill="{MINT}">{lab_a} {lab_b}</text>')
    S.append("</svg>")
    Path(out).write_text("\n".join(S), encoding="utf-8")
    print(f"{out}  {W:.0f}x{H:.0f}pt  ({len(panels)} panels)")

if __name__ == "__main__":
    stitch(sys.argv[2:], sys.argv[1])

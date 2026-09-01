#!/usr/bin/env python3
"""The stage-panel SVG emitter, shared by every panel.

These panels are a GRID, not a graph: three fixed columns, one row per step, an artifact
opposite its producer, a band from row i to row j. Handing that to a layout engine meant
specifying every position anyway -- through invisible placeholders, ordering edges, spacer
columns and cluster-membership rules -- and then losing to its edge router regardless
(graphviz 2.43's ortho ignores ports and never clips at a node boundary, so every line
started at a box CENTRE and crossed whatever lay between).

Here the geometry is arithmetic. A line starts where I say it starts.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

# ── geometry ──────────────────────────────────────────────────────────────────
PAD_X, PAD_Y = 10, 8
LH          = 12.6          # line height at 9.5pt
FS          = 9.5
MARGIN      = 18
COL_GAP     = 34
ROW_GAP     = 20
LANE_W, SPINE_W, OUT_W = 258, 330, 176
LANE_X  = MARGIN
SPINE_X = LANE_X + LANE_W + COL_GAP
GUTTER  = SPINE_X + SPINE_W + COL_GAP / 2      # where vertical artifact runs live
OUT_X   = SPINE_X + SPINE_W + COL_GAP
WIDTH   = OUT_X + OUT_W + MARGIN

INK, GREY, PURPLE, GREEN = "#334155", "#a8a29e", "#7c3aed", "#16a34a"
STYLES = {
 "amber":  ("#fde68a", "#b45309", 2),
 "indigo": ("#eef2ff", "#94a3b8", 1),
 "purple": ("#ede9fe", "#7c3aed", 2),
 "head":   ("#e2e8f0", "#64748b", 1),
 "art":    ("#e2e8f0", "#94a3b8", 1),
 "mint":   ("#c7d2fe", "#4f46e5", 1.5),
}


_NARROW, _WIDE, _CAP, _DIG = set("iljItf.,;:'!|()[]{}/-· "), set("mwMW@%"), None, set("0123456789")

def _char_w(c, fs):
    if c in _NARROW: return fs * 0.30
    if c in _WIDE:   return fs * 0.86
    if c in _DIG:    return fs * 0.56
    if c.isupper():  return fs * 0.70
    return fs * 0.545

def text_w(s, fs=FS): return sum(_char_w(c, fs) for c in s)

def wrap(paras, box_w, fs=FS):
    """Greedy wrap to the usable width. `paras` may be a string or a list of strings; each
    element is wrapped independently, so an intentional break (an artifact's second line,
    say) survives while everything else reflows to whatever the column width is."""
    if isinstance(paras, str): paras = [paras]
    avail, out = box_w - 2 * PAD_X, []
    for para in paras:
        line, words = "", para.split()
        if not words: out.append(""); continue
        for w in words:
            trial = f"{line} {w}".strip()
            if line and text_w(trial, fs) > avail:
                out.append(line); line = w
            else:
                line = trial
        out.append(line)
    return out

def esc(t): return t.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

def box_h(lines): return len(lines) * LH + 2 * PAD_Y

def rrect(x, y, w, h, style, r=7):
    fill, stroke, sw = STYLES[style]
    return (f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{r}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>')

def _folder_path(x, y, w, h, style):
    """A folder: full-height tab on the left, body top stepped down. One closed path, so the
    outline is continuous and there is no seam where a tab rect would overlap a body rect."""
    fill, stroke, sw = STYLES[style]
    tab = min(64, w * 0.30)
    return (f'<path d="M{x:.1f},{y+h:.1f} L{x:.1f},{y:.1f} L{x+tab:.1f},{y:.1f} '
            f'L{x+tab+7:.1f},{y+9:.1f} L{x+w:.1f},{y+9:.1f} L{x+w:.1f},{y+h:.1f} Z" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}" stroke-linejoin="round"/>')

def folder(x, y, w, h):      return _folder_path(x, y, w, h, "art")
def mint_folder(x, y, w, h): return _folder_path(x, y, w, h, "mint")

def release(x, y, w, h):
    """The mint: a double outline, so a release is visibly not just another step."""
    fill, stroke, _ = STYLES["mint"]
    return (rrect(x, y, w, h, "mint", r=10).replace('stroke-width="1.5"', 'stroke-width="1.5"')
            + f'<rect x="{x+4:.1f}" y="{y+4:.1f}" width="{w-8:.1f}" height="{h-8:.1f}" rx="7" '
              f'fill="none" stroke="{stroke}" stroke-width="1"/>')

def text(cx, y0, lines, bold=False, fs=FS, fill="#0f172a"):
    out = []
    for i, ln in enumerate(lines):
        out.append(f'<text x="{cx:.1f}" y="{y0 + PAD_Y + LH*(i+0.78):.1f}" text-anchor="middle" '
                   f'font-family="Helvetica,Arial,sans-serif" font-size="{fs}" '
                   f'{"font-weight=\"bold\" " if bold else ""}fill="{fill}">{esc(ln)}</text>')
    return "".join(out)

ARROW = 5.0
def head(x, y, d):
    """A filled triangle at the LINE's end -- so it lands on the boundary, not past it."""
    if d == "down":  p = f"{x},{y} {x-ARROW},{y-ARROW*1.8} {x+ARROW},{y-ARROW*1.8}"
    elif d == "up":  p = f"{x},{y} {x-ARROW},{y+ARROW*1.8} {x+ARROW},{y+ARROW*1.8}"
    elif d == "right": p = f"{x},{y} {x-ARROW*1.8},{y-ARROW} {x-ARROW*1.8},{y+ARROW}"
    else:            p = f"{x},{y} {x+ARROW*1.8},{y-ARROW} {x+ARROW*1.8},{y+ARROW}"
    return f'<polygon points="{p}" fill="currentColor"/>'

def path(d, colour, dash=None, w=1.4):
    ds = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<g color="{colour}"><path d="{d}" fill="none" stroke="{colour}" '
            f'stroke-width="{w}"{ds}/>')


# ── the emitter ───────────────────────────────────────────────────────────────

def render(out_path, *, rows_spec, spine, lane, arts, makes, band, gate_key="gate",
           release_key="rel", header_key="hdr", gate_label="all comments resolved",
           # a stage may mint more than once (raconteur climbs three rungs), so this
           # takes either one key or a collection of them
           release_keys=None,
           hop_labels=None, loops=(), gate_exits=None):
    """`band` is (start_row, end_row) or a list of them — a stage that gates more than once
    gets one band per cycle, since each gate sends work back only into its OWN rung.
    `gate_exits` maps a gate key to the band index it points into; it defaults to the single
    gate pointing at the single band.
    `hop_labels` labels the arrow LEAVING a given spine step: {key: (text, colour)}.
    `loops` are back-edges up the spine, [(from_key, to_key, [lines])], routed in the gap
    between the lane and the spine so they never cross a box."""
    rel_keys = set(release_keys) if release_keys else {release_key}
    hop_labels = dict(hop_labels or {})
    hop_labels.setdefault(gate_key, (gate_label, GREEN))
    """One panel. `rows_spec` is [(spine_key|None, lane_key|None, artifact_key|None), ...] —
    one row per step, with None wherever a column is empty on that row."""
    spine = {k: (st, wrap(v, SPINE_W)) for k, (st, v) in spine.items()}
    lane  = {k: wrap(v, LANE_W) for k, v in lane.items()}
    arts  = {k: wrap(v, OUT_W, 8.6) for k, v in arts.items()}

    def box_lines(which, key):
        return spine[key][1] if which == "sp" else (lane[key] if which == "ln" else arts[key])

    rows, y = [], MARGIN + 34
    for sp, ln, ar in rows_spec:
        h = max([box_h(spine[sp][1]) if sp else 0,
                 box_h(lane[ln]) if ln else 0,
                 box_h(arts[ar]) if ar else 0, 30])
        rows.append({"y": y, "h": h, "sp": sp, "ln": ln, "ar": ar})
        y += h + ROW_GAP
    height = y - ROW_GAP + MARGIN

    def cell(r, which):
        key = r[which]
        x, w = ((SPINE_X, SPINE_W) if which == "sp" else
                (LANE_X, LANE_W) if which == "ln" else (OUT_X, OUT_W))
        h = box_h(box_lines(which, key))
        return x, r["y"] + (r["h"] - h) / 2, w, h

    S = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH:.0f}pt" '
         f'height="{height:.0f}pt" viewBox="0 0 {WIDTH:.0f} {height:.0f}">',
         '<rect width="100%" height="100%" fill="#ffffff"/>']

    # the bands first, so every box sits on top of them
    bands = [band] if isinstance(band[0], int) else list(band)
    band_bottom = []
    for a, b in bands:
        y0, rb = rows[a]["y"] - 12, rows[b]
        y1 = rb["y"] + rb["h"] + 12
        band_bottom.append(y1)
        S.append(f'<rect x="{MARGIN-6:.1f}" y="{y0:.1f}" width="{WIDTH-2*MARGIN+12:.1f}" '
                 f'height="{y1-y0:.1f}" rx="10" fill="none" stroke="{PURPLE}" '
                 f'stroke-width="1.5" stroke-dasharray="7,5"/>')
        S.append(f'<text x="{MARGIN+2:.1f}" y="{y0-5:.1f}" '
                 f'font-family="Helvetica,Arial,sans-serif" font-size="11.5" '
                 f'font-weight="bold" fill="{PURPLE}">revisions</text>')

    row_of = {}
    for i, r in enumerate(rows):
        for k in ("sp", "ln", "ar"):
            if r[k]: row_of[r[k]] = i

    # the spine: one arrow per hop, skipping rows with no spine box
    spine_rows = [i for i, r in enumerate(rows) if r["sp"]]
    cx = SPINE_X + SPINE_W / 2
    for a, b in zip(spine_rows, spine_rows[1:]):
        _x, ay, _w, ah = cell(rows[a], "sp"); _bx, byy, _bw, _bh = cell(rows[b], "sp")
        lab = hop_labels.get(rows[a]["sp"])
        colour = lab[1] if lab else INK
        S.append(path(f"M{cx:.1f},{ay+ah:.1f} V{byy-ARROW*1.8:.1f}", colour))
        S.append(head(cx, byy, "down") + "</g>")
        if lab:
            S.append(f'<text x="{cx+8:.1f}" y="{(ay+ah+byy)/2+3:.1f}" '
                     f'font-family="Helvetica,Arial,sans-serif" font-size="9" '
                     f'fill="{colour}">{esc(lab[0])}</text>')

    # artifacts: out of the producer's EAST edge, down the gutter, into the artifact's WEST edge
    for s_key, a_key in makes:
        rs, ra = rows[row_of[s_key]], rows[row_of[a_key]]
        sx, sy, sw, sh = cell(rs, "sp"); ax, ay, _aw, ah = cell(ra, "ar")
        y0, y1 = sy + sh / 2, ay + ah / 2
        d = (f"M{sx+sw:.1f},{y0:.1f} H{GUTTER:.1f} V{y1:.1f} H{ax-ARROW*1.8:.1f}"
             if abs(y0 - y1) > 1 else f"M{sx+sw:.1f},{y0:.1f} H{ax-ARROW*1.8:.1f}")
        S.append(path(d, GREY, dash="5,4", w=1.2))
        S.append(head(ax, y1, "right") + "</g>")

    # back-edges up the spine, routed in the lane/spine gap so they cross nothing
    loop_x = SPINE_X - COL_GAP / 2
    for f_key, t_key, lines in loops:
        rf, rt = rows[row_of[f_key]], rows[row_of[t_key]]
        fx, fy, _fw, fh = cell(rf, "sp"); tx, ty, _tw, th = cell(rt, "sp")
        y0, y1 = fy + fh / 2, ty + th / 2
        S.append(path(f"M{fx:.1f},{y0:.1f} H{loop_x:.1f} V{y1:.1f} H{tx-ARROW*1.8:.1f}",
                      GREEN, w=1.4))
        S.append(head(tx, y1, "right") + "</g>")
        for i, ln in enumerate(lines):
            S.append(f'<text x="{loop_x-6:.1f}" y="{(y0+y1)/2 + LH*(i-0.2):.1f}" '
                     f'text-anchor="end" font-family="Helvetica,Arial,sans-serif" '
                     f'font-size="9" fill="{GREEN}">{esc(ln)}</text>')

    # each gate's other exit: west, then up into ITS band's bottom edge
    lane_cx = LANE_X + LANE_W / 2
    for g_key, b_i in (gate_exits or {gate_key: 0}).items():
        gr = rows[row_of[g_key]]
        gx, gy, _gw, gh = cell(gr, "sp")
        y1 = band_bottom[b_i]
        S.append(path(f"M{gx:.1f},{gy+gh/2:.1f} H{lane_cx:.1f} V{y1+ARROW*1.8:.1f}",
                      PURPLE, dash="7,5", w=1.6))
        S.append(head(lane_cx, y1, "up") + "</g>")

    for r in rows:
        if r["sp"]:
            style, lines = spine[r["sp"]]
            x, yy, w, h = cell(r, "sp")
            S.append(release(x, yy, w, h) if r["sp"] in rel_keys
                     else rrect(x, yy, w, h, style))
            S.append(text(x + w/2, yy, lines, bold=(r["sp"] == header_key or r["sp"] in rel_keys)))
        if r["ln"]:
            x, yy, w, h = cell(r, "ln")
            S.append(rrect(x, yy, w, h, "indigo"))
            S.append(text(x + w/2, yy, lane[r["ln"]]))
        if r["ar"]:
            x, yy, w, h = cell(r, "ar")
            S.append(mint_folder(x, yy, w, h) if "mint" in r["ar"]
                     else folder(x, yy, w, h))
            S.append(text(x + w/2, yy + 4, arts[r["ar"]], fs=8.6))

    S.append("</svg>")
    Path(out_path).write_text("\n".join(S), encoding="utf-8")

    # anchors for the stitcher: where a hand-off leaves this stage and where it arrives.
    # Written beside the .svg so composing does not have to re-derive the layout.
    hdr_row = rows[0]
    _hx, hy, _hw, hh = cell(hdr_row, "sp")
    last = [r for r in rows if r["sp"]][-1]
    _lx, ly, _lw, lh = cell(last, "sp")
    anchors = {"width": WIDTH, "height": height, "spine_cx": SPINE_X + SPINE_W / 2,
               "spine_x": SPINE_X, "header_cy": hy + hh / 2, "exit_y": ly + lh,
               "exit_key": last["sp"]}
    Path(out_path).with_suffix(".anchors.json").write_text(json.dumps(anchors), encoding="utf-8")
    print(f"{out_path}  {WIDTH:.0f}x{height:.0f}pt  ({len(rows)} rows)")
    return anchors


# ── the information-flow map ──────────────────────────────────────────────────
# A different SHAPE from a stage panel — bipartite, not a grid — so it gets its own layout,
# sharing the primitives and the visual language above. Sources on the left, the manuscript's
# sections on the right, one lane per edge in the gutter between, and exit/entry points
# staggered within a box so several edges off one source never overlap.

FLOW_W_SRC, FLOW_W_SEC, FLOW_GUT = 250, 190, 360
KIND = {"prose":  (INK,    None,  1.5),
        "asset":  (GREY,   "5,4", 1.3),
        "digest": ("#94a3b8", "2,3", 1.2)}


def render_flow(out_path, *, sources, sections, edges, digests=(), structure=(), title="",
                enclosure=""):
    """`edges` are (source_key, section_key, kind, label); `digests` are (section, section)
    pairs routed up the EAST side, for sections summarised into another.

    `enclosure` draws a folder around the whole section column and labels it: the sections are
    not free-standing boxes, they are the inside of ONE artifact, and everything on the left
    feeds into that artifact rather than into eight separate ones."""
    sx, gx = MARGIN, MARGIN + FLOW_W_SRC
    secx = gx + FLOW_GUT
    ENC = 16 if enclosure else 0               # padding between the folder and its sections
    width = secx + FLOW_W_SEC + ENC + 150      # room for the east-side digest lanes
    head_h = 62 if title else MARGIN

    def stack(items, w, fs, x, y0):
        out, y = {}, y0
        for k, (lines, style) in items.items():
            ls = wrap(lines, w, fs)
            h = len(ls) * LH + 2 * PAD_Y
            out[k] = {"x": x, "y": y, "w": w, "h": h, "lines": ls, "style": style}
            y += h + 16
        return out, y

    S_, y1 = stack(sources, FLOW_W_SRC, 8.8, sx, head_h + 26)
    C_, y2 = stack(sections, FLOW_W_SEC, 9.5, secx, head_h + 26)
    height = max(y1, y2) + MARGIN

    def port(box, n, i):
        """Spread n connections evenly down a box's face, so they never share a point."""
        return box["y"] + box["h"] * (i + 1) / (n + 1)

    outs, ins = {}, {}
    for a, b, *_ in edges:
        outs.setdefault(a, []).append(b); ins.setdefault(b, []).append(a)

    S = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}pt" '
         f'height="{height:.0f}pt" viewBox="0 0 {width:.0f} {height:.0f}">',
         '<rect width="100%" height="100%" fill="#ffffff"/>']
    if title:
        S.append(f'<text x="{width/2:.0f}" y="34" text-anchor="middle" '
                 f'font-family="Helvetica,Arial,sans-serif" font-size="19" font-weight="bold" '
                 f'fill="#0f172a">{esc(title)}</text>')

    # the enclosing folder, drawn BEFORE the sections so they sit on top of it
    if enclosure:
        first, last = next(iter(C_.values())), list(C_.values())[-1]
        fx, fw = secx - ENC, FLOW_W_SEC + 2 * ENC
        fy = first["y"] - ENC - 16
        fh = last["y"] + last["h"] + ENC - fy
        tab = min(150, fw * 0.42)
        S.append(f'<path d="M{fx:.1f},{fy+fh:.1f} L{fx:.1f},{fy:.1f} L{fx+tab:.1f},{fy:.1f} '
                 f'L{fx+tab+9:.1f},{fy+13:.1f} L{fx+fw:.1f},{fy+13:.1f} '
                 f'L{fx+fw:.1f},{fy+fh:.1f} Z" fill="#f8fafc" stroke="#94a3b8" '
                 f'stroke-width="1.4" stroke-linejoin="round"/>')
        S.append(f'<text x="{fx+9:.1f}" y="{fy+10:.1f}" '
                 f'font-family="Helvetica,Arial,sans-serif" font-size="8.6" '
                 f'font-weight="bold" fill="#475569">{esc(enclosure)}</text>')

    # lanes: ordered by target then source, so the bundle fans without crossing itself
    order = sorted(range(len(edges)),
                   key=lambda i: (list(C_).index(edges[i][1]), list(S_).index(edges[i][0])))
    for slot, i in enumerate(order):
        a, b, kind, label = edges[i]
        col, dash, w = KIND[kind]
        A, B = S_[a], C_[b]
        y0 = port(A, len(outs[a]), outs[a].index(b))
        y1_ = port(B, len(ins[b]), ins[b].index(a))
        lane = gx + 26 + (FLOW_GUT - 52) * (slot + 0.5) / len(edges)
        S.append(path(f"M{A['x']+A['w']:.1f},{y0:.1f} H{lane:.1f} V{y1_:.1f} "
                      f"H{B['x']-ARROW*1.8:.1f}", col, dash=dash, w=w))
        S.append(head(B["x"], y1_, "right") + "</g>")
        if label:
            # anchored at the edge's own EXIT point, not the lane midpoint: exits are already
            # staggered down each source's face, so the labels inherit that separation. Placing
            # them all at mid-lane put thirteen of them on top of each other.
            S.append(f'<text x="{A["x"]+A["w"]+7:.1f}" y="{y0-3:.1f}" '
                     f'font-family="Helvetica,Arial,sans-serif" font-size="8" '
                     f'fill="{col}">{esc(label)}</text>')

    # sections summarised into another section, routed up the EAST side
    for j, (a, b) in enumerate(digests):
        A, B = C_[a], C_[b]
        col, dash, w = KIND["digest"]
        lane = secx + FLOW_W_SEC + ENC + 16 + j * 15
        S.append(path(f"M{A['x']+A['w']:.1f},{A['y']+A['h']/2:.1f} H{lane:.1f} "
                      f"V{B['y']+B['h']/2:.1f} H{B['x']+B['w']+ARROW*1.8:.1f}", col,
                      dash=dash, w=w))
        S.append(head(B["x"] + B["w"], B["y"] + B["h"] / 2, "left") + "</g>")

    # the structure chain among the sources themselves
    for a, b, label in structure:
        A, B = S_[a], S_[b]
        S.append(path(f"M{A['x']+A['w']/2:.1f},{A['y']+A['h']:.1f} "
                      f"V{B['y']-ARROW*1.8:.1f}", INK, w=1.5))
        S.append(head(A["x"] + A["w"] / 2, B["y"], "down") + "</g>")
        S.append(f'<text x="{A["x"]+A["w"]/2+6:.1f}" y="{(A["y"]+A["h"]+B["y"])/2+3:.1f}" '
                 f'font-family="Helvetica,Arial,sans-serif" font-size="8" '
                 f'fill="{INK}">{esc(label)}</text>')

    for box in list(S_.values()):
        S.append(mint_folder(box["x"], box["y"], box["w"], box["h"])
                 if box["style"] == "mint" else folder(box["x"], box["y"], box["w"], box["h"]))
        S.append(text(box["x"] + box["w"] / 2, box["y"] + 4, box["lines"], fs=8.8))
    for box in C_.values():
        S.append(rrect(box["x"], box["y"], box["w"], box["h"], box["style"]))
        S.append(text(box["x"] + box["w"] / 2, box["y"], box["lines"]))

    S.append("</svg>")
    Path(out_path).write_text("\n".join(S), encoding="utf-8")
    print(f"{out_path}  {width:.0f}x{height:.0f}pt  ({len(edges)} flows)")

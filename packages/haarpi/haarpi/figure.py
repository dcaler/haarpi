"""haarpi.figure — the shared diagram-as-code figure engine (see DESIGN_figure_engine.md).

Figures are first-class **revision-chain artifacts**, resolved through `haarpi.naming` exactly like
every other deliverable — no side index. The engine authors a tool draft (`…_<figid>_ra.svg`); a
human polishing it in Inkscape saves the same trailing-initials way (`…_<figid>_ra_DCR.svg`); an
accepted figure is a token-free release consumers bind. The engine only ever writes `_ra`, so it can
never clobber a hand-edit — the naming convention IS the guard.

SVG is the canonical render (graphviz `dot -Tsvg`); PNG is a derived export (cairosvg) from whatever
SVG is authoritative — so downstream always gets your polished version. Deterministic emitters turn
structured project data into diagram SOURCE with no model in the loop. Rendering is best-effort — a
missing renderer keeps the source and warns, exactly as a missing pandoc never blocks a docx.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

from . import naming


def _log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[figure {ts}] {msg}", file=sys.stderr)


# Source-file extension per format. A figure id is a SINGLE chain token (no `_`, the chain separator).
_SRC_EXT = {"dot": "dot", "mermaid": "mmd", "tikz": "tex", "matplotlib": "py"}


@dataclass
class FigureSpec:
    id: str                         # a single chain token, e.g. "stageLadder"
    kind: str                       # dag | flowchart | schematic | graph | plot
    format: str                     # dot | mermaid | tikz | matplotlib
    source: str                     # the diagram code, header comments included
    caption: str = ""
    provenance: dict = field(default_factory=dict)
    born_stage: str = ""


# ── deterministic emitters (no LLM — the figure IS the structured data) ────────

def _q(s: str, limit: int = 60) -> str:
    """A DOT-quoted-string body: escape quotes/backslashes, flatten newlines, bound length."""
    return str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").strip()[:limit]


def _dot_header(fig_id: str, caption: str, provenance: dict) -> str:
    """`//` comment header carrying the metadata that used to live in a side index — the source
    file is the durable artifact, so its provenance rides with it. dot ignores `//`."""
    lines = [f"// figure: {fig_id}"]
    if caption:
        lines.append(f"// caption: {caption}")
    if provenance:
        lines.append("// provenance: " + ", ".join(f"{k}={v}" for k, v in provenance.items()))
    return "\n".join(lines) + "\n"


_NODE = 'node [shape=box, style="rounded,filled", fillcolor="#eef2ff", fontname="Helvetica"];'
_EDGE = 'edge [color="#64748b"];'


def stage_dag(stages: dict, *, fig_id: str = "stageLadder") -> FigureSpec:
    """The pipeline ladder as a Graphviz DAG, straight from a manifest's `stages` dict. Always
    correct — it is the dependency graph, not a depiction of it."""
    prov = {"mode": "deterministic", "from": "manifest.stages"}
    body = [f"digraph {fig_id} {{", "  rankdir=LR;", f"  {_NODE}", f"  {_EDGE}"]
    for name in stages:
        body.append(f'  "{_q(name)}";')
    for name, spec in stages.items():
        for inp in spec.get("inputs", []) or []:
            if inp in stages:
                body.append(f'  "{_q(inp)}" -> "{_q(name)}";')
    body.append("}")
    src = _dot_header(fig_id, "The HAARPi stage pipeline.", prov) + "\n".join(body) + "\n"
    return FigureSpec(id=fig_id, kind="dag", format="dot", source=src,
                      caption="The HAARPi stage pipeline.", provenance=prov, born_stage="")


def experiment_dag(experiments: list, *, fig_id: str = "experimentDag") -> FigureSpec:
    """Each experiment → its preregistered outputs, as a Graphviz DAG from experiments.yaml's
    `experiments:` list. What this cycle produces, derived — never invented."""
    prov = {"mode": "deterministic", "from": "experiments.yaml"}
    body = [f"digraph {fig_id} {{", "  rankdir=LR;", f"  {_NODE}", f"  {_EDGE}"]
    for exp in experiments or []:
        eid = str(exp.get("id", "E?"))
        title = _q(exp.get("title") or exp.get("question") or "", 32)
        body.append(f'  "{_q(eid)}" [label="{_q(eid, 8)}: {title}"];')
        for i, out in enumerate(exp.get("outputs", []) or []):
            kind = out.get("kind", "output")
            label = _q(out.get("caption") or out.get("name") or kind, 30)
            on = f"{_q(eid, 8)}_o{i}"
            body.append(f'  "{on}" [style=rounded, fillcolor="#ffffff", label="{kind}: {label}"];')
            body.append(f'  "{_q(eid)}" -> "{on}";')
    body.append("}")
    src = _dot_header(fig_id, "Experiments and their preregistered outputs.", prov) + "\n".join(body) + "\n"
    return FigureSpec(id=fig_id, kind="dag", format="dot", source=src,
                      caption="Experiments and their preregistered outputs.", provenance=prov,
                      born_stage="experiments")


# ── conceptual figures: the LLM authors DOT source, grounded + validated ───────
# For the figures no structured file can derive — a framework schematic, a mechanism diagram.
# The model writes CODE (DOT), never pixels; the result is validated (it must compile) before it
# lands, and its provenance records that a human still approves it wherever it's embedded.

_SYS = ("You draw figures as Graphviz DOT source. You output DOT only — never prose, never an "
        "image. Ground every node and edge in the provided context; invent nothing.")

_COMPOSE_PROMPT = """Draw this figure as Graphviz DOT.

Figure requested: {request}

Context to ground it in (use ONLY what is here; do not invent entities or relationships):
{context}

Output ONLY a single fenced ```dot code block containing one valid `digraph`:
- `rankdir=LR` unless a top-down flow reads better;
- concise node labels (a few words); edges = real relationships from the context;
- no prose before or after the block."""

_REPAIR_PROMPT = ("This Graphviz DOT did not compile:\n\n{dot}\n\n"
                  "Return a corrected version as a single ```dot code block — valid `digraph`, "
                  "nothing else.")

_DOT_FENCE = re.compile(r"```(?:dot|graphviz)?\s*(.*?)```", re.S | re.I)
_DOT_BARE = re.compile(r"((?:di)?graph\s+\w*\s*\{.*\})", re.S)


def _extract_dot(reply: str) -> str | None:
    """Pull a DOT `digraph`/`graph` from a model reply — a fenced ```dot block, else a bare one."""
    if not reply:
        return None
    for block in _DOT_FENCE.findall(reply):
        if re.search(r"(?:di)?graph\s", block):
            return block.strip()
    m = _DOT_BARE.search(reply)
    return m.group(1).strip() if m else None


def _dot_validates(source: str) -> bool:
    """Does this DOT compile? Best-effort — without graphviz we can't check, so we assume yes."""
    if not _have("dot"):
        return True
    r = subprocess.run(["dot", "-Tsvg"], input=source, capture_output=True, text=True)
    return r.returncode == 0 and bool(r.stdout)


def compose(brain, request: str, context: str = "", *, fig_id: str, kind: str = "schematic",
            model: str = "", repair: bool = True) -> FigureSpec:
    """The LLM authors the figure as DOT, grounded in `context`. The output is EXTRACTED and
    VALIDATED (it must compile); one repair pass on failure; and on total failure a labelled stub is
    emitted (never a crash) for the human to fix. Provenance marks it conceptual — a human approves
    it wherever it's embedded."""
    reply = brain.coordinator(_COMPOSE_PROMPT.format(request=request,
                                                     context=(context or "(none provided)")[:6000]),
                              _SYS)
    dot = _extract_dot(reply)
    if dot and not _dot_validates(dot) and repair:
        dot2 = _extract_dot(brain.coordinator(_REPAIR_PROMPT.format(dot=dot), _SYS))
        if dot2 and _dot_validates(dot2):
            dot = dot2
    if not dot or not _dot_validates(dot):
        _log(f"compose: no valid DOT for {fig_id} — emitting a labelled stub for you to fix")
        dot = f'digraph {fig_id} {{ "compose_failed" [shape=note, label="TODO: {request[:40]}"]; }}'
        prov = {"mode": "conceptual", "status": "stub", "request": request[:80]}
    else:
        prov = {"mode": "conceptual", "request": request[:80]}
        if model:
            prov["model"] = model
    src = _dot_header(fig_id, request[:80], prov) + dot + "\n"
    return FigureSpec(id=fig_id, kind=kind, format="dot", source=src, caption=request[:80],
                      provenance=prov)


class FigurePolicy(Protocol):
    """Per-tool I/O for conceptual figures: the brain that authors, the context that grounds, the
    log. The engine owns extraction/validation/naming; the policy owns where the grounding and the
    model come from (rayleigh's design prose, raster's DESIGN.md, …)."""
    def brain(self): ...
    def context(self, request: str) -> str: ...
    def log(self, msg: str) -> None: ...


def run_compose(policy: FigurePolicy, request: str, *, fig_id: str, kind: str = "schematic") -> FigureSpec:
    """Author a conceptual figure through a tool's policy: gather grounding, author + validate DOT."""
    return compose(policy.brain(), request, policy.context(request), fig_id=fig_id, kind=kind)


# ── rendering: source → canonical SVG, and SVG → PNG export (best-effort) ───────

def _have(tool: str) -> bool:
    return shutil.which(tool) is not None


def render(spec: FigureSpec, out_svg: Path) -> Path | None:
    """Render the figure SOURCE to the canonical SVG. Best-effort: a missing/failing renderer keeps
    the source and returns None (a consumer can render later)."""
    out_svg.parent.mkdir(parents=True, exist_ok=True)
    if spec.format == "dot":
        if not _have("dot"):
            _log(f"dot absent — kept {spec.id} source, no SVG (apt install graphviz)")
            return None
        r = subprocess.run(["dot", "-Tsvg"], input=spec.source, capture_output=True, text=True)
        if r.returncode != 0 or not r.stdout:
            _log(f"dot failed for {spec.id}: {r.stderr.strip()[:200]}")
            return None
        out_svg.write_text(r.stdout)
        return out_svg
    if spec.format == "mermaid":
        if not _have("mmdc"):
            _log(f"mmdc absent — kept {spec.id} source, no SVG")
            return None
        src = out_svg.with_suffix(".mmd")
        src.write_text(spec.source)
        r = subprocess.run(["mmdc", "-i", str(src), "-o", str(out_svg)], capture_output=True, text=True)
        if r.returncode != 0 or not out_svg.exists():
            _log(f"mmdc failed for {spec.id}: {r.stderr.strip()[:200]}")
            return None
        return out_svg
    _log(f"no renderer wired for format {spec.format!r} — kept {spec.id} source")
    return None


def export_png(svg: Path, out_png: Path, width: int = 1600) -> Path | None:
    """Rasterise a canonical SVG to PNG at a target width — cairosvg (in-stack), else rsvg-convert.
    Best-effort: no rasteriser → keep the SVG, return None."""
    out_png.parent.mkdir(parents=True, exist_ok=True)
    try:
        import cairosvg
        cairosvg.svg2png(url=str(svg), write_to=str(out_png), output_width=width)
        return out_png
    except Exception:
        pass
    if _have("rsvg-convert"):
        r = subprocess.run(["rsvg-convert", "-w", str(width), "-o", str(out_png), str(svg)],
                           capture_output=True)
        if r.returncode == 0 and out_png.exists():
            return out_png
    _log(f"no SVG→PNG rasteriser (pip install cairosvg) — kept {svg.name}")
    return None


# ── the pool: chain-named files, resolved through haarpi.naming ─────────────────

def pool_dir(root: Path) -> Path:
    return root / "figures"


def _figure_datestamp(figdir: Path, short_title: str, fig_id: str) -> str:
    """Reuse the datestamp of any existing file for this figure, so a re-derive OVERWRITES the `_ra`
    draft rather than piling up a new dated one each render. New figure → today."""
    newest: tuple[float, str] | None = None
    if figdir.is_dir():
        for p in figdir.glob("*"):
            r = naming.parse(p, short_title)
            if r and fig_id in r[1]:
                t = p.stat().st_mtime
                if newest is None or t > newest[0]:
                    newest = (t, r[0])
    return newest[1] if newest else naming.today()


def _has_finished_version(figdir: Path, short_title: str, fig_id: str) -> bool:
    """A hand-edited (chain ends non-`ra`) or released (token-free) SVG exists — so a regenerated
    `_ra` draft is NOT the authoritative version, and we should say so."""
    for p in figdir.glob("*.svg"):
        r = naming.parse(p, short_title)
        if not r or fig_id not in r[1]:
            continue
        chain = r[1]
        if naming.is_release(chain) or (chain and chain[-1].lower() != "ra"):
            return True
    return False


def write_figure(root: Path, short_title: str, spec: FigureSpec, *, render_svg: bool = True) -> dict:
    """Author the figure as a tool draft on the revision chain: write `{ds}_{short}_{id}_ra.<fmt>`
    source + (best-effort) `…_ra.svg`. Only ever writes `_ra`, so it can never clobber a `_DCR`
    hand-edit or a release. Returns the written paths."""
    figdir = pool_dir(root)
    figdir.mkdir(parents=True, exist_ok=True)
    ds = _figure_datestamp(figdir, short_title, spec.id)
    src_ext = _SRC_EXT.get(spec.format, "txt")
    src_path = figdir / naming.minor_name(short_title, [spec.id], src_ext, datestamp=ds)
    svg_path = figdir / naming.minor_name(short_title, [spec.id], "svg", datestamp=ds)
    src_path.write_text(spec.source)
    svg = render(spec, svg_path) if render_svg else None
    if _has_finished_version(figdir, short_title, spec.id):
        _log(f"note: {spec.id} has a hand-edited or released version — refreshed the _ra draft; your "
             f"version stays authoritative (consumers bind it). Re-render deliberately if you want it.")
    return {"id": spec.id, "datestamp": ds, "source": src_path, "svg": svg}


def _resolve_svg(figdir: Path, short_title: str, fig_id: str) -> Path | None:
    """The authoritative SVG for a figure, by the pipeline's own precedence:
    a minted release > your newest hand-edit > the tool's `_ra` draft."""
    release = naming.find_latest_release(figdir, short_title, "svg", chain_includes=fig_id)
    if release is not None:
        return release
    best_human: tuple[float, Path] | None = None
    best_ra: tuple[float, Path] | None = None
    for p in figdir.glob("*.svg"):
        r = naming.parse(p, short_title)
        if not r or fig_id not in r[1] or naming.is_release(r[1]):
            continue
        t = p.stat().st_mtime
        if r[1] and r[1][-1].lower() != "ra":               # human-touched (…_ra_DCR.svg)
            best_human = (t, p) if best_human is None or t > best_human[0] else best_human
        else:                                               # the tool draft (…_ra.svg)
            best_ra = (t, p) if best_ra is None or t > best_ra[0] else best_ra
    return (best_human or best_ra or (0, None))[1]


def list_ids(root: Path, short_title: str) -> list[str]:
    """The distinct figure ids present in the pool (the leading chain token of each figure file).
    What a consumer enumerates to embed everything the pipeline produced."""
    figdir = pool_dir(root)
    if not figdir.is_dir():
        return []
    ids = set()
    for p in figdir.glob("*"):
        r = naming.parse(p, short_title)
        if r and r[1]:
            ids.add(r[1][0])
    return sorted(ids)


def caption_of(root: Path, short_title: str, fig_id: str) -> str:
    """The figure's caption, read from its source header (`// caption:` / `%% caption:`)."""
    src = resolve(root, short_title, fig_id, "source")
    if src is None:
        return ""
    for line in src.read_text().splitlines():
        m = re.match(r"\s*(?://|%%)\s*caption:\s*(.+)", line)
        if m:
            return m.group(1).strip()
    return ""


def resolve(root: Path, short_title: str, fig_id: str, want: str = "svg", width: int = 1600) -> Path | None:
    """Consumer-facing: get the authoritative figure by id. `svg` = release > hand-edit > draft;
    `png` = that SVG rasterised on demand (and cached); `source` = the newest source file."""
    figdir = pool_dir(root)
    if not figdir.is_dir():
        return None
    if want == "source":
        for ext in ("dot", "mmd"):
            p = naming.find_latest(figdir, short_title, ext, chain_includes=fig_id)
            if p is not None:
                return p
        return None
    svg = _resolve_svg(figdir, short_title, fig_id)
    if want == "svg" or svg is None:
        return svg
    if want == "png":
        png = svg.with_suffix(".png")
        return png if png.exists() else export_png(svg, png, width)
    return None

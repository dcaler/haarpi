"""rabbitHole `mindmap` — a contribution map minted from a minted literature review.

See DESIGN_contribution_mindmap.md. The brain writes a small SPEC (papers + edges); everything
else is deterministic and tested. The grounding law: every citekey in the spec must exist in
refs.bib, or it is dropped — the model may summarise, never invent a paper.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from haarpi import figure, naming

# ── parsing the review (deterministic, no LLM) ─────────────────────────────────

_H2 = re.compile(r"^##[ \t]+(.+?)[ \t]*$", re.M)
# citekeys are read only inside bracketed pandoc citation groups [@a][@b] or [@a; @b] — so a
# stray `@` (an email, a handle) outside a citation is never mistaken for a paper.
_CITE_GROUP = re.compile(r"\[([^\]]*@[^\]]*)\]")
_CITE_KEY = re.compile(r"@([A-Za-z0-9_][A-Za-z0-9_:\-]*)")
# threads that are wrappers, not themes:
_SKIP = ("narrative review", "annotated bibliography", "references", "bibliography")


@dataclass
class Thread:
    theme: str
    citekeys: list[str] = field(default_factory=list)   # deduped, first-seen order


def parse_threads(md: str) -> list[Thread]:
    """Every ``## `` thesis thread (minus the Narrative-Review wrapper and the bibliography tail),
    each with the ``[@citekey]`` it cites, deduped in first-seen order."""
    heads = [(m.group(1).strip(), m.start(), m.end()) for m in _H2.finditer(md)]
    out: list[Thread] = []
    for i, (name, _s, e) in enumerate(heads):
        if name.lower() in _SKIP:
            continue
        body = md[e: heads[i + 1][1]] if i + 1 < len(heads) else md[e:]
        seen: dict[str, None] = {}
        for grp in _CITE_GROUP.findall(body):
            for k in _CITE_KEY.findall(grp):
                seen.setdefault(k, None)
        if seen:
            out.append(Thread(theme=name, citekeys=list(seen)))
    return out


# ── refs.bib → citekey labels (the grounding set) ──────────────────────────────

_ENTRY = re.compile(r"@\w+\s*\{\s*([^,\s]+)\s*,(.*?)(?=@\w+\s*\{|\Z)", re.S)
_FIELD = lambda name, blob: (m := re.search(rf"\b{name}\s*=\s*[{{\"]\s*(.*?)\s*[}}\"]\s*,?", blob, re.S)) and m.group(1)


def _label(author: str | None, year: str | None) -> str:
    """'Rousta 2015', 'Rousta et al. 2015', or a graceful fallback."""
    yr = (year or "").strip()
    if author:
        first = author.split(" and ")[0].strip()
        surname = first.split(",")[0].strip() or first
        etal = " et al." if " and " in author else ""
        return f"{surname}{etal} {yr}".strip()
    return yr or "?"


def bib_keys(refs_bib: str) -> dict[str, str]:
    """citekey -> 'Author Year' label, parsed from a biblatex/bibtex file."""
    out: dict[str, str] = {}
    for m in _ENTRY.finditer(refs_bib):
        key, blob = m.group(1).strip(), m.group(2)
        out[key] = _label(_FIELD("author", blob), _FIELD("year", blob))
    return out


# ── the spec (the frozen contract) ─────────────────────────────────────────────

@dataclass
class Paper:
    key: str
    label: str
    theme: str
    phrase: str


@dataclass
class Edge:
    src: str
    dst: str
    kind: str          # influence | temporal | evolution


@dataclass
class Mindmap:
    themes: list[str]
    papers: list[Paper]
    edges: list[Edge]


KINDS = ("influence", "temporal", "evolution")

_JSON_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)
_JSON_BARE = re.compile(r"(\{.*\})", re.S)


def parse_spec(reply: str) -> dict:
    """Pull the JSON object out of a brain reply (fenced first, else the widest bare braces)."""
    if not reply:
        return {}
    for pat in (_JSON_FENCE, _JSON_BARE):
        m = pat.search(reply)
        if m:
            try:
                obj = json.loads(m.group(1))
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                continue
    return {}


def validate(raw: dict, valid_keys: dict[str, str], themes: list[str]) -> Mindmap:
    """Apply the grounding law. Drop any paper/edge whose citekey is not in refs.bib; coerce theme
    to a real thread; label from refs.bib; default an unknown kind to 'influence'. Never raises."""
    tset = {t.lower(): t for t in themes}
    default_theme = themes[0] if themes else "papers"
    papers: dict[str, Paper] = {}
    for p in (raw.get("papers") or []):
        if not isinstance(p, dict):
            continue
        key = str(p.get("key", "")).strip()
        if key not in valid_keys or key in papers:
            continue                                   # grounding: unknown or duplicate key
        theme = tset.get(str(p.get("theme", "")).strip().lower(), default_theme)
        phrase = " ".join(str(p.get("phrase", "")).split())[:180]
        papers[key] = Paper(key=key, label=valid_keys[key], theme=theme, phrase=phrase)
    edges: list[Edge] = []
    seen_e: set[tuple[str, str]] = set()
    for e in (raw.get("edges") or []):
        if not isinstance(e, dict):
            continue
        src, dst = str(e.get("src", "")).strip(), str(e.get("dst", "")).strip()
        if src not in papers or dst not in papers or src == dst or (src, dst) in seen_e:
            continue                                   # grounding: both ends must be kept papers
        kind = str(e.get("kind", "")).strip().lower()
        edges.append(Edge(src=src, dst=dst, kind=kind if kind in KINDS else "influence"))
        seen_e.add((src, dst))
    used = [t for t in themes if any(p.theme == t for p in papers.values())]
    return Mindmap(themes=used or [default_theme], papers=list(papers.values()), edges=edges)


# ── DOT (deterministic) ────────────────────────────────────────────────────────

_EDGE_STYLE = {
    "influence": 'color="#334155"',                                  # who built on whom
    "temporal":  'color="#2563eb", style=dashed',                    # time ordering
    "evolution": 'color="#7c3aed", penwidth=2',                      # how the theme evolved
}


def _q(s: str, limit: int = 90) -> str:
    return str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").strip()[:limit]


def _wrap(s: str, width: int = 26) -> str:
    words, lines, cur = s.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width and cur:
            lines.append(cur); cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return "\\n".join(lines)


def to_dot(m: Mindmap, *, fig_id: str = "litmap", title: str = "") -> str:
    """themes = clusters, papers = nodes ('Author Year' + wrapped phrase), edges styled by kind."""
    prov = {"mode": "conceptual", "author": "rabbitHole+brain", "from": "minted litreview"}
    L = [figure._dot_header(fig_id, title or "Contribution map.", prov),
         f"digraph {fig_id} {{", '  rankdir=LR; bgcolor="#ffffff"; compound=true;',
         '  node [shape=box, style="rounded,filled", fillcolor="#eef2ff", '
         'color="#94a3b8", fontname="Helvetica", fontsize=9];',
         '  edge [fontname="Helvetica", fontsize=8];']
    if title:
        L.append(f'  labelloc=t; fontsize=15; fontname="Helvetica-Bold"; label="{_q(title, 120)}";')
    by_theme: dict[str, list[Paper]] = {}
    for p in m.papers:
        by_theme.setdefault(p.theme, []).append(p)
    for i, theme in enumerate(m.themes):
        L.append(f"  subgraph cluster_t{i} {{")
        L.append(f'    label="{_q(theme, 60)}"; style=rounded; color="#cbd5e1"; '
                 'fontname="Helvetica-Bold"; fontsize=10;')
        for p in by_theme.get(theme, []):
            lab = f"{_q(p.label, 40)}" + (f"\\n{_wrap(_q(p.phrase, 160))}" if p.phrase else "")
            L.append(f'    "{p.key}" [label="{lab}"];')
        L.append("  }")
    for e in m.edges:
        L.append(f'  "{e.src}" -> "{e.dst}" [{_EDGE_STYLE.get(e.kind, _EDGE_STYLE["influence"])}];')
    L.append("}")
    return "\n".join(L) + "\n"


# ── the LLM step: brain builds the spec, grounded + validated ──────────────────

_SYS = ("You map a literature review into a themed contribution graph. You output ONE JSON object "
        "and nothing else. You never invent a paper: use only the citekeys given to you.")

_PROMPT = """From this literature review, build a contribution map.

Themes (use these verbatim as the "theme" values):
{themes}

Papers you may use (ONLY these citekeys — inventing a key is forbidden):
{papers}

The review's threads (for context; each paper's citekey appears here in use):
{threads}

Return ONE JSON object, no prose:
{{"papers": [{{"key": "<citekey>", "theme": "<a theme above>",
              "phrase": "<one sentence: this paper's specific contribution>"}}],
  "edges":  [{{"src": "<citekey>", "dst": "<citekey>",
              "kind": "influence" | "temporal" | "evolution"}}]}}

- one entry in "papers" per citekey, assigned to its most fitting theme, phrase <= 25 words;
- "edges" connect papers where one plausibly influenced another, orders them in time, or shows a
  theme evolving; keep edges few and defensible; every src/dst must be a citekey above."""

_REPAIR = ("That was not one valid JSON object of the required shape. Return ONLY the JSON object "
           "({{\"papers\": [...], \"edges\": [...]}}), nothing else.")


def _threads_block(threads: list[Thread]) -> str:
    return "\n".join(f"## {t.theme}\n  cites: {', '.join(t.citekeys)}" for t in threads)


def compose(brain, threads: list[Thread], valid_keys: dict[str, str], *,
            repair: bool = True) -> Mindmap:
    """Brain builds the spec; parsed, grounded, validated. One repair pass; a labelled-stub Mindmap
    on total failure (an empty map with one note), never an exception."""
    themes = [t.theme for t in threads]
    cited = {k for t in threads for k in t.citekeys if k in valid_keys}
    papers_lines = "\n".join(f"- {k}  ({valid_keys[k]})" for k in sorted(cited))
    prompt = _PROMPT.format(themes="\n".join(f"- {t}" for t in themes),
                            papers=papers_lines, threads=_threads_block(threads))
    raw = parse_spec(brain.coordinator(prompt, _SYS))
    m = validate(raw, {k: valid_keys[k] for k in cited}, themes)
    if not m.papers and repair:
        raw = parse_spec(brain.coordinator(_REPAIR, _SYS))
        m = validate(raw, {k: valid_keys[k] for k in cited}, themes)
    return m


# ── orchestration ──────────────────────────────────────────────────────────────

def build_spec(review_md: str, refs_bib: str, brain, *, fig_id: str = "litmap",
               title: str = "") -> figure.FigureSpec:
    """Core, testable with a fake brain: review + refs.bib -> composed, grounded FigureSpec."""
    threads = parse_threads(review_md)
    keys = bib_keys(refs_bib)
    m = compose(brain, threads, keys)
    dot = to_dot(m, fig_id=fig_id, title=title)
    prov = {"mode": "conceptual", "author": "rabbitHole+brain", "from": "minted litreview",
            "papers": len(m.papers), "edges": len(m.edges)}
    return figure.FigureSpec(id=fig_id, kind="mindmap", format="dot", source=dot,
                             caption=title or "Contribution map.", provenance=prov)


def _renders(dot: str) -> bool:
    """Best-effort: does this DOT compile? (used by tests / a sanity gate)."""
    if not (path := __import__("shutil").which("dot")):
        return True
    r = subprocess.run([path, "-Tsvg"], input=dot, capture_output=True, text=True)
    return r.returncode == 0 and bool(r.stdout)


def emit(outdir: Path, short: str, spec: figure.FigureSpec) -> dict:
    """Write the map as a chain-named artifact into ``outdir`` (litReview/output) — NOT the paper's
    figures pool, since a contribution map never enters a paper. Same _ra-only clobber guard as the
    pool (a hand-edited ``*_DCR.svg`` stays authoritative), reusing the figure engine's naming +
    renderers so the revision chain and the Inkscape workflow behave identically."""
    outdir.mkdir(parents=True, exist_ok=True)
    ext = figure._SRC_EXT.get(spec.format, "txt")
    ds = figure._figure_datestamp(outdir, short, spec.id)
    src = outdir / naming.minor_name(short, [spec.id], ext, datestamp=ds)
    svg = outdir / naming.minor_name(short, [spec.id], "svg", datestamp=ds)
    src.write_text(spec.source)
    rendered = figure.render(spec, svg)
    if rendered:
        figure.export_png(rendered, rendered.with_suffix(".png"))
    if figure._has_finished_version(outdir, short, spec.id):
        print(f"[mindmap] note: a hand-edited/released {spec.id} exists — refreshed the _ra draft; "
              "your version stays authoritative.", file=sys.stderr)
    return {"id": spec.id, "datestamp": ds, "source": src, "svg": rendered}


def _find_minted_review(out: Path) -> Path | None:
    """The newest MINTED review in litReview/output — a token-free ``*_litreview.md`` (a draft
    ends in ``_ra``/``_ra_DCR``, so its stem does not end in 'litreview')."""
    rel = [p for p in out.glob("*.md") if p.stem.endswith("litreview")]
    return max(rel, key=lambda p: p.stat().st_mtime) if rel else None


def run(directory: str = ".", brain_override: str | None = None, *, fig_id: str = "litmap") -> int:
    """CLI entry: read the MINTED litreview + refs.bib, build the contribution map, write it onto
    the project's figure pool. Requires a minted review (per the design — not a draft)."""
    from . import config as _config
    from .brain import Brain
    cfg = _config.load_project(directory)
    gc = _config.load_global()
    paths = _config.project_paths(directory)
    review = _find_minted_review(paths.output)
    if review is None:
        print(f"[mindmap] no MINTED litreview (a token-free *_litreview.md) in {paths.output} — "
              "mint a review first (this verb consumes the release, not a draft).", file=sys.stderr)
        return 1
    refs = paths.output / "refs.bib"
    if not refs.is_file():
        print(f"[mindmap] no refs.bib in {paths.output} — cannot ground the map.", file=sys.stderr)
        return 1
    parts = review.stem.split("_")
    short = "_".join(parts[1:-1]) if len(parts) >= 3 else review.stem
    title = f"Contribution map — {short}"
    print(f"[mindmap] reading {review.name}  (coordinator={cfg.brain.coordinator_model})",
          file=sys.stderr)
    brain = Brain(cfg.brain, gc, backend_override=brain_override)
    spec = build_spec(review.read_text(), refs.read_text(), brain, fig_id=fig_id, title=title)
    if not spec.provenance.get("papers"):
        print("[mindmap] the brain returned no grounded papers — wrote a stub; re-run or check "
              "the review.", file=sys.stderr)
    res = emit(paths.output, short, spec)          # into litReview/output, NOT the figures pool
    print(f"[mindmap] {spec.provenance['papers']} papers, {spec.provenance['edges']} edges  ->  "
          f"{res.get('svg') or res.get('source')}")
    return 0

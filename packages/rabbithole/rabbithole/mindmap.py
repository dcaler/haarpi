"""rabbitHole `mindmap` — a contribution map minted from a minted literature review.

See DESIGN_contribution_mindmap.md. The brain writes a small SPEC (papers + edges); everything
else is deterministic and tested. The grounding law: every citekey in the spec must exist in
refs.bib, or it is dropped — the model may summarise, never invent a paper.
"""
from __future__ import annotations

import json
import math
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from haarpi import figure, naming

from . import guards, runlog

# ── parsing the review (deterministic, no LLM) ─────────────────────────────────

_H2 = re.compile(r"^##[ \t]+(.+?)[ \t]*$", re.M)
# citekeys are read only inside bracketed pandoc citation groups [@a][@b] or [@a; @b] — so a
# stray `@` (an email, a handle) outside a citation is never mistaken for a paper.
_CITE_GROUP = re.compile(r"\[([^\]]*@[^\]]*)\]")
# threads that are wrappers, not themes:
_SKIP = ("narrative review", "annotated bibliography", "references", "bibliography")
# ...and front matter that only re-lists papers: `summarize.top_sources_block` heads the draft with
# "## Most load-bearing sources (top 5% of 184)", whose count varies with the corpus so it cannot be
# an exact _SKIP entry (`redline._TOP_HEADING` and `revise` match it by prefix for the same reason).
# Left in, it opened a theme holding the very papers the map's innermost ring already names — the
# same ranking, drawn twice, one of them labelled as a thesis.
_TOP_SOURCES = re.compile(r"^##\s+most load-bearing sources\b", re.I | re.M)
_NEXT_H2 = re.compile(r"^##\s", re.M)

# where the bibliography starts — everything from here on is back matter, never a thread. Matching
# by HEADING NAME alone was not enough: a stray heading emitted inside the bibliography opened a
# thread whose body ran to EOF and scooped 62 citekeys out of the reference list.
_BIB_START = re.compile(r"^##\s+(?:annotated\s+bibliography|references|bibliography)\b",
                        re.I | re.M)


def _is_wrapper(theme: str) -> bool:
    return theme.strip().lower() in _SKIP


def _review_body(md: str) -> str:
    """The review's own prose: the load-bearing front block and the bibliography tail cut away.

    Both are apparatus that re-lists papers rather than argument that discusses them, and both
    were being read as review prose. The front block is the worse of the two: it names the top
    5% of sources with a sentence of rationale each, so every measure taken over the whole file
    credited those papers extra weight — the ones already innermost — and handed the composer
    the block's own blurb as a paper's grounding sentence instead of a claim from the review.
    Shared by the thread parse and the two per-sentence measures so all three see one body.
    """
    if cut := _BIB_START.search(md):
        md = md[: cut.start()]
    if top := _TOP_SOURCES.search(md):
        end = _NEXT_H2.search(md, top.end())
        md = md[: top.start()] + (md[end.start():] if end else "")
    return md


def _cite_keys(text: str) -> list[str]:
    """Citekeys inside bracketed citation groups, using the ONE shared extractor.

    This used to own a private `@([A-Za-z0-9_][A-Za-z0-9_:\\-]*)` pattern, which is ASCII-only:
    `böhringerPotential2022` matched as `b`. The stub then failed the grounding law in
    :func:`validate` and the paper vanished from the map — 9 of 167 cited sources in a real
    review, replaced by phantom keys (b, d, g, gr, h, k, m, n, pe). ``guards.all_citekeys``
    already splits grouped citations correctly and is Unicode-safe, so there is no reason for a
    second extractor that disagrees with it.
    """
    return [k for grp in _CITE_GROUP.findall(text) for k in guards.all_citekeys(f"[{grp}]")]


@dataclass
class Thread:
    theme: str
    citekeys: list[str] = field(default_factory=list)   # deduped, first-seen order


def parse_threads(md: str) -> list[Thread]:
    """Every ``## `` thesis thread (minus the Narrative-Review wrapper and the bibliography tail),
    each with the ``[@citekey]`` it cites, deduped in first-seen order.

    The bibliography is CUT, not name-matched: a heading that leaks into the reference list (see
    the claim-extraction fix in summarize) is not in ``_SKIP``, so it opened a thread whose body
    ran to end-of-file and harvested the whole reference list as one theme."""
    md = _review_body(md)
    heads = [(m.group(1).strip(), m.start(), m.end()) for m in _H2.finditer(md)]
    out: list[Thread] = []
    for i, (name, _s, e) in enumerate(heads):
        if _is_wrapper(name):
            continue
        body = md[e: heads[i + 1][1]] if i + 1 < len(heads) else md[e:]
        seen: dict[str, None] = {}
        for k in _cite_keys(body):
            seen.setdefault(k, None)
        if seen:
            out.append(Thread(theme=name, citekeys=list(seen)))
    return out


# ── refs.bib → citekey labels (the grounding set) ──────────────────────────────

_ENTRY = re.compile(r"@\w+\s*\{\s*([^,\s]+)\s*,(.*?)(?=@\w+\s*\{|\Z)", re.S)
_FIELD = lambda name, blob: (m := re.search(rf"\b{name}\s*=\s*[{{\"]\s*(.*?)\s*[}}\"]\s*,?", blob, re.S)) and m.group(1)


def _label(author: str | None, year: str | None) -> str:
    """'Rousta 2015', 'Rousta et al. 2015', or a graceful fallback. Strips the stray biblatex
    braces a name field can carry ({Bonjoc, X} -> Bonjoc) so they never reach a node label."""
    yr = (year or "").strip().strip("{}")
    if author:
        first = author.split(" and ")[0].strip()
        surname = (first.split(",")[0].strip() or first).strip("{}")
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


def _norm_doi(s: str | None) -> str:
    """Bare, lowercased DOI — strip a doi.org URL prefix so refs.bib and OpenAlex compare equal."""
    d = (s or "").strip().lower()
    return re.sub(r"^https?://(dx\.)?doi\.org/", "", d).strip()


def bib_dois(refs_bib: str) -> dict[str, str]:
    """citekey -> bare DOI, for the entries that carry one (the citation-graph grounding set)."""
    out: dict[str, str] = {}
    for m in _ENTRY.finditer(refs_bib):
        key, blob = m.group(1).strip(), m.group(2)
        if d := _norm_doi(_FIELD("doi", blob) or ""):
            out[key] = d
    return out


def _review_sentences(md: str):
    """Yield (citekeys, clean_sentence) for each body sentence that cites something — bibliography
    tail and markdown headings excluded, ``[@..]`` tags stripped and punctuation gaps tidied. Shared
    by the findings-evidence and the project-importance measures."""
    body = _review_body(md)
    body = "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("#"))  # drop headings
    for sent in re.split(r"(?<=[.!?])\s+", body.replace("\n", " ")):
        s = sent.strip()
        if "@" not in s:
            continue
        keys = set(_cite_keys(s))
        clean = _CITE_GROUP.sub("", s)                              # drop the [@..] tags
        clean = re.sub(r"\s+([.,;:!?])", r"\1", " ".join(clean.split()))  # tidy the gaps they leave
        if keys and clean:
            yield keys, clean


def citation_evidence(md: str) -> dict[str, list[str]]:
    """citekey -> the review sentence(s) that cite it — the grounding for a *findings* phrase.

    The composer otherwise sees only citekeys and can only guess a paper's topic; the actual
    result ('cut miss-sorting 55%→39% over two years') lives in the prose beside the ``[@key]``.
    At most two sentences per key, each bounded, so the prompt stays small."""
    out: dict[str, list[str]] = {}
    for keys, clean in _review_sentences(md):
        for k in keys:
            out.setdefault(k, [])
            if len(out[k]) < 2:                                    # at most two sentences per key
                out[k].append(clean[:300])
    return out


def evidence_weight(md: str) -> dict[str, int]:
    """citekey -> importance to THIS project, as a diagnostic: total words of review prose devoted
    to the paper (summed over every sentence that cites it). A load-bearing paper earns paragraphs
    and sits central; a perfunctory one-clause citation earns few words and drifts to the rim, so the
    radius surfaces where the review actually invests its argument. A sentence citing several papers
    credits its words to each (co-cited papers are genuinely discussed together)."""
    out: Counter = Counter()
    for keys, clean in _review_sentences(md):
        wc = len(clean.split())
        for k in keys:
            out[k] += wc
    return dict(out)


# ── the real citation graph (OpenAlex referenced_works, deterministic) ─────────
# The arrows are not the model's guesses: an edge A→B exists iff B is in A's actual OpenAlex
# reference list. Grounded and checkable. Papers without a DOI (or missing from OpenAlex) simply
# get no arrows — an honest gap, never an invented link. Network lives behind an injectable
# ``fetch`` so the edge logic is unit-tested offline.

def _short(oaid: str | None) -> str:
    return (oaid or "").rsplit("/", 1)[-1]


def _openalex_fetch_refs(dois: list[str], email: str) -> list[dict]:
    """Batch OpenAlex works by DOI -> [{id, doi, referenced_works}]. Best-effort; [] on failure."""
    import httpx
    out: list[dict] = []
    for i in range(0, len(dois), 50):
        filt = "doi:" + "|".join(dois[i:i + 50])
        try:
            r = httpx.get("https://api.openalex.org/works",
                          params={"filter": filt, "per-page": 50,
                                  "select": "id,doi,referenced_works,cited_by_count",
                                  "mailto": email},
                          timeout=30)
            r.raise_for_status()
            out += r.json().get("results", [])
        except Exception as e:  # noqa: BLE001 — a citation graph is a bonus, never fatal
            print(f"[mindmap] OpenAlex citation fetch failed: {e}", file=sys.stderr)
    return out


def citation_graph(papers: list["Paper"], dois: dict[str, str], email: str = "",
                   *, fetch=_openalex_fetch_refs) -> tuple[list["Edge"], dict[str, int]]:
    """One OpenAlex pass -> (real citation edges, world citation counts).

    Edge(A, B, 'cites') whenever B is in A's ``referenced_works`` and both are corpus papers; the
    counts map each citekey to its OpenAlex ``cited_by_count`` (total citations in the field, used to
    size the node). Deterministic given ``fetch``; papers without a DOI/OpenAlex record just don't
    appear (no invented links, no invented counts)."""
    keys = {p.key for p in papers}
    doi_ck = {dois[k]: k for k in dois if k in keys}                # doi -> citekey, corpus only
    if not doi_ck:
        return [], {}
    id_ck: dict[str, str] = {}
    refs: dict[str, set[str]] = {}
    world: dict[str, int] = {}
    for w in fetch(sorted(doi_ck), email):
        ck = doi_ck.get(_norm_doi(w.get("doi")))
        if not ck:
            continue
        id_ck[_short(w.get("id"))] = ck
        refs[ck] = {_short(x) for x in (w.get("referenced_works") or [])}
        world[ck] = int(w.get("cited_by_count") or 0)
    edges: list[Edge] = []
    seen: set[tuple[str, str]] = set()
    for ck, rf in refs.items():
        for rid in rf:
            b = id_ck.get(rid)
            if b and b != ck and (ck, b) not in seen:
                edges.append(Edge(src=ck, dst=b, kind="cites"))
            seen.add((ck, b))
    return edges, world


def citation_edges(papers: list["Paper"], dois: dict[str, str], email: str = "",
                   *, fetch=_openalex_fetch_refs) -> list["Edge"]:
    """Just the citation edges (thin wrapper over :func:`citation_graph`)."""
    return citation_graph(papers, dois, email, fetch=fetch)[0]


# ── the spec (the frozen contract) ─────────────────────────────────────────────

@dataclass
class Paper:
    key: str
    label: str
    theme: str
    phrase: str
    cited_by: int = 0          # OpenAlex total citations (field-wide); sizes the node. 0 = unknown
    importance: int = 0        # words of review prose devoted to it; pulls the node toward centre


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

_JSON_FENCE = re.compile(r"```(?:json)?\s*([\[{].*?[\]}])\s*```", re.S)   # object OR array in a fence
_JSON_OBJ = re.compile(r"(\{.*\})", re.S)                                 # widest bare object
_JSON_ARR = re.compile(r"(\[.*\])", re.S)                                 # widest bare array


def parse_spec(reply: str) -> dict:
    """Pull the paper spec out of a brain reply, tolerant of the two shapes a model actually returns:
    the requested ``{"papers": [...]}`` object AND a bare (or fenced) top-level ARRAY ``[{...}, ...]``
    — a reasoning model very often drops the wrapper and returns just the list. A list is wrapped as
    ``{"papers": [...]}`` so the caller always sees one contract. Tries the fenced block first, then
    the whole reply, then the widest bare object / array; the old regex matched only ``{..}`` and so
    turned every array reply into a stub (first ``{`` to last ``}`` spans two objects → invalid JSON)."""
    if not reply:
        return {}
    candidates: list[str] = []
    if m := _JSON_FENCE.search(reply):
        candidates.append(m.group(1))
    candidates.append(reply)                                   # a clean reply is itself the JSON
    for pat in (_JSON_OBJ, _JSON_ARR):                         # else the widest object / array in prose
        if mm := pat.search(reply):
            candidates.append(mm.group(1))
    for c in candidates:
        try:
            obj = json.loads(c)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
        if isinstance(obj, list):
            return {"papers": obj}
    return {}


def _clean_phrase(s: str) -> str:
    """Collapse whitespace and strip the stray braces / markdown emphasis a model sometimes
    wraps a phrase in (the ``{Bonjoc 2025…`` bug), bounded to a tooltip-sized length."""
    s = " ".join(str(s).split())
    return s.strip("{}*` ")[:180]


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
        phrase = _clean_phrase(str(p.get("contribution") or p.get("finding") or p.get("phrase") or ""))
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


# ── DOT (deterministic) — a radial twopi mind-map ────────────────────────────────
# The map is radial, not a left-right DAG: a centre (the review) → a ring of themes →
# a ring of papers. `twopi` gives that shape for free (the `dot` binary honours the in-file
# `layout=twopi` attribute, so `figure.render` needs no change). Each theme owns a hue; its
# papers are a pale tint of the same hue, so the branches read at a glance (the graphviz
# "happiness" technique). Each paper node carries its `Author Year` citation followed by the
# one-sentence contribution phrase (radial layout + colour keep this legible where the old
# left-right wall did not). The theme spokes carry no arrowhead; the influence/temporal/evolution
# links are drawn as directed citation ARROWS overlaid with `constraint=false` (so they never
# distort the rings) and decoded by a legend of the kinds actually present.

# a strong hue + a pale tint per theme, cycled when there are more themes than entries
_PALETTE = [
    ("#2563eb", "#dbeafe"), ("#059669", "#d1fae5"), ("#d97706", "#fef3c7"),
    ("#dc2626", "#fee2e2"), ("#7c3aed", "#ede9fe"), ("#0891b2", "#cffafe"),
    ("#db2777", "#fce7f3"), ("#65a30d", "#ecfccb"), ("#475569", "#e2e8f0"),
    ("#ea580c", "#ffedd5"),
]

_CROSSLINK = {                                                       # directed, constraint=false
    "cites":     'color="#47556955", penwidth=1.2',                # A cites B (OpenAlex); alpha-faded
    "influence": 'color="#334155", penwidth=1.6',                   # who built on whom
    "temporal":  'color="#1d4ed8", style=dashed, penwidth=1.4',     # time ordering
    "evolution": 'color="#7c3aed", penwidth=2.4',                   # how the theme evolved
}
_KIND_LABEL = {"cites":     "cites — A cites B (from OpenAlex)",
               "influence": "influence — one paper built on another",
               "temporal":  "temporal — earlier ▸ later",
               "evolution": "evolution — the theme shifting"}
_KIND_ORDER = ("cites", "influence", "temporal", "evolution")       # stable legend order


def _node_fontsize(cited_by: int) -> float:
    """Node size encodes TOTAL (field-wide) citations. Citation counts are heavy-tailed, so the map
    is log-scaled and clamped: 0→8pt, 10→~13, 100→~18, 1000+→22 — perceptible without one blob
    dwarfing the rest."""
    return round(min(22.0, 8.0 + 5.0 * math.log10(1 + max(0, cited_by))), 1)


def _node_border(in_degree: int) -> float:
    """Black-ring thickness encodes citations WITHIN this review (in-corpus in-degree). 0→no ring;
    otherwise a bounded penwidth so the local pillar stands out without a cartoon-thick border."""
    return 0.0 if in_degree <= 0 else round(min(5.0, 0.8 + 0.6 * in_degree), 1)


# ── packed-bands pie-slice layout (points; rendered by `dot -Kneato -n2`) ────────
# Papers are ranked by importance to THIS review (evidence_weight) and split into IMPORTANCE bands
# cut at fixed QUANTILES OF THE PROJECT CORPUS, separated by radial gaps that hold the red rings.
# Each band is packed tight into concentric rings; a paper sits at its THEME's angular sector (a
# coloured pie slice), so a peripheral theme shows an empty inner slice (the diagnostic). Band
# MEMBERSHIP carries the meaning; radius WITHIN a band is just packing. Blob size = total citations;
# black ring = in-corpus.
#
# The rings used to mark a reference BUDGET (target_min/target_max). They no longer do: a litreview
# is a coverage instrument, and exceeding a reference target when the work asks for it is correct,
# so a ring labelled "papers outside exceed it" was making a false claim about a healthy review.
# They now mark where a paper sits in the corpus by importance, which is a fact rather than a verdict.
BAND_QUANTILES = (0.05, 0.25, 0.50)   # innermost cut is the top-5% slice the review's header prints


def band_cuts(corpus_size: int, quantiles: tuple[float, ...] = BAND_QUANTILES) -> list[int]:
    """Rank positions of the rings, as a share of the project corpus. Strictly increasing.

    A small corpus can round several quantiles onto the same rank; collapsing them means one ring
    rather than three drawn on top of each other (the duplicate-node-id bug the old two-ring code
    shipped whenever ``target_min == target_max``).
    """
    cuts: list[int] = []
    for q in quantiles:
        c = max(1, round(max(0, corpus_size) * q))
        if not cuts or c > cuts[-1]:
            cuts.append(c)
    return cuts
_HALF_FILL = 0.92        # fraction of each theme's angular sector used (leaves gaps between slices)
_R0 = 210.0              # inner radius, points (leaves room for the centre hub)
_GAP = 48.0              # radial gap between bands (holds a target ring)
_PAD = 12.0              # minimum spacing between boxes, points
_LABEL_GAP = 130.0       # theme-label distance beyond the outermost paper, points
_HUB_W, _HUB_H = 2.3, 1.0    # centre hub size, inches


def _legend(kinds: list[str], cuts: list[int], corpus_size: int,
            quantiles: tuple[float, ...] = BAND_QUANTILES) -> str:
    """Graph-label legend decoding every channel: the red quantile rings, blob size (total
    citations), black-ring thickness (in-corpus citations), and the citation arrows present."""
    pcts = "% / ".join(f"{q * 100:g}" for q in quantiles[:len(cuts)]) + "%"
    ranks = " / ".join(str(c) for c in cuts)
    rows = [f'<TR><TD ALIGN="LEFT"><FONT COLOR="#dc2626"><B>red rings</B></FONT> = top {pcts} '
            f'of the {corpus_size}-source corpus by importance to this review '
            f'({ranks} papers)</TD></TR>',
            '<TR><TD ALIGN="LEFT"><B>nearer the centre</B> = more discussed in this review</TD></TR>',
            '<TR><TD ALIGN="LEFT"><B>blob size</B> = total citations (OpenAlex)</TD></TR>',
            '<TR><TD ALIGN="LEFT"><B>black ring</B> = citations within this review</TD></TR>']
    for k in kinds:
        hue = _CROSSLINK[k].split('"')[1]                            # the colour out of the style
        rows.append(f'<TR><TD ALIGN="LEFT"><FONT COLOR="{hue}"><B>&#8594;</B></FONT> '
                    f'{_KIND_LABEL[k]}</TD></TR>')
    return ('<<TABLE BORDER="0" CELLBORDER="0" CELLSPACING="2" CELLPADDING="2">'
            '<TR><TD ALIGN="LEFT"><B>legend</B></TD></TR>'
            + "".join(rows) + "</TABLE>>")


def _q(s: str, limit: int = 90) -> str:
    return str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").strip()[:limit]


def _wrap_lines(s: str, width: int = 22) -> list[str]:
    words, lines, cur = s.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width and cur:
            lines.append(cur); cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines or [""]


def _wrap(s: str, width: int = 20) -> str:
    return "\\n".join(_wrap_lines(s, width))


def _node_label(p: Paper) -> tuple[str, list[str]]:
    """The node's label string and its lines: the ``Author Year`` citation, then the wrapped
    contribution phrase (when present — labels-only draft maps pass papers with an empty phrase)."""
    lines = [_q(p.label, 40)] + (_wrap_lines(_q(p.phrase, 160), 22) if p.phrase else [])
    return "\\n".join(lines), lines


def _extents(p: Paper) -> tuple[float, float]:
    """Estimated box (width, height) in points — used to pack tight and to guarantee no overlap."""
    _, lines = _node_label(p)
    fs = _node_fontsize(p.cited_by)
    return max(len(l) for l in lines) * fs * 0.60 + 24, len(lines) * fs * 1.34 + 20


def _place_band(ps: list[Paper], r0: float, pos: dict[str, tuple[float, float]],
                theme_idx: dict[str, int], n: int, half: float) -> float:
    """Pack one importance band into concentric rings, box-aware, each paper at its theme's sector.
    Returns the band's outer radius. Rings fill in importance order with a per-theme angular budget,
    so the band is radially dense; a theme absent from a ring just leaves an angular gap."""
    ps = sorted(ps, key=lambda p: (p.importance, p.cited_by), reverse=True)
    r, idx, outer = r0, 0, r0
    while idx < len(ps):
        ring, used = [], defaultdict(float)                          # per-theme arc used
        while idx < len(ps):
            p = ps[idx]; w, h = _extents(p)
            arc = 2 * half * (r + h / 2)
            if used[p.theme] and used[p.theme] + w > arc:
                break                                                # this theme full at this radius
            ring.append((p, w, h)); used[p.theme] += w + _PAD; idx += 1
        if not ring:                                                 # safety: never stall
            p = ps[idx]; w, h = _extents(p); ring = [(p, w, h)]; idx += 1
        rowh = max(h for _, _, h in ring)
        Rc = r + rowh / 2
        by_theme: dict[str, list] = defaultdict(list)
        for p, w, h in ring:
            by_theme[p.theme].append((p, w))
        for th, items in by_theme.items():
            ac = 2 * math.pi * theme_idx[th] / n
            span = sum(w for _, w in items) + _PAD * (len(items) - 1)
            acc = 0.0
            for p, w in items:
                ang = ac + (acc + w / 2 - span / 2) / Rc
                pos[p.key] = (Rc * math.cos(ang), Rc * math.sin(ang))
                acc += w + _PAD
        r = Rc + rowh / 2 + _PAD
        outer = r
    return outer


def _collision_scale(m: Mindmap, pos: dict[str, tuple[float, float]]) -> float:
    """Smallest S >= 1 such that multiplying every position by S leaves no two boxes overlapping —
    the guarantee that packing overlaps (from size variation) are removed with minimal expansion."""
    he = {p.key: _extents(p) for p in m.papers}
    keys = [p.key for p in m.papers if p.key in pos]
    s = 1.0
    for i in range(len(keys)):
        xi, yi = pos[keys[i]]; wi, hi = he[keys[i]]
        for j in range(i + 1, len(keys)):
            xj, yj = pos[keys[j]]; wj, hj = he[keys[j]]
            dx, dy = abs(xi - xj), abs(yi - yj)
            sx = (wi / 2 + wj / 2) / dx if dx > 1e-6 else 1e9
            sy = (hi / 2 + hj / 2) / dy if dy > 1e-6 else 1e9
            s = max(s, min(sx, sy))
    return s * 1.02


def band_layout(m: Mindmap, cuts: list[int]
                ) -> tuple[dict[str, tuple[float, float]], list[float], dict[int, tuple[float, float]], float]:
    """The full geometry: pinned (x,y) points per paper, one ring radius per cut, per-theme label
    anchors, and the outer radius. Pure + deterministic (unit-testable).

    Papers rank by importance; ``cuts`` (from :func:`band_cuts`) splits the ranking into
    ``len(cuts) + 1`` bands, and a red ring sits in each gap so exactly ``cuts[i]`` papers fall
    inside ring *i*. Then a single collision-scale guarantees zero overlap."""
    n = max(1, len(m.themes))
    half = (math.pi / n) * _HALF_FILL
    theme_idx = {t: i for i, t in enumerate(m.themes)}
    ranked = sorted(m.papers, key=lambda p: (p.importance, p.cited_by, p.key), reverse=True)
    edges_ = [0, *cuts, len(ranked)]
    bands = [ranked[a:b] for a, b in zip(edges_, edges_[1:])]
    pos: dict[str, tuple[float, float]] = {}
    r0, circle_r, outer = _R0, [], _R0
    last = len(bands) - 1
    for bi, ps in enumerate(bands):
        bmax = _place_band(ps, r0, pos, theme_idx, n, half) if ps else r0
        if bi < last:
            circle_r.append(bmax + _GAP / 2)
            r0 = bmax + _GAP
        else:
            outer = bmax
    s = _collision_scale(m, pos)
    if s > 1.0:
        pos = {k: (x * s, y * s) for k, (x, y) in pos.items()}
        circle_r = [r * s for r in circle_r]
        outer *= s
    label_pos = {i: ((outer + _LABEL_GAP) * math.cos(2 * math.pi * i / n),
                     (outer + _LABEL_GAP) * math.sin(2 * math.pi * i / n)) for i in range(n)}
    return pos, circle_r, label_pos, outer


def to_dot(m: Mindmap, *, fig_id: str = "litmap", title: str = "",
           corpus_size: int = 0, quantiles: tuple[float, ...] = BAND_QUANTILES) -> str:
    """The contribution map: importance BANDS packed into theme pie-slices, with red rings at fixed
    quantiles of the project corpus. Blob size = total citations, black ring = in-corpus citations,
    faded arrows = the real OpenAlex citation graph, a centre hub, theme labels outside. Emits pinned
    coordinates in points — render with ``dot -Kneato -n2`` (see :func:`_render_pinned`)."""
    prov = {"mode": "conceptual", "author": "rabbitHole+brain", "from": "minted litreview"}
    cuts = band_cuts(corpus_size or len(m.papers), quantiles)
    pos, circle_r, label_pos, outer = band_layout(m, cuts)
    shown = set(pos)
    indeg = Counter(e.dst for e in m.edges if e.src in shown and e.dst in shown)
    L = [figure._dot_header(fig_id, title or "Contribution map.", prov),
         f"digraph {fig_id} {{",
         '  bgcolor="#ffffff"; outputorder=edgesfirst; fontname="Helvetica";',
         '  node [shape=box, style="rounded,filled", fontname="Helvetica", penwidth=0, '
         'margin="0.06,0.03"];',
         f'  "__hub__" [pos="0,0!", shape=ellipse, style=filled, fillcolor="#0f172a", '
         f'fontcolor="#ffffff", fontsize=20, fixedsize=true, width={_HUB_W}, height={_HUB_H}, '
         f'label="{_wrap(_q(title or fig_id, 48), 20)}"];']
    for p in m.papers:
        if p.key not in pos:
            continue
        hue, tint = _PALETTE[m.themes.index(p.theme) % len(_PALETTE)] if p.theme in m.themes \
            else _PALETTE[0]
        x, y = pos[p.key]
        lab, _lines = _node_label(p)
        fs = _node_fontsize(p.cited_by)
        border = _node_border(indeg.get(p.key, 0))
        ring = (f', penwidth={border}, color="#0f172a"' if border else ', penwidth=0')
        L.append(f'  "{p.key}" [label="{lab}", pos="{x:.1f},{y:.1f}!", fillcolor="{tint}", '
                 f'fontcolor="#1e293b", fontsize={fs}{ring}];')
    present: list[str] = []
    for e in m.edges:
        if e.src in shown and e.dst in shown:
            style = _CROSSLINK.get(e.kind, _CROSSLINK["influence"])
            kind = e.kind if e.kind in _CROSSLINK else "influence"
            if kind not in present:
                present.append(kind)
            L.append(f'  "{e.src}" -> "{e.dst}" [{style}, arrowsize=0.6];')
    # native red quantile rings (drawn on top) + their labels. Node ids are the ring INDEX, never
    # the cut value: two equal cuts used to emit the same id twice and graphviz silently kept one.
    for bi, (rr, cut) in enumerate(zip(circle_r, cuts)):
        pct = f"{quantiles[bi] * 100:g}%" if bi < len(quantiles) else f"top {cut}"
        L.append(f'  "__ring{bi}__" [pos="0,0!", shape=circle, fixedsize=true, width={2*rr/72:.3f}, '
                 f'height={2*rr/72:.3f}, label="", style=solid, fillcolor="none", color="#dc2626", '
                 'penwidth=4];')
        L.append(f'  "__ringlbl{bi}__" [pos="0,{rr+34:.0f}!", shape=plaintext, '
                 f'label="top {pct} ({cut})", '
                 f'fontcolor="#dc2626", fontsize=26, fontname="Helvetica-Bold"];')
    # theme labels outside the outermost ring
    for i, theme in enumerate(m.themes):
        hue, _tint = _PALETTE[i % len(_PALETTE)]
        lx, ly = label_pos[i]
        L.append(f'  "__t{i}__" [pos="{lx:.1f},{ly:.1f}!", shape=box, style="rounded,filled", '
                 f'fillcolor="{hue}", fontcolor="#ffffff", fontsize=16, label="{_wrap(_q(theme, 70), 18)}"];')
    legend = _legend([k for k in _KIND_ORDER if k in present], cuts, corpus_size or len(m.papers),
                     quantiles)
    L.append(f'  label={legend}; labelloc=b; fontsize=11; fontname="Helvetica";')
    L.append("}")
    return "\n".join(L) + "\n"


def _render_pinned(source: str, out_svg: Path) -> Path | None:
    """Render a pinned-coordinate DOT with ``dot -Kneato -n2`` (positions are final; no layout).
    Best-effort: a missing/failing renderer keeps the source and returns None."""
    if not __import__("shutil").which("dot"):
        return None
    out_svg.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["dot", "-Kneato", "-n2", "-Tsvg"], input=source, capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout:
        print(f"[mindmap] dot -Kneato -n2 failed: {r.stderr.strip()[:200]}", file=sys.stderr)
        return None
    out_svg.write_text(r.stdout)
    return out_svg


# ── the LLM step: brain builds the spec, grounded + validated ──────────────────

_SYS = ("You state each paper's key CONTRIBUTION to knowledge — at a high level, in plain words: what "
        "we now know from this paper that we did not before. You output ONE JSON object and nothing "
        "else. Use ONLY the citekeys given. Ground every contribution in that paper's evidence "
        "sentences; never invent a claim the evidence does not support.")

_PROMPT = """From this literature review, state each paper's key CONTRIBUTION — qualitatively and at a
high level, what we now know from it that we did not before.

Themes (use these verbatim as the "theme" values):
{themes}

For each paper below you are given its citekey, its label, and the review sentence(s) that cite it
(its evidence). Use ONLY these citekeys — inventing a key is forbidden.

{papers}

Return ONE JSON object, no prose:
{{"papers": [{{"key": "<citekey>", "theme": "<a theme above>",
              "contribution": "<what we now know from this paper>"}}]}}

Rules for "contribution" (<= 20 words each, plain language):
- State the QUALITATIVE takeaway — the claim the paper established, in words. What do we now know?
- NO statistics: no numbers, percentages, coefficients, p-values, effect sizes, or sample sizes.
  Say "far more than", not "b=0.74"; say "rarely", not "in 3 of 11 cases".
- NEVER a methods description ("Examines / Analyzes / Explores / Investigates / Studies / Reviews /
  Quantifies / Models how ..."). That is what a paper DID, not what we learned from it.
- Ground ONLY in that paper's own evidence; if the evidence is a framework/theory, state the claim
  it makes about the world, not that it "proposes a framework".

Examples (evidence -> contribution):
- evidence: "Bringing bins closer cut miss-sorted packaging by 28 percent, lowering the miss-sorted
  ratio from 55 to 39 percent over two years."
  BAD (methods): "Examines how proximity to bins affects household sorting."
  BAD (stats):   "Cut miss-sorting from 55% to 39% over two years."
  GOOD:          "Bringing collection closer to homes cuts sorting errors by lowering the effort to sort."
- evidence: "A review of 38 studies found information alone rarely reduced energy use, while direct
  feedback outperformed indirect feedback."
  GOOD: "Information alone rarely changes behavior; direct feedback does more than indirect."
"""

_REPAIR = ("That was not one valid JSON object of the required shape. Return ONLY the JSON object "
           "({{\"papers\": [{{\"key\":..., \"theme\":..., \"contribution\":...}}]}}), nothing else.")


def _papers_block(cited: list[str], valid_keys: dict[str, str],
                  evidence: dict[str, list[str]]) -> str:
    """One block per paper: citekey, label, and its review evidence sentence(s) — the grounding the
    model distils a finding from (it otherwise sees only the citekey and can only guess a topic)."""
    out = []
    for k in cited:
        ev = " ".join(evidence.get(k, [])) or "(no citing sentence found in the review)"
        out.append(f"- {k}  ({valid_keys[k]})\n    evidence: {ev}")
    return "\n".join(out)


_COMPOSE_BATCH = 25      # papers per compose call — see the note in :func:`compose`


def _compose_batch(brain, themes: list[str], batch: list[str], valid_keys: dict[str, str],
                   evidence: dict[str, list[str]], repair: bool) -> tuple[list[Paper], str]:
    """One compose call over one batch of papers. Returns (grounded papers, last raw reply)."""
    prompt = _PROMPT.format(themes="\n".join(f"- {t}" for t in themes),
                            papers=_papers_block(batch, valid_keys, evidence))
    sub = {k: valid_keys[k] for k in batch}
    # think=False: a contribution phrase is a grounded rewrite of the paper's evidence sentence,
    # governed by the prompt's few-shot rules — not judgement work. Leaving the coordinator's default
    # chain-of-thought on made it reason across every paper at once (a 69-paper review ran past a
    # 500s wall before emitting any JSON); without it the model streams the spec directly.
    reply = brain.coordinator(prompt, _SYS, think=False)
    m = validate(parse_spec(reply), sub, themes)
    if not m.papers and repair:
        # The repair must CARRY the papers: the coordinator is stateless, so a bare "return JSON"
        # follow-up asks the model to re-emit a spec it can no longer see. Re-send the full prompt.
        reply = brain.coordinator(prompt + "\n\n" + _REPAIR, _SYS, think=False)
        m = validate(parse_spec(reply), sub, themes)
    return m.papers, reply


def compose(brain, threads: list[Thread], valid_keys: dict[str, str], *,
            evidence: dict[str, list[str]] | None = None, repair: bool = True,
            batch_size: int = _COMPOSE_BATCH) -> Mindmap:
    """Brain distils a findings phrase per paper; parsed, grounded, validated. A labelled-stub
    Mindmap on total failure, never an exception. Edges are NOT the model's job — the caller
    overlays the real OpenAlex citation graph (see :func:`citation_edges`).

    Composed in BATCHES, one call per chunk of papers within a theme. A single call carrying the
    whole review does not fit: a 160-paper review built a 14.7k-token prompt and then needed ~5.6k
    tokens of JSON back, against a 16k window. The reply truncated, the repair pass re-sent the same
    oversized prompt and truncated identically, and the map shipped with zero papers — a blank
    diagnostic that reported nothing while looking like a rendered figure. Batching also contains
    failure: a batch that comes back unparseable costs its own papers, not the whole map.
    """
    evidence = evidence or {}
    themes = [t.theme for t in threads]
    # Each paper is composed ONCE, under the first theme that cites it — a paper cited in four
    # sections must not be sent four times, nor land as four nodes.
    seen: set[str] = set()
    batches: list[list[str]] = []
    for t in threads:
        keys = [k for k in t.citekeys if k in valid_keys and k not in seen]
        seen.update(keys)
        for i in range(0, len(keys), max(1, batch_size)):
            batches.append(keys[i:i + max(1, batch_size)])
    cited = sorted(seen)

    papers: dict[str, Paper] = {}
    last_reply, failed = "", 0
    print(f"  {runlog.stamp()}[mindmap] composing {len(cited)} paper(s) in {len(batches)} "
          f"batch(es) of <= {batch_size}...", flush=True)
    for bi, batch in enumerate(batches, 1):
        t_b = time.time()
        print(f"  {runlog.stamp()}[mindmap] batch {bi}/{len(batches)} — {len(batch)} paper(s)...",
              flush=True)
        try:
            got, last_reply = _compose_batch(brain, themes, batch, valid_keys, evidence, repair)
        except Exception as e:                                       # noqa: BLE001
            print(f"[mindmap] compose batch {bi}/{len(batches)} failed ({e}) — "
                  f"{len(batch)} paper(s) will be missing.", file=sys.stderr)
            failed += 1
            continue
        if not got:
            failed += 1
        for p in got:
            papers.setdefault(p.key, p)
        print(f"  {runlog.stamp()}[mindmap] batch {bi}/{len(batches)} grounded {len(got)}"
              f"/{len(batch)} in {runlog.fmt_dt(time.time() - t_b)}", flush=True)
    if failed:
        print(f"[mindmap] compose: {failed} of {len(batches)} batch(es) grounded nothing; "
              f"{len(papers)} of {len(cited)} cited papers on the map.", file=sys.stderr)
    if not papers and cited:
        # A per-draft diagnostic that never runs a model is worthless; surface WHY it grounded
        # nothing (into the task log_tail) so the next failure is diagnosable without a live re-run.
        print(f"[mindmap] compose grounded 0 of {len(cited)} cited papers. "
              f"raw reply head: {(last_reply or '')[:400]!r}", file=sys.stderr)
    used = [t for t in themes if any(p.theme == t for p in papers.values())]
    return Mindmap(themes=used or ([themes[0]] if themes else ["papers"]),
                   papers=list(papers.values()), edges=[])


# ── orchestration ──────────────────────────────────────────────────────────────

def spec_from_map(m: Mindmap, *, fig_id: str = "litmap", title: str = "",
                  corpus_size: int = 0,
                  quantiles: tuple[float, ...] = BAND_QUANTILES) -> figure.FigureSpec:
    """Render an already-composed Mindmap (all papers) to a FigureSpec, with the red rings drawn at
    fixed quantiles of the project corpus (``corpus_size``)."""
    dot = to_dot(m, fig_id=fig_id, title=title, corpus_size=corpus_size, quantiles=quantiles)
    cuts = band_cuts(corpus_size or len(m.papers), quantiles)
    prov = {"mode": "conceptual", "author": "rabbitHole+brain", "from": "minted litreview",
            "themes": len(m.themes), "papers": len(m.papers), "edges": len(m.edges),
            "corpus_size": corpus_size or len(m.papers),
            "quantiles": list(quantiles[:len(cuts)]), "cuts": cuts}
    return figure.FigureSpec(id=fig_id, kind="mindmap", format="dot", source=dot,
                             caption=title or "Contribution map.", provenance=prov)


def build_spec(review_md: str, refs_bib: str, brain, *, fig_id: str = "litmap", title: str = "",
               corpus_size: int = 0,
               quantiles: tuple[float, ...] = BAND_QUANTILES) -> figure.FigureSpec:
    """Core, testable with a fake brain: review + refs.bib -> composed, grounded FigureSpec. The
    contribution phrases come from the brain (grounded in the review's citing sentences) and node
    centrality from the review's own prose weight; the citation arrows + sizes are overlaid in
    :func:`run` (they need the network), so this seam stays offline."""
    keys = bib_keys(refs_bib)
    m = compose(brain, parse_threads(review_md), keys,
                evidence=citation_evidence(review_md))
    weight = evidence_weight(review_md)
    for p in m.papers:
        p.importance = weight.get(p.key, 0)
    return spec_from_map(m, fig_id=fig_id, title=title,
                         corpus_size=corpus_size or len(keys), quantiles=quantiles)


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
    rendered = _render_pinned(spec.source, svg)          # pinned coords -> dot -Kneato -n2
    if rendered:
        figure.export_png(rendered, rendered.with_suffix(".png"))
    if figure._has_finished_version(outdir, short, spec.id):
        print(f"[mindmap] note: a hand-edited/released {spec.id} exists — refreshed the _ra draft; "
              "your version stays authoritative.", file=sys.stderr)
    return {"id": spec.id, "datestamp": ds, "source": src, "svg": rendered}


def _find_review(out: Path) -> Path | None:
    """The newest litreview DRAFT in litReview/output, markdown or docx.

    The map is a per-DRAFT diagnostic: it steers while the review is still being written, so it
    consumes whatever the current draft is, not only a release. That draft is not always markdown.
    A redline revise deliberately writes none — the accepted text is the reviewer's to settle, so
    there is no markdown of a draft whose changes are still proposals — and leaves the tracked-change
    docx as the only copy. Globbing ``*.md`` alone meant the map died with "render a draft first" on
    every redline cycle, standing in a directory that held the draft.

    When the newest artifact is a docx that has a same-stem ``.md`` beside it (the resynth path
    writes both, docx last), the markdown wins: it is the text before pandoc round-tripped it.
    """
    rel = [p for p in [*out.glob("*.md"), *out.glob("*.docx")]
           if "litreview" in p.stem and not p.name.startswith("~$")]
    if not rel:
        return None
    newest = max(rel, key=lambda p: p.stat().st_mtime)
    if newest.suffix == ".docx" and (md := newest.with_suffix(".md")).is_file():
        return md
    return newest


def _read_review(path: Path) -> str:
    """The draft as markdown. A docx is read with its tracked insertions applied and its deletions
    gone — the honest reading of a redline for a diagnostic: *if this were accepted, here is the
    shape of the review*. ``Heading N`` styles come back as ``#`` x N so :func:`parse_threads` sees
    the same sections either way."""
    if path.suffix == ".docx":
        from . import docxio
        docxio.require_docx()
        return docxio.read_body_markdown(path)
    return path.read_text()


def _project_short(stem: str) -> str:
    """The project short name from a litreview stem ``{date}_{project}_litreview[_ra[_DCR]]``."""
    parts = stem.split("_")
    li = parts.index("litreview") if "litreview" in parts else len(parts)
    return "_".join(parts[1:li]) or stem


def run(directory: str = ".", brain_override: str | None = None, *, fig_id: str = "litmap") -> int:
    """CLI entry: read the current litreview DRAFT (or minted release) + refs.bib and write the
    contribution map beside it. A per-draft diagnostic — regenerated each revise cycle so the author
    can see the reference budget and which themes are peripheral while there is still time to act."""
    from . import config as _config
    from .brain import Brain
    runlog.start()               # the run clock every stamp() in this process reads
    cfg = _config.load_project(directory)
    gc = _config.load_global()
    paths = _config.project_paths(directory)
    review = _find_review(paths.output)
    if review is None:
        print(f"[mindmap] no litreview draft (*_litreview*.md or .docx) in {paths.output} — render "
              "a draft first.", file=sys.stderr)
        return 1
    refs = paths.output / "refs.bib"
    if not refs.is_file():
        print(f"[mindmap] no refs.bib in {paths.output} — cannot ground the map.", file=sys.stderr)
        return 1
    short = _project_short(review.stem)
    print(f"  {runlog.stamp()}[mindmap] reading {review.name}  "
          f"(coordinator={cfg.brain.coordinator_model})", flush=True)
    brain = Brain(cfg.brain, gc, backend_override=brain_override)
    review_md, refs_bib = _read_review(review), refs.read_text()
    # Compose the findings phrases (grounded in the review's citing sentences).
    m = compose(brain, parse_threads(review_md), bib_keys(refs_bib),
                evidence=citation_evidence(review_md))
    if not m.papers:
        print("[mindmap] the brain returned no grounded papers — wrote a stub; re-run or check "
              "the review.", file=sys.stderr)
    # Node channels: importance-to-this-review (prose weight) pulls a paper toward the centre;
    # OpenAlex citation counts size each blob; the real citation graph draws the faded arrows.
    weight = evidence_weight(review_md)
    edges, world = citation_graph(m.papers, bib_dois(refs_bib), gc.contact_email)
    for p in m.papers:
        p.cited_by = world.get(p.key, 0)
        p.importance = weight.get(p.key, 0)
    m.edges = edges
    print(f"  {runlog.stamp()}[mindmap] citation graph: {len(edges)} real edges, {len(world)} "
          f"papers sized by OpenAlex citations (email={gc.contact_email or 'unset'})", flush=True)
    # ONE big map: importance bands (core / budget / overflow) as theme pie-slices, with the red
    # target rings drawn at the project's reference budget; size = total citations, ring = in-corpus.
    # The rings are quantiles of the PROJECT CORPUS — refs.bib is that corpus as exported, and is
    # the same universe the grounding law admits papers from, so it is the honest denominator.
    spec = spec_from_map(m, fig_id=fig_id, title=f"Contribution map — {short}",
                         corpus_size=len(bib_keys(refs_bib)))
    print(f"  {runlog.stamp()}[mindmap] rendering...", flush=True)
    res = emit(paths.output, short, spec)          # into litReview/output, NOT the figures pool
    print(f"  {runlog.stamp()}[mindmap] {spec.provenance['papers']} papers, "
          f"{spec.provenance['edges']} citation edges, rings at "
          f"{spec.provenance['cuts']} of {spec.provenance['corpus_size']}  ->  "
          f"{res.get('svg') or res.get('source')}", flush=True)
    return 0

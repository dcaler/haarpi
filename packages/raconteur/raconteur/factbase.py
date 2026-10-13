"""Reading a literature review that is a FACT BASE, not a narrative.

rabbitHole can render the review as facts — each a sentence the source establishes, its page,
a verbatim quote, and a 0-3 score per approved topic — grouped into themes with short synthesis
bullets (`report_format: facts`). Its structured form is a JSON sidecar, frozen beside the
release by `haarpi next` (`<release>.facts.json`). That file is this module's input: raconteur
does not import rabbitHole, and the sidecar is the contract between them.

Read three ways:

  * a DIGEST in place of the review prose: the topics, the coverage verdicts, the load-bearing
    sources — a few thousand characters, where the narrative path read a blind 12k prefix;
  * THREADS for the outline's harvest: one per theme, so the analysis sees the whole review;
  * per-section FACT BLOCKS: the facts a section may draw on, chosen for its outline beats,
    each with its citekey, page and id — and afterwards a TRACE from every cited sentence back
    to the fact it used, so a claim in the paper can be checked at its page.

Only facts that COUNT are used: quoted verbatim on their page, and scoring useful (2) or
central (3) on a topic that serves a research question. Search-placed facts and background
facts stay in the review for a human to read; they never reach a draft.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from haarpi.text import sentence_units

from .log import log

SNAPSHOT_GLOB = "*.facts.json"          # haarpi.planner.facts_snapshot_name
FACT_BASE_MARK = "*Format:* fact base"  # rabbithole.mindmap.FACT_BASE_MARK
SECTION_FACTS = 40                      # facts offered to one section, at most
SECTION_CHARS = 6000                    # …and the characters they may take


@dataclass
class Fact:
    id: str
    citekey: str
    text: str
    page: int | None = None
    link: str = ""
    quote: str = ""
    weight: int = 0                     # best score on a question-serving topic (0 = not counted)


@dataclass
class Theme:
    topic: int                          # 1-based
    name: str
    synthesis: list = field(default_factory=list)
    facts: list = field(default_factory=list)       # [Fact], counted only, heaviest first


@dataclass
class FactBase:
    source: Path
    topics: list                        # [{"name", "serves", "covers"}]
    focus: list
    themes: list                        # [Theme] with at least one counted fact
    facts: dict                         # id -> Fact (counted only)
    labels: dict                        # citekey -> "Author, Year"
    coverage: list = field(default_factory=list)
    load_bearing: list = field(default_factory=list)

    def theme_heading(self, th: Theme) -> str:
        return f"T{th.topic} · {th.name}"


# ── finding and loading ─────────────────────────────────────────────────────────

def find(project_dir: Path, subdir: str) -> Path | None:
    """The facts to read: the snapshot frozen with the newest release; failing that, the live
    sidecar — but only when the newest review document IS a fact base (and it says so)."""
    out = Path(project_dir) / subdir / "output"
    snaps = sorted(out.glob(SNAPSHOT_GLOB), key=lambda p: p.stat().st_mtime, reverse=True)
    if snaps:
        return snaps[0]
    mds = sorted(out.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    live = Path(project_dir) / subdir / "work" / "facts.json"
    if mds and live.is_file():
        try:
            head = mds[0].read_text(encoding="utf-8", errors="replace")[:4000]
        except OSError:
            return None
        if FACT_BASE_MARK in head:
            log(f"[raconteur] fact base not released yet — reading the live {live.name}")
            return live
    return None


def load(project_dir: Path, subdir: str) -> FactBase | None:
    if not subdir:
        return None
    fp = find(project_dir, subdir)
    if fp is None:
        return None
    try:
        blob = json.loads(fp.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log(f"[warn] could not read the fact base {fp.name} ({e}) — reading the review prose")
        return None
    return from_blob(blob, fp)


def from_blob(blob: dict, source: Path) -> FactBase:
    topics = blob.get("topics") or []
    serving = {i for i, t in enumerate(topics) if t.get("serves")} or set(range(len(topics)))
    facts: dict[str, Fact] = {}
    for d in blob.get("facts", []):
        if not d.get("verified"):
            continue                    # search-placed: shown in the review, never drafted from
        w = max((sc for i, sc in enumerate(d.get("scores") or []) if i in serving), default=0)
        if w >= 2:
            facts[d["id"]] = Fact(d["id"], d["citekey"], " ".join((d.get("text") or "").split()),
                                  d.get("page"), d.get("link") or "",
                                  " ".join((d.get("quote") or "").split()), w)
    themes = []
    for t in blob.get("themes", []):
        fs = sorted((facts[i] for i in t.get("fact_ids", []) if i in facts),
                    key=lambda f: -f.weight)
        if fs and (t.get("item", 0) - 1) in serving:
            themes.append(Theme(t.get("item", 0), t.get("name", ""), t.get("synthesis") or [], fs))
    labels = {ck: (s.get("label") or ck) for ck, s in (blob.get("sources") or {}).items()}
    return FactBase(source, topics, blob.get("focus_items") or [], themes, facts, labels,
                    blob.get("coverage") or [], blob.get("load_bearing") or [])


# ── the three readings ──────────────────────────────────────────────────────────

def digest(fb: FactBase) -> str:
    """What stands in for the review prose: its shape, not its content. The content reaches a
    section as that section's facts."""
    lines = ["(A fact base: the review is facts from each source, grouped into topics and "
             "themes. Each section is given the facts it may cite.)", "", "Topics:"]
    for i, t in enumerate(fb.topics, 1):
        qs = ", ".join(f"Q{q}" for q in t.get("serves") or []) or "no research question"
        lines.append(f"- T{i}. {t.get('name', '')} (serves {qs})"
                     + (f" — covers: {t['covers']}" if t.get("covers") else ""))
    if fb.coverage:
        lines += ["", "Coverage against the brief:"] + [re.sub(r"\*+", "", c) for c in fb.coverage]
    if fb.load_bearing:
        lines += ["", "Most load-bearing sources: "
                  + "; ".join(f"{fb.labels.get(ck, ck)} [@{ck}]" for ck in fb.load_bearing)]
    return "\n".join(lines)


def thread_parts(fb: FactBase) -> list[tuple[str, str, tuple, str]]:
    """(heading, thesis, citekeys, body) per theme — `context.LitThread`'s fields."""
    out = []
    for th in fb.themes:
        keys = tuple(dict.fromkeys(f.citekey for f in th.facts))
        thesis = re.sub(r"\[@[^\]]+\]", "", th.synthesis[0] if th.synthesis else th.facts[0].text)
        thesis = re.sub(r"\s+([.,;:])", r"\1", " ".join(thesis.split())).strip()
        body = "\n".join([f"- {b}" for b in th.synthesis] + [fact_line(f) for f in th.facts])
        out.append((fb.theme_heading(th), thesis[:240], keys, body))
    return out


def fact_line(f: Fact) -> str:
    where = f" (p.{f.page})" if f.page else ""
    return f"- {f.text} [@{f.citekey}]{where} {{{f.id}}}"


# ── per-section facts ───────────────────────────────────────────────────────────

_WORD = re.compile(r"[a-z][a-z-]{2,}")
_STOP = frozenset("the and for with that this from are was were its their which into over "
                  "between about these those such than also more most can may how what".split())


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall((text or "").lower()) if w not in _STOP}


def _cos(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    num = sum(x * y for x, y in zip(a, b))
    den = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return num / den if den else 0.0


class SectionFacts:
    """Chooses each section's facts. Built once per draft run: the facts are embedded once
    (on the GPU the draft already holds) and every section is ranked against them; without an
    embedder, or if it fails, word overlap stands in."""

    def __init__(self, fb: FactBase, brain=None, themes: set[str] | None = None):
        self.fb = fb
        pool = [f for th in fb.themes if themes is None or fb.theme_heading(th) in themes
                for f in th.facts]
        self.pool = list({f.id: f for f in pool}.values()) or list(fb.facts.values())
        self.brain = brain
        self.vectors: dict[str, list[float]] = {}
        if brain is not None and getattr(brain, "embed_batch", None):
            try:
                vecs = brain.embed_batch([f.text for f in self.pool])
                self.vectors = {f.id: v for f, v in zip(self.pool, vecs) if v}
            except Exception as e:  # noqa: BLE001 — word overlap is a worse ranking, not a failure
                log(f"[warn] could not embed the facts ({e}) — ranking by word overlap")

    def for_section(self, heading: str, outline: str) -> list[Fact]:
        query = f"{heading}\n{outline}"
        if self.vectors:
            try:
                q = self.brain.embed(query[:1500])
                rel = {f.id: _cos(q, self.vectors.get(f.id, [])) for f in self.pool}
            except Exception:  # noqa: BLE001
                rel = {}
        else:
            rel = {}
        if not rel:
            qw = _words(query)
            rel = {f.id: len(qw & _words(f.text)) / (1 + len(_words(f.text))) for f in self.pool}
        ranked = sorted(self.pool, key=lambda f: (-(rel[f.id] + 0.02 * f.weight), f.id))
        out, used = [], 0
        for f in ranked:
            line = fact_line(f)
            if len(out) >= SECTION_FACTS or used + len(line) > SECTION_CHARS:
                break
            out.append(f)
            used += len(line) + 1
        return out


def section_block(facts: list[Fact]) -> str:
    if not facts:
        return ""
    return ("Facts you may use — each is what its source establishes, checked verbatim at the "
            "page shown. Cite a fact's source as [@citekey] where you use it; keep its numbers "
            "exactly. The {…} after each fact is its id, for tracing only — never write it in the text:\n"
            + "\n".join(fact_line(f) for f in facts))


# ── after drafting: trace and number fidelity ──────────────────────────────────

_KEY = re.compile(r"\[@([^\]]+)\]")
_NUM = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)*%?")


def _cited(sentence: str) -> list[str]:
    keys = []
    for grp in _KEY.findall(sentence):
        keys += [k.strip().lstrip("@").strip() for k in grp.split(";") if k.strip()]
    return keys


def numbers(text: str) -> set[str]:
    """Reported numbers; years and bare single digits are not facts (as rabbitHole counts)."""
    return {n for n in _NUM.findall(_KEY.sub(" ", text or ""))
            if not re.fullmatch(r"(1[5-9]|20)\d\d", n) and not re.fullmatch(r"\d", n)}


def trace(text: str, supplied: list[Fact]) -> list[dict]:
    """Every sentence citing a source, matched to the supplied fact of that source it most
    resembles (word overlap; deterministic, no model). ``fact`` is None when the sentence cites
    a source without using any fact it was given."""
    by_ck: dict[str, list[Fact]] = {}
    for f in supplied:
        by_ck.setdefault(f.citekey, []).append(f)
    rows = []
    for s in sentence_units(text):
        keys = _cited(s)
        if not keys:
            continue
        sw = _words(s)
        best, score = None, 0.0
        for ck in keys:
            for f in by_ck.get(ck, []):
                fw = _words(f.text)
                ov = len(sw & fw) / (min(len(sw), len(fw)) or 1)
                if ov > score:
                    best, score = f, ov
        rows.append({"sentence": " ".join(s.split()), "citekeys": keys,
                     "fact": best if score >= 0.25 else None, "overlap": round(score, 2)})
    return rows


def number_findings(text: str, supplied: list[Fact]) -> list:
    """A sentence traced to a fact must keep the fact's numbers. Returns guard Findings."""
    from .guards import Finding
    out = []
    for r in trace(text, supplied):
        f = r["fact"]
        if f is None:
            continue
        extra = numbers(r["sentence"]) - numbers(f.text)
        if extra and numbers(f.text):
            out.append(Finding(
                "fact-number", r["sentence"][:80],
                f"The sentence citing [@{f.citekey}] reports {', '.join(sorted(extra))}, which "
                f"its fact does not; the fact says: “{f.text}”. Use the fact's numbers exactly."))
    return out


_ID = re.compile(r"\s?\{[^{}\s]+#\d+\}")


def strip_ids(text: str) -> str:
    """A fact id the model copied into the prose despite being told not to."""
    return _ID.sub("", text or "")


def write_trace(path: Path, sections: list[tuple[str, str]],
                supplied: dict[str, list[Fact]], fb: FactBase) -> Path | None:
    """The sidecar a reader checks claims with: sentence → fact → page link, per section."""
    lines = ["# Fact trace", "",
             f"*Every sentence that cites a source, and the fact it draws on (from "
             f"{fb.source.name}). The link opens the source at the fact's page. “No matching "
             f"fact” means the sentence cites a source without using a fact it was given — "
             f"check it against the source.*", ""]
    n_all = n_hit = 0
    for heading, text in sections:
        rows = trace(text, supplied.get(heading, []))
        if not rows:
            continue
        lines += [f"## {heading}", ""]
        for r in rows:
            n_all += 1
            f = r["fact"]
            if f is None:
                lines.append(f"- {r['sentence']}  \n  → *no matching fact* "
                             f"({', '.join('@' + k for k in r['citekeys'])})")
                continue
            n_hit += 1
            page = f"[p.{f.page}]({f.link})" if f.page and f.link else (f.link or "")
            lines.append(f"- {r['sentence']}  \n  → {{{f.id}}} {f.text} — {page}")
        lines.append("")
    lines[2:2] = [f"*{n_hit} of {n_all} cited sentences matched to a fact.*", ""]
    try:
        path.write_text("\n".join(lines), encoding="utf-8")
    except OSError as e:
        log(f"[warn] could not write the fact trace ({e})")
        return None
    log(f"[raconteur] fact trace: {path.name} ({n_hit}/{n_all} cited sentences matched)")
    return path

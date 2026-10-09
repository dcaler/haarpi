"""The litreview as a fact base: each source's salient facts, each one checkable at its page.

The narrative review spent ~4 of its ~4.5 hours drafting and polishing prose, and the polish
fought itself: guards asked for more sources, the reviser re-cited familiar ones with their
numbers, and the same fact landed under five headings (DigiPros 261005/261006). Downstream, the
paper stage read a 12,000-character prefix of that prose and never reached the annotated
bibliography, which is where the facts were. A fact base drops the prose and keeps the facts:

  extract   one coordinator call per paper: up to `_MAX_FACTS` atomic facts, each with its
            kind, the focus items it bears on, the page it is on and a verbatim quote
  verify    the quote is searched for in the paper's own page-marked text. Found, the fact is
            VERBATIM and its page is the page the quote is on. Not found, the fact is located
            by embedding search instead and carries a support score, labelled when weak
  group     facts by focus item, then into themes by embedding; one short call per theme names
            it and writes 2-4 synthesis bullets that may cite only that theme's facts
  render    coverage against the brief, the contribution map (embedded), the load-bearing
            sources, the facts by focus item and theme, and every source's facts in full —
            each fact linked to its page in Zotero. `work/facts.json` holds the same, for tools

Opt-in per project (`report_format: facts`) while it is tested; nothing else changes.
"""

from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from . import guards, mindmap, render, runlog
from .models import Candidate

_FACTS_VERSION = 1
_MAX_FACTS = 10
FACT_KINDS = ("finding", "method", "definition", "argument", "limitation")
_MIN_QUOTE_WORDS = 5            # shorter than this, a "verbatim" match proves nothing
_THEME_TARGET = 10              # facts per theme, roughly
_MAX_THEMES = 8                 # per focus item
_TOP_QUANTILE = 0.05

_SYS = """\
You extract the most salient facts a paper establishes, for a researcher who will check every
one against the paper itself. Respond with ONLY a JSON array, no other text:

[
  {"fact": "one self-contained sentence stating what the paper establishes",
   "kind": "finding | method | definition | argument | limitation",
   "focus": [numbers of the focus items this fact bears on; [] if none],
   "page": the N of the [p.N] marker the supporting passage sits under,
   "quote": "a passage copied EXACTLY from the text, 8-30 words, that supports the fact"}
]

Rules:
- At most {max_facts} facts. Prefer the paper's own results, methods and definitions over
  background it merely cites. A fact must be checkable against the quote.
- Each fact stands alone: name what it is about instead of writing "this paper" or "they".
- Reproduce reported quantities EXACTLY (numbers, units, statistics). Never round them away.
- The quote must be copied character for character from the text, not paraphrased, and must
  not span a [p.N] marker.
- Tag a focus item only when the fact itself bears on it. Do not stretch a fact to fit the
  brief; an untagged fact is fine.
- Base everything strictly on the provided text. Do not invent."""

_THEME_SYS = """\
You organise a literature review's facts. Respond with ONLY a JSON object, no other text."""

_THEME_PROMPT = """\
Focus item: {item}
Other themes under this item, already named: {taken}

These facts were grouped together. Each begins with the [@citekey] of its source:
{facts}

Return:
{{"name": "what THESE facts share that the other themes do not, at most 6 words. Never restate
           the focus item, and never reuse or paraphrase another theme's name",
  "synthesis": ["2-4 bullets. Each says where these sources AGREE, where they are in TENSION,
                 or what GAP they leave for the focus item — and cites the [@citekey]s of the
                 facts it rests on. Use ONLY the facts above; add nothing they do not say."]}}"""


# ── data ───────────────────────────────────────────────────────────────────────

@dataclass
class Fact:
    id: str
    citekey: str
    text: str
    kind: str = "finding"
    focus: list = field(default_factory=list)      # 1-based focus item numbers
    page: int | None = None
    quote: str = ""
    verified: bool = False                         # the quote is on that page, verbatim
    support: float | None = None                   # embedding cosine, when located by search


@dataclass
class Theme:
    item: int                                      # 1-based focus item
    name: str
    fact_ids: list = field(default_factory=list)
    synthesis: list = field(default_factory=list)


def focus_items(focus: str) -> list[str]:
    return [x.strip() for x in (focus or "").split(";") if x.strip()]


# ── extraction and verification ─────────────────────────────────────────────────

_PAGE_SPLIT = re.compile(r"\[p\.(\d+)\]")


def _norm(text: str) -> str:
    """Text as compared for a verbatim match: case, whitespace, hyphenation, quote style and
    ligatures do not count; words do."""
    t = (text or "").replace("ﬁ", "fi").replace("ﬂ", "fl")
    # A word hyphenated across a line: "ini-\ntially" in the page, "ini- tially" in a quote
    # copied from it. Both are joined, so the two still compare equal.
    t = re.sub(r"(\w)-\s+(\w)", r"\1\2", t)
    t = t.translate(str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'", "–": "-", "—": "-"}))
    # Quotation marks never count: a model wraps its quote in them, the page rarely has them.
    t = re.sub(r"[^\w%.,;:()\-/<>=+]", " ", t.lower())
    return " ".join(t.split())


def page_texts(marked: str) -> dict[int, str]:
    """Normalised text of each page of a [p.N]-marked paper."""
    parts = _PAGE_SPLIT.split(marked or "")
    out: dict[int, str] = {}
    for i in range(1, len(parts) - 1, 2):
        out[int(parts[i])] = _norm(parts[i + 1])
    return out


def find_quote(quote: str, pages: dict[int, str], hint: int | None = None) -> int | None:
    """The page the quote appears on verbatim, or None. The model's page is tried first; a quote
    elided with "..." matches when every piece of five or more words is on that one page."""
    pieces = [p.strip(" .,;:") for p in (_norm(x) for x in re.split(r"\.\.\.|…", quote or ""))]
    pieces = [p for p in pieces if p]
    pieces = [p for p in pieces if len(p.split()) >= _MIN_QUOTE_WORDS]
    if not pieces:
        return None
    order = ([hint] if hint in pages else []) + [n for n in pages if n != hint]
    for n in order:
        if all(p in pages[n] for p in pieces):
            return n
    return None


def _json_objects(raw: str) -> list:
    """The reply's JSON array, or — when the array does not parse — every complete `{...}` object
    in it. One malformed element (an unescaped quote mark inside a verbatim quote, a reply cut
    off at the end) used to cost the paper all of its facts: 20 of DigiPros' 194 got none."""
    m = re.search(r"\[.*\]", raw or "", re.DOTALL)
    if m:
        try:
            v = json.loads(m.group(0))
            if isinstance(v, list):
                return v
        except json.JSONDecodeError:
            pass
    dec, out, i = json.JSONDecoder(), [], 0
    text = raw or ""
    while (i := text.find("{", i)) != -1:
        try:
            obj, end = dec.raw_decode(text, i)
        except json.JSONDecodeError:
            i += 1
            continue
        if isinstance(obj, dict):
            out.append(obj)
        i = end
    return out


def _parse_facts(raw: str, citekey: str, n_items: int) -> list[Fact]:
    items = _json_objects(raw)
    out: list[Fact] = []
    for d in items if isinstance(items, list) else []:
        if not isinstance(d, dict) or not str(d.get("fact") or "").strip():
            continue
        kind = str(d.get("kind") or "finding").strip().lower()
        focus = sorted({int(x) for x in (d.get("focus") or [])
                        if str(x).strip().isdigit() and 1 <= int(x) <= n_items})
        try:
            page = int(d.get("page")) if d.get("page") not in (None, "") else None
        except (TypeError, ValueError):
            page = None
        out.append(Fact(id=f"{citekey}#{len(out) + 1}", citekey=citekey,
                        text=" ".join(str(d["fact"]).split()),
                        kind=kind if kind in FACT_KINDS else "finding", focus=focus,
                        page=page, quote=" ".join(str(d.get("quote") or "").split())))
        if len(out) >= _MAX_FACTS:
            break
    return out


def ground(facts: list[Fact], marked: str, *, locate=None) -> list[Fact]:
    """Verify each fact's quote on its page; locate the rest by embedding search.

    `locate(texts) -> [{claim, location, quote, support}]` is `chroma.locate_list` bound to the
    paper. A fact neither verified nor located keeps no page: it is shown, unanchored, rather
    than dropped — a fact silently vanishing is the worse defect.
    """
    pages = page_texts(marked)
    for f in facts:
        hit = find_quote(f.quote, pages, f.page)
        f.verified = hit is not None
        if hit is not None:
            f.page = hit
    todo = [f for f in facts if not f.verified]
    if todo and locate is not None:
        try:
            located = {it.get("claim"): it for it in locate([f.text for f in todo])}
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] locate failed ({e}); unverified facts stay unanchored",
                  file=sys.stderr)
            located = {}
        for f in todo:
            it = located.get(f.text)
            if not it:
                f.page, f.quote = None, ""
                continue
            m = re.search(r"\d+", it.get("location") or "")
            f.page = int(m.group(0)) if m else None
            f.quote = it.get("quote") or ""
            f.support = it.get("support")
    return facts


def extract(brain, corpus: list[Candidate], cfg, paths, collection,
            citekeys: dict[int, str]) -> dict[str, list[Fact]]:
    """Every source's facts, grounded. Cached per citekey in work/facts/, versioned."""
    from . import chroma as _chroma
    from .summarize import _located_filename, _paper_text, reading_text
    d = paths.work / "facts"
    d.mkdir(parents=True, exist_ok=True)
    items = focus_items(cfg.focus)
    numbered = "\n".join(f"{i}. {it}" for i, it in enumerate(items, 1)) or "(none)"
    sys_prompt = _SYS.replace("{max_facts}", str(_MAX_FACTS))
    out: dict[str, list[Fact]] = {}
    t0, done, cached = time.time(), 0, 0
    for i, c in enumerate(corpus):
        ck = citekeys.get(i)
        if not ck:
            continue
        fp = d / f"{_located_filename(ck)}.json"
        try:
            blob = json.loads(fp.read_text(encoding="utf-8"))
            if blob.get("_v") == _FACTS_VERSION and blob.get("facts"):
                out[ck] = [Fact(**f) for f in blob["facts"]]
                cached += 1
                continue
        except (OSError, ValueError, TypeError):
            pass
        label = f"{c.first_author_last} {c.year or ''}".strip()
        print(f"  {runlog.stamp()}[{i + 1}/{len(corpus)}] facts: {label}", flush=True)
        text = reading_text(brain, c, cfg, collection, ck)
        prompt = (f"Review topic: {cfg.topic}\nFocus items:\n{numbered}\n\n"
                  f"Paper: {c.title} ({c.author_year()})\n\n"
                  f"--- PAPER TEXT ---\n{text}\n--- END ---\n\nJSON array:")
        facts: list[Fact] = []
        replies: list[str] = []
        for attempt in (1, 2):                    # one retry: a cut-off reply is usually a one-off
            try:
                raw = brain.coordinator(prompt, sys_prompt, think=False, num_ctx=16384)
                replies.append(raw or "")
                facts = _parse_facts(raw, ck, len(items))
            except Exception as e:  # noqa: BLE001
                replies.append(f"[exception] {e}")
                print(f"  [warn] fact extraction failed for {label}: {e}", file=sys.stderr)
            if facts:
                break
        failed = d / f"{_located_filename(ck)}.failed.txt"
        if not facts:
            # Kept so the next failure is diagnosed rather than guessed at.
            failed.write_text(f"# {label} — {len(text):,} chars read\n\n"
                              + "\n\n----- next attempt -----\n\n".join(replies),
                              encoding="utf-8")
            print(f"  [warn] {label}: no facts extracted (will retry on the next run; "
                  f"replies in work/facts/{failed.name})", file=sys.stderr)
            out[ck] = []
            continue
        failed.unlink(missing_ok=True)
        locate = None
        if collection is not None:
            locate = lambda texts, _ck=ck: _chroma.locate_list(collection, brain, _ck, texts)  # noqa: E731
        ground(facts, _paper_text(c), locate=locate)
        n_ok = sum(f.verified for f in facts)
        print(f"  {runlog.stamp()}    {len(facts)} facts, {n_ok} quoted verbatim", flush=True)
        fp.write_text(json.dumps({"_v": _FACTS_VERSION, "facts": [asdict(f) for f in facts]},
                                 indent=2, ensure_ascii=False), encoding="utf-8")
        out[ck] = facts
        done += 1
    print(f"  {runlog.stamp()}facts ready: {done} extracted, {cached} cached  "
          f"[{runlog.fmt_dt(time.time() - t0)}]", flush=True)
    return out


# ── grouping ────────────────────────────────────────────────────────────────────

def cluster(vectors: list[list[float]], k: int) -> list[int]:
    """Cosine k-means with a deterministic farthest-point start; a label per vector."""
    import numpy as np
    n = len(vectors)
    if n == 0:
        return []
    k = max(1, min(k, n))
    X = np.asarray(vectors, dtype=float)
    X = X / np.maximum(np.linalg.norm(X, axis=1, keepdims=True), 1e-12)
    centres = [0]
    for _ in range(1, k):
        sim = np.max(X @ X[centres].T, axis=1)
        centres.append(int(np.argmin(sim)))
    C = X[centres].copy()
    labels = np.zeros(n, dtype=int)
    for _ in range(25):
        new = np.argmax(X @ C.T, axis=1)
        if _ and np.array_equal(new, labels):
            break
        labels = new
        for j in range(k):
            members = X[labels == j]
            if len(members):
                v = members.mean(axis=0)
                C[j] = v / max(np.linalg.norm(v), 1e-12)
    return [int(x) for x in labels]


def _synthesis_ok(bullet: str, allowed: set[str]) -> bool:
    """A synthesis bullet must cite, and cite only facts in its own theme — that is what makes
    a bridge to a source that never said it impossible here rather than merely flagged."""
    keys = set(guards.all_citekeys(bullet))
    return bool(keys) and keys <= allowed


def _same_name(a: str, b: str) -> bool:
    norm = lambda x: " ".join(re.findall(r"[a-z0-9]+", x.lower()))   # noqa: E731
    return norm(a) == norm(b)


def _distinguisher(fs: list[Fact]) -> str:
    """A deterministic tiebreak for a theme name the model repeated: its main sources."""
    from collections import Counter
    top = [ck for ck, _ in Counter(f.citekey for f in fs).most_common(2)]
    return ", ".join(re.sub(r"\d{4}$", "", ck) for ck in top)


def themes_for(brain, item_no: int, item: str, facts: list[Fact],
               vectors: dict[str, list[float]]) -> list[Theme]:
    if not facts:
        return []
    k = min(_MAX_THEMES, max(1, round(len(facts) / _THEME_TARGET)))
    labels = cluster([vectors[f.id] for f in facts], k) if k > 1 else [0] * len(facts)
    groups: dict[int, list[Fact]] = {}
    for f, lab in zip(facts, labels):
        groups.setdefault(lab, []).append(f)
    out: list[Theme] = []
    dropped = 0
    for n, (_lab, fs) in enumerate(sorted(groups.items(), key=lambda kv: -len(kv[1])), 1):
        allowed = {f.citekey for f in fs}
        lines = "\n".join(f"- [@{f.citekey}] {f.text}" for f in fs)
        name, synthesis = f"Theme {n}", []
        taken = "; ".join(t.name for t in out) or "(none yet)"
        try:
            raw = brain.coordinator(_THEME_PROMPT.format(item=item, taken=taken, facts=lines),
                                    _THEME_SYS, think=False, num_ctx=16384)
            m = re.search(r"\{.*\}", raw or "", re.DOTALL)
            obj = json.loads(m.group(0)) if m else {}
            name = " ".join(str(obj.get("name") or name).split())[:80] or name
            if _same_name(name, item) or any(_same_name(name, t.name) for t in out):
                name = f"{name} ({_distinguisher(fs)})"
            bullets = [" ".join(str(b).split()) for b in (obj.get("synthesis") or [])
                       if str(b).strip()]
            synthesis = [b for b in bullets if _synthesis_ok(b, allowed)]
            dropped += len(bullets) - len(synthesis)
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] theme naming failed ({e}); kept the facts unnamed", file=sys.stderr)
        out.append(Theme(item=item_no, name=name, fact_ids=[f.id for f in fs],
                         synthesis=synthesis))
    if dropped:
        print(f"  {runlog.stamp()}  item {item_no}: dropped {dropped} synthesis bullet(s) that "
              f"cited outside their theme or cited nothing", flush=True)
    return out


# ── ranking, links, rendering ───────────────────────────────────────────────────

def _good(f: Fact) -> bool:
    from .summarize import _WEAK_SUPPORT
    return f.verified or (f.support is not None and f.support >= _WEAK_SUPPORT)


def fact_weight(facts: dict[str, list[Fact]]) -> dict[str, int]:
    """Importance to THIS review: a source's well-grounded facts that bear on the brief."""
    return {ck: sum(1 for f in fs if f.focus and _good(f)) for ck, fs in facts.items()}


def rank_key(fs: list[Fact]) -> tuple:
    """Load-bearing order: breadth across the brief first, then well-grounded facts on it, then
    verbatim ones. Counting facts alone tied 74 DigiPros sources at the 10-fact cap."""
    on = [f for f in fs if f.focus and _good(f)]
    return (-len({n for f in on for n in f.focus}), -len(on),
            -sum(1 for f in on if f.verified))


def on_brief(fs: list[Fact]) -> bool:
    return any(f.focus for f in fs)


def page_link(c: Candidate, page: int | None, gc) -> str:
    """Where a reader checks a fact. Zotero opens its own copy of the PDF at the page; a source
    with no PDF in Zotero falls back to its DOI (no page)."""
    if c.attachment_key:
        lib = (f"groups/{gc.zotero_library_id}"
               if getattr(gc, "zotero_library_type", "user") == "group" else "library")
        return (f"zotero://open-pdf/{lib}/items/{c.attachment_key}"
                + (f"?page={page}" if page else ""))
    if c.doi_key:
        return f"https://doi.org/{c.doi_key}"
    return c.url or ""


def guards_safe(s: str) -> str:   # kept tiny and local: a fact is one line of markdown
    return " ".join((s or "").split())


def _fact_line(f: Fact, c: Candidate, gc, *, with_key: bool = True) -> str:
    from .summarize import _WEAK_SUPPORT
    text = guards_safe(f.text)
    line = f"- {text}" + (f" [@{f.citekey}]" if with_key else "")
    link = page_link(c, f.page, gc)
    where = f"p.{f.page}" if f.page else ("DOI" if link.startswith("https://doi") else "")
    if where and link:
        line += f" — [{where}]({link})"
    elif where:
        line += f" — {where}"
    if f.quote:
        line += f': “{guards_safe(f.quote)}”'
    if not f.verified:
        if f.support is not None and f.support < _WEAK_SUPPORT:
            line += " *(located by search; weak support — verify)*"
        elif f.page:
            line += " *(located by search, not quoted verbatim)*"
        else:
            line += " *(not found in the text — verify)*"
    return line


@dataclass
class FactBase:
    focus: list
    facts: dict                                   # citekey -> [Fact]
    themes: list                                  # [Theme]

    def by_id(self) -> dict[str, Fact]:
        return {f.id: f for fs in self.facts.values() for f in fs}


def save_sidecar(paths, base: FactBase, corpus: list[Candidate], citekeys, gc) -> Path:
    by_key = {citekeys[i]: c for i, c in enumerate(corpus) if i in citekeys}
    blob = {
        "version": _FACTS_VERSION, "generated": date.today().isoformat(),
        "focus_items": base.focus,
        "facts": [dict(asdict(f), link=page_link(by_key[f.citekey], f.page, gc)
                       if f.citekey in by_key else "")
                  for fs in base.facts.values() for f in fs],
        "themes": [asdict(t) for t in base.themes],
        # Per source: does any of its facts bear on the brief? A clean list of off-brief
        # sources tells a drafter what not to reach for.
        "sources": {ck: {"on_brief": on_brief(fs), "facts": len(fs),
                         "verbatim": sum(f.verified for f in fs)}
                    for ck, fs in base.facts.items()},
    }
    fp = paths.work / "facts.json"
    fp.write_text(json.dumps(blob, indent=2, ensure_ascii=False), encoding="utf-8")
    return fp


def load_sidecar(paths) -> FactBase | None:
    fp = paths.work / "facts.json"
    try:
        blob = json.loads(fp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    facts: dict[str, list[Fact]] = {}
    for d in blob.get("facts", []):
        d = {k: v for k, v in d.items() if k != "link"}
        facts.setdefault(d["citekey"], []).append(Fact(**d))
    return FactBase(focus=blob.get("focus_items", []), facts=facts,
                    themes=[Theme(**t) for t in blob.get("themes", [])])


def map_inputs(base: FactBase):
    """What the contribution map needs, from the facts: themes as threads, facts as the
    grounding sentences, well-grounded facts as importance."""
    by_id = base.by_id()
    threads = []
    for t in base.themes:
        keys: dict[str, None] = {}
        for fid in t.fact_ids:
            if fid in by_id:
                keys.setdefault(by_id[fid].citekey, None)
        if keys:
            threads.append(mindmap.Thread(theme=t.name, citekeys=list(keys)))
    evidence = {ck: [f.text for f in fs if f.focus][:2] or [f.text for f in fs][:2]
                for ck, fs in base.facts.items()}
    return threads, evidence, fact_weight(base.facts)


def build_markdown(cfg, model: str, base: FactBase, corpus: list[Candidate], citekeys,
                   gc, coverage: list[str], map_png: Path | None) -> str:
    by_key = {citekeys[i]: c for i, c in enumerate(corpus) if i in citekeys}
    by_id = base.by_id()
    all_facts = [f for fs in base.facts.values() for f in fs]
    n_src = sum(1 for fs in base.facts.values() if fs)
    n_ver = sum(f.verified for f in all_facts)
    n_tag = sum(1 for f in all_facts if f.focus)
    pct = (lambda a: f"{100 * a / len(all_facts):.0f}%") if all_facts else (lambda a: "0%")
    parts = [
        f"# Literature Review: {cfg.topic}", "",
        f"*Project:* {cfg.project_name}  ",
        f"*Date:* {date.today().isoformat()}  ",
        f"*Sources:* {len(corpus)}  ",
        f"*Generated by:* rabbitHole — {model}  ",
        f"{mindmap.FACT_BASE_MARK}  ",
    ]
    if cfg.focus:
        parts.append(f"*Focus:* {cfg.focus}  ")
    parts += [f"*Foundation:* {len(all_facts)} facts from {n_src} sources · quoted verbatim "
              f"{pct(n_ver)} · bearing on the brief {pct(n_tag)}  ", ""]
    parts += ["*How to read this: every fact links to its page — the link opens the PDF in "
              "Zotero at that page. A plain quote was found verbatim on that page; anything "
              "located by search instead says so.*", ""]
    if map_png is not None:
        parts += [f"![Contribution map]({map_png.name})", ""]
    if coverage:
        parts += ["## Coverage against the brief", "",
                  "*Each focus item, and how much of the corpus addresses it in its own words. "
                  "THIN means fewer than 3 sources sit close to it, or one of its terms appears "
                  "in no source's own notes.*", ""] + coverage + [""]

    weight = fact_weight(base.facts)
    from .summarize import top_source_count
    ranked = sorted((ck for ck in weight if weight[ck] > 0 and ck in by_key),
                    key=lambda ck: (rank_key(base.facts[ck]), ck))[:top_source_count(
                        len(corpus), _TOP_QUANTILE)]
    if ranked:
        parts += [f"## Most load-bearing sources (top {_TOP_QUANTILE * 100:g}% of {len(corpus)})",
                  "", "*The sources with the most well-grounded facts bearing on the brief — the "
                  "same ranking as the contribution map's innermost ring.*", ""]
        for ck in ranked:
            fs = [f for f in base.facts[ck] if f.focus and _good(f)]
            items = sorted({n for f in fs for n in f.focus})
            parts.append(f"- **{by_key[ck].author_year()}** [@{ck}] — {weight[ck]} facts on "
                         f"item(s) {', '.join(map(str, items))}; e.g. {guards_safe(fs[0].text)}")
        parts.append("")

    parts += ["## Facts by focus item", ""]
    for n, item in enumerate(base.focus, 1):
        themes = [t for t in base.themes if t.item == n]
        parts += [f"## {n}. {item}", ""]
        if not themes:
            parts += ["*No fact in the corpus bears on this item.*", ""]
            continue
        for t in themes:
            fs = [by_id[fid] for fid in t.fact_ids if fid in by_id]
            parts += [f"### {t.name}", ""]
            if t.synthesis:
                parts += [f"- *{guards_safe(b)}*" for b in t.synthesis] + [""]
            parts += [_fact_line(f, by_key[f.citekey], gc) for f in fs
                      if f.citekey in by_key] + [""]

    untagged = [ck for ck, fs in base.facts.items() if fs and not on_brief(fs)]
    parts += ["## Annotated Bibliography (by publication)", "",
              "*Every source and all of its facts, the untagged ones included. A source none of "
              "whose facts bears on the brief is marked, and listed again at the end.*", ""]
    for ck in sorted((k for k in base.facts if k in by_key),
                     key=lambda k: by_key[k].first_author_last.lower()):
        c = by_key[ck]
        mark = " *(no fact bears on the brief)*" if ck in untagged else ""
        parts += [f"**{c.full_citation()}** [@{ck}]{mark}", ""]
        fs = base.facts[ck]
        parts += ([f"{_fact_line(f, c, gc, with_key=False)} *[{f.kind}"
                   + (f"; item {', '.join(map(str, f.focus))}" if f.focus else "") + "]*"
                   for f in fs] if fs else ["- *(no facts extracted — re-run report)*"]) + [""]
    if untagged:
        parts += ["### Sources with no fact bearing on the brief", "",
                  "*A source is listed here when none of its facts was tagged to a focus item. "
                  "Its facts are above. Candidates for the next audit — nothing is deleted.*", ""]
        parts += [f"- {by_key[ck].full_citation()} [@{ck}]"
                  for ck in sorted(untagged, key=lambda k: by_key[k].first_author_last.lower()
                                   if k in by_key else k) if ck in by_key] + [""]
    return "\n".join(parts)


# ── the report ─────────────────────────────────────────────────────────────────

def report(cfg, gc, paths, brain, corpus, notes, citekeys, collection) -> int:
    """The fact-base `report`: extract → group → map → render. Returns 0 on success."""
    from .render import _paged_reference
    from .summarize import _export_bibtex, coverage_lines
    t0 = time.time()
    items = focus_items(cfg.focus)
    print(f"\n{runlog.stamp()}[2/3] Extracting facts (up to {_MAX_FACTS} per paper, each "
          f"checked against its page)...", flush=True)
    facts = extract(brain, corpus, cfg, paths, collection, citekeys)

    print(f"\n{runlog.stamp()}[3/3] Grouping facts by focus item and theme...", flush=True)
    vectors: dict[str, list[float]] = {}
    for fs in facts.values():
        for f in fs:
            if f.focus:
                try:
                    vectors[f.id] = brain.embed(f.text)
                except Exception as e:  # noqa: BLE001
                    print(f"  [warn] embed failed for {f.id} ({e})", file=sys.stderr)
    themes: list[Theme] = []
    for n, item in enumerate(items, 1):
        fs = [f for f_list in facts.values() for f in f_list if n in f.focus and f.id in vectors]
        print(f"  {runlog.stamp()}item {n}: {len(fs)} facts from "
              f"{len({f.citekey for f in fs})} sources", flush=True)
        themes += themes_for(brain, n, item, fs, vectors)
    base = FactBase(focus=items, facts=facts, themes=themes)
    save_sidecar(paths, base, corpus, citekeys, gc)

    coverage = coverage_lines(brain, cfg.focus or "", "", corpus, notes, citekeys)
    for n, line in enumerate(coverage, 1):
        fs = [f for f_list in facts.values() for f in f_list if n in f.focus]
        coverage[n - 1] = (line + f" **{len(fs)} facts** from "
                           f"{len({f.citekey for f in fs})} sources "
                           f"({sum(f.verified for f in fs)} quoted verbatim).")

    bib_path = _export_bibtex(cfg, gc, paths, citekeys, corpus)
    map_png = None
    if bib_path and Path(bib_path).is_file():
        try:
            threads, evidence, weight = map_inputs(base)
            res = mindmap.make_map(brain, gc, paths.output, cfg.project_name, threads,
                                   Path(bib_path).read_text(), evidence, weight,
                                   datestamp=date.today().strftime("%y%m%d"))
            svg = res.get("svg")
            png = Path(svg).with_suffix(".png") if svg else None
            map_png = png if png and png.is_file() else None
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] contribution map failed ({e}); the document ships without it.",
                  file=sys.stderr)

    model = (cfg.brain.claude_model if brain.backend == "claude"
             else cfg.brain.coordinator_model)
    md = build_markdown(cfg, model, base, corpus, citekeys, gc, coverage, map_png)
    stem = f"{date.today().strftime('%y%m%d')}_{cfg.project_name}_litreview_ra"
    out_md = paths.output / f"{stem}.md"
    out_md.write_text(md, encoding="utf-8")
    out_docx = paths.output / f"{stem}.docx"
    ok = render.pandoc_convert(out_md, out_docx, resource_path=paths.output,
                               reference_doc=_paged_reference(paths))
    all_facts = [f for fs in facts.values() for f in fs]
    print("\n" + "=" * 60)
    print(f" report (fact base) complete  [{runlog.fmt_dt(time.time() - t0)}]")
    print("=" * 60)
    print(f"  Review (md)  : {out_md}")
    if ok:
        print(f"  Review (docx): {out_docx}")
    if map_png:
        print(f"  Map (embedded): {map_png}")
    print(f"  Facts: {len(all_facts)} from {sum(1 for fs in facts.values() if fs)} sources | "
          f"verbatim {sum(f.verified for f in all_facts)} | themes {len(themes)} | "
          f"sidecar work/facts.json")
    return 0

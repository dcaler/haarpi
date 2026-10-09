"""The litreview as a fact base: each source's salient facts, each one checkable at its page.

The narrative review spent ~4 of its ~4.5 hours drafting and polishing prose, and the polish
fought itself: guards asked for more sources, the reviser re-cited familiar ones with their
numbers, and the same fact landed under five headings (DigiPros 261005/261006). Downstream, the
paper stage read a 12,000-character prefix of that prose and never reached the annotated
bibliography, which is where the facts were. A fact base drops the prose and keeps the facts:

  extract   one coordinator call per paper: up to `_MAX_FACTS` atomic facts, each with its
            kind, the page it is on and a verbatim quote
  verify    the quote is searched for in the paper's own page-marked text. Found, the fact is
            VERBATIM and its page is the page the quote is on. Not found, the fact is located
            by embedding search instead and carries a support score, labelled when weak
  score     one call per paper scores every fact 0-3 against every TOPIC — a human-approved list
            of single subjects, each mapped to the research questions it serves. Tagging facts
            against the compound research questions themselves was unstable: read strictly it
            dropped core sources, read loosely everything drifted to the broadest question
  group     facts by topic (score 2+), then into themes by embedding; one short call per theme
            names it and writes 2-4 synthesis bullets that may cite only that theme's facts
  render    coverage per research question (from its topics), the contribution map (embedded),
            the load-bearing sources, the facts by topic and theme, and every source's facts in
            full — each fact linked to its page in Zotero. `work/facts.json` holds the same

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
_MAX_THEMES = 8                 # per topic
_TOP_QUANTILE = 0.05
_THIN_SOURCES = 3               # a question whose topics hold useful facts from fewer is THIN

_SYS = """\
You extract the most salient facts a paper establishes, for a researcher who will check every
one against the paper itself. Respond with ONLY a JSON array, no other text:

[
  {"fact": "one self-contained sentence stating what the paper establishes",
   "kind": "finding | method | definition | argument | limitation",
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
- Base everything strictly on the provided text. Do not invent."""

_THEME_SYS = """\
You organise a literature review's facts. Respond with ONLY a JSON object, no other text."""

_THEME_PROMPT = """\
Topic: {item}
Other themes under this topic, already named: {taken}

These facts were grouped together. Each begins with the [@citekey] of its source:
{facts}

Return:
{{"name": "what THESE facts share that the other themes do not, at most 6 words. Never restate
           the topic, and never reuse or paraphrase another theme's name",
  "synthesis": ["2-4 bullets. Each says where these sources AGREE, where they are in TENSION,
                 or what GAP they leave for the topic — and cites the [@citekey]s of the
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
    scores: list = field(default_factory=list)     # 0-3 per topic, in topic order


@dataclass
class Theme:
    item: int                                      # 1-based topic number
    name: str
    fact_ids: list = field(default_factory=list)
    synthesis: list = field(default_factory=list)


def focus_items(focus: str) -> list[str]:
    return [x.strip() for x in (focus or "").split(";") if x.strip()]


@dataclass
class Topic:
    name: str
    serves: list = field(default_factory=list)     # 1-based research questions (focus items)


def resolve_topics(cfg) -> list[Topic]:
    """The approved topics from the review's config (`topics:`), each a name and the research
    questions it serves. Without any, each focus item stands in as its own topic — the compound
    questions are a poor scoring target, which is why the config list exists, but a review that
    has none still renders."""
    out: list[Topic] = []
    for t in getattr(cfg, "topics", None) or []:
        if isinstance(t, str) and t.strip():
            out.append(Topic(name=t.strip()))
        elif isinstance(t, dict) and str(t.get("name") or "").strip():
            out.append(Topic(name=str(t["name"]).strip(),
                             serves=sorted({int(x) for x in (t.get("serves") or [])
                                            if str(x).strip().isdigit()})))
    if out:
        return out
    return [Topic(name=q, serves=[n]) for n, q in enumerate(focus_items(cfg.focus), 1)]


def best(f: Fact) -> int:
    return max(f.scores) if f.scores else 0


def derive_focus(facts: dict[str, list[Fact]], topics: list[Topic]) -> None:
    """A fact bears on a research question when it scores 2+ on a topic serving that question."""
    for fs in facts.values():
        for f in fs:
            f.focus = sorted({q for t, sc in zip(topics, f.scores) if sc >= 2 for q in t.serves})


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


# ── scoring against the approved topics ────────────────────────────────────────

_SCORE_SYS = "You score how useful each fact is to each research topic. Respond with ONLY a JSON object."

_SCORE_PROMPT = """\
Research topics:
{topics}

Facts from one source ({label}):
{facts}

Score each fact against EVERY topic:
  0 = no use for this topic
  1 = background: worth knowing, not something to build on
  2 = useful: a method, definition or finding a researcher on this topic would likely cite
  3 = central: the fact is directly about this topic
Judge each topic on its own; a fact can score high on several or on none.

Return {{"F1": [{n} scores in topic order], "F2": [...], ...}}"""


def topics_signature(topics: list[Topic]) -> str:
    import hashlib
    return hashlib.sha1("\n".join(t.name for t in topics).encode()).hexdigest()[:12]


def _parse_scores(raw: str, n_facts: int, n_topics: int) -> list[list[int]] | None:
    m = re.search(r"\{.*\}", raw or "", re.DOTALL)
    try:
        obj = json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        obj = None
    if not isinstance(obj, dict):
        return None
    out = []
    for j in range(1, n_facts + 1):
        row = obj.get(f"F{j}")
        if not isinstance(row, list) or len(row) != n_topics:
            return None
        try:
            out.append([min(3, max(0, int(x))) for x in row])
        except (TypeError, ValueError):
            return None
    return out


def score(brain, facts: dict[str, list[Fact]], topics: list[Topic], corpus, citekeys,
          paths) -> None:
    """Every fact's 0-3 score against every topic, one call per source. Cached per citekey and
    keyed by the topic list, so editing the topics re-scores and nothing else does. Measured on
    12 DigiPros sources scored twice: 93% of scores identical, all within one point."""
    from .summarize import _located_filename
    d = paths.work / "facts" / "scores"
    d.mkdir(parents=True, exist_ok=True)
    sig = topics_signature(topics)
    listing = "\n".join(f"T{i}. {t.name}" for i, t in enumerate(topics, 1))
    label = {citekeys[i]: c.author_year() for i, c in enumerate(corpus) if i in citekeys}
    t0, done, cached, failed = time.time(), 0, 0, 0
    for ck, fs in facts.items():
        if not fs:
            continue
        fp = d / f"{_located_filename(ck)}.json"
        try:
            blob = json.loads(fp.read_text(encoding="utf-8"))
            if blob.get("sig") == sig and set(blob.get("scores", {})) == {f.id for f in fs}:
                for f in fs:
                    f.scores = blob["scores"][f.id]
                cached += 1
                continue
        except (OSError, ValueError):
            pass
        prompt = _SCORE_PROMPT.format(topics=listing, label=label.get(ck, ck), n=len(topics),
                                      facts="\n".join(f"F{j}. {f.text}"
                                                      for j, f in enumerate(fs, 1)))
        rows = None
        for _attempt in (1, 2):
            try:
                rows = _parse_scores(brain.coordinator(prompt, _SCORE_SYS, think=False,
                                                       num_ctx=8192), len(fs), len(topics))
            except Exception as e:  # noqa: BLE001
                print(f"  [warn] scoring failed for {ck}: {e}", file=sys.stderr)
            if rows:
                break
        if not rows:
            failed += 1
            print(f"  [warn] {ck}: facts not scored (shown unscored; retried next run)",
                  file=sys.stderr)
            for f in fs:
                f.scores = [0] * len(topics)
            continue
        for f, row in zip(fs, rows):
            f.scores = row
        fp.write_text(json.dumps({"sig": sig, "scores": {f.id: f.scores for f in fs}},
                                 indent=1), encoding="utf-8")
        done += 1
    print(f"  {runlog.stamp()}scores ready: {done} scored, {cached} cached, {failed} failed  "
          f"[{runlog.fmt_dt(time.time() - t0)}]", flush=True)


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
        print(f"  {runlog.stamp()}  T{item_no}: dropped {dropped} synthesis bullet(s) that "
              f"cited outside their theme or cited nothing", flush=True)
    return out


# ── ranking, links, rendering ───────────────────────────────────────────────────

def _good(f: Fact) -> bool:
    from .summarize import _WEAK_SUPPORT
    return f.verified or (f.support is not None and f.support >= _WEAK_SUPPORT)


def fact_weight(facts: dict[str, list[Fact]]) -> dict[str, int]:
    """Importance to THIS review: the best topic score of each well-grounded fact that scores
    2+ somewhere, summed — a central fact counts 3, a useful one 2, background nothing."""
    return {ck: sum(best(f) for f in fs if best(f) >= 2 and _good(f))
            for ck, fs in facts.items()}


def rank_key(fs: list[Fact]) -> tuple:
    """Load-bearing order: breadth across the topics first, then weight, then verbatim facts.
    Counting facts alone tied 74 DigiPros sources at the 10-fact cap."""
    on = [f for f in fs if best(f) >= 2 and _good(f)]
    topics = {i for f in on for i, sc in enumerate(f.scores) if sc >= 2}
    return (-len(topics), -sum(best(f) for f in on), -sum(1 for f in on if f.verified))


def on_brief(fs: list[Fact]) -> bool:
    """Does any fact score useful (2) or central (3) on any topic?"""
    return any(best(f) >= 2 for f in fs)


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
    topics: list = field(default_factory=list)    # [Topic]

    def by_id(self) -> dict[str, Fact]:
        return {f.id: f for fs in self.facts.values() for f in fs}


def save_sidecar(paths, base: FactBase, corpus: list[Candidate], citekeys, gc) -> Path:
    by_key = {citekeys[i]: c for i, c in enumerate(corpus) if i in citekeys}
    blob = {
        "version": _FACTS_VERSION, "generated": date.today().isoformat(),
        "focus_items": base.focus,
        "topics": [asdict(t) for t in base.topics],
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
                    themes=[Theme(**t) for t in blob.get("themes", [])],
                    topics=[Topic(**t) for t in blob.get("topics", [])])


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
    evidence = {ck: [f.text for f in sorted(fs, key=lambda f: -best(f))][:2]
                for ck, fs in base.facts.items()}
    return threads, evidence, fact_weight(base.facts)


def coverage(base: FactBase) -> list[str]:
    """One line per research question, built from the topics that serve it.

    A question with no topic, or whose topics hold useful-or-better facts from fewer than
    `_THIN_SOURCES` sources, is THIN. Listing each topic's count shows which half of a
    two-subject question is missing, which the per-question view alone cannot."""
    lines: list[str] = []
    all_facts = [f for fs in base.facts.values() for f in fs]
    for q, question in enumerate(base.focus, 1):
        serving = [(i, t) for i, t in enumerate(base.topics) if q in t.serves]
        if not serving:
            lines.append(f"- **THIN** — *Q{q}: {question}*. No topic serves this question.")
            continue
        parts, srcs = [], set()
        for i, t in serving:
            central = [f for f in all_facts if len(f.scores) > i and f.scores[i] == 3]
            useful = [f for f in all_facts if len(f.scores) > i and f.scores[i] == 2]
            s_t = {f.citekey for f in central + useful}
            srcs |= s_t
            parts.append(f"T{i + 1} {t.name}: {len(central)} central, {len(useful)} useful "
                         f"facts from {len(s_t)} sources")
        verdict = "THIN" if len(srcs) < _THIN_SOURCES else "Covered"
        lines.append(f"- **{verdict}** — *Q{q}: {question}*. {len(srcs)} sources. "
                     + "; ".join(parts) + ".")
    return lines


def _scores_tag(f: Fact, base: FactBase) -> str:
    hits = [f"T{i + 1}:{sc}" for i, sc in enumerate(f.scores) if sc >= 2]
    return ", ".join(hits)


def build_markdown(cfg, model: str, base: FactBase, corpus: list[Candidate], citekeys,
                   gc, cover: list[str], map_png: Path | None) -> str:
    by_key = {citekeys[i]: c for i, c in enumerate(corpus) if i in citekeys}
    by_id = base.by_id()
    all_facts = [f for fs in base.facts.values() for f in fs]
    n_src = sum(1 for fs in base.facts.values() if fs)
    n_ver = sum(f.verified for f in all_facts)
    n_on = sum(1 for f in all_facts if best(f) >= 2)
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
              f"{pct(n_ver)} · useful or central to a topic {pct(n_on)}  ", ""]
    parts += ["*How to read this: every fact links to its page — the link opens the PDF in "
              "Zotero at that page. A plain quote was found verbatim on that page; anything "
              "located by search instead says so. Each fact is scored against the approved "
              "topics: 3 central, 2 useful, 1 background.*", ""]
    if map_png is not None:
        parts += [f"![Contribution map]({map_png.name})", ""]

    parts += ["## Topics", "",
              "*The subjects every fact was scored against, and the research questions each "
              "serves.*", ""]
    for i, t in enumerate(base.topics, 1):
        qs = ", ".join(f"Q{q}" for q in t.serves) or "no question"
        n3 = sum(1 for f in all_facts if len(f.scores) >= i and f.scores[i - 1] == 3)
        n2 = sum(1 for f in all_facts if len(f.scores) >= i and f.scores[i - 1] == 2)
        parts.append(f"- **T{i}. {t.name}** — serves {qs}; {n3} central, {n2} useful facts")
    parts.append("")
    if cover:
        parts += ["## Coverage against the brief", "",
                  f"*Each research question, from the topics that serve it. THIN means no topic "
                  f"serves it, or its topics hold useful-or-central facts from fewer than "
                  f"{_THIN_SOURCES} sources.*", ""] + cover + [""]

    weight = fact_weight(base.facts)
    from .summarize import top_source_count
    ranked = sorted((ck for ck in weight if weight[ck] > 0 and ck in by_key),
                    key=lambda ck: (rank_key(base.facts[ck]), ck))[:top_source_count(
                        len(corpus), _TOP_QUANTILE)]
    if ranked:
        parts += [f"## Most load-bearing sources (top {_TOP_QUANTILE * 100:g}% of {len(corpus)})",
                  "", "*The sources whose well-grounded facts score highest across the topics — "
                  "the same ranking as the contribution map's innermost ring.*", ""]
        for ck in ranked:
            fs = sorted((f for f in base.facts[ck] if best(f) >= 2 and _good(f)),
                        key=lambda f: -best(f))
            tops = sorted({i + 1 for f in fs for i, sc in enumerate(f.scores) if sc >= 2})
            parts.append(f"- **{by_key[ck].author_year()}** [@{ck}] — topics "
                         f"{', '.join(f'T{n}' for n in tops)}; e.g. {guards_safe(fs[0].text)}")
        parts.append("")

    parts += ["## Facts by topic", "",
              "*Facts scoring useful (2) or central (3) on the topic, central first. Background "
              "facts appear only in the by-publication list below.*", ""]
    for n, t in enumerate(base.topics, 1):
        themes = [th for th in base.themes if th.item == n]
        qs = ", ".join(f"Q{q}" for q in t.serves)
        parts += [f"## T{n}. {t.name}" + (f" ({qs})" if qs else ""), ""]
        if not themes:
            parts += ["*No fact in the corpus scores useful or central on this topic.*", ""]
            continue
        for th in themes:
            fs = sorted((by_id[fid] for fid in th.fact_ids if fid in by_id),
                        key=lambda f: -(f.scores[n - 1] if len(f.scores) >= n else 0))
            parts += [f"### {th.name}", ""]
            if th.synthesis:
                parts += [f"- *{guards_safe(b)}*" for b in th.synthesis] + [""]
            parts += [_fact_line(f, by_key[f.citekey], gc)
                      + (" **(central)**" if len(f.scores) >= n and f.scores[n - 1] == 3 else "")
                      for f in fs if f.citekey in by_key] + [""]

    off = [ck for ck, fs in base.facts.items() if fs and not on_brief(fs)]
    parts += ["## Annotated Bibliography (by publication)", "",
              "*Every source and all of its facts, background ones included, with each fact's "
              "useful-or-central topics. A source with no such fact is marked, and listed again "
              "at the end.*", ""]
    for ck in sorted((k for k in base.facts if k in by_key),
                     key=lambda k: by_key[k].first_author_last.lower()):
        c = by_key[ck]
        mark = " *(no fact is useful to a topic)*" if ck in off else ""
        parts += [f"**{c.full_citation()}** [@{ck}]{mark}", ""]
        fs = base.facts[ck]
        lines = []
        for f in fs:
            tag = _scores_tag(f, base)
            lines.append(f"{_fact_line(f, c, gc, with_key=False)} *[{f.kind}"
                         + (f"; {tag}" if tag else "") + "]*")
        parts += (lines or ["- *(no facts extracted — re-run report)*"]) + [""]
    if off:
        parts += ["### Sources with no fact useful to a topic", "",
                  "*None of their facts scores 2 or more on any topic. Their facts are above. "
                  "Candidates for the next audit — nothing is deleted.*", ""]
        parts += [f"- {by_key[ck].full_citation()} [@{ck}]"
                  for ck in sorted(off, key=lambda k: by_key[k].first_author_last.lower()
                                   if k in by_key else k) if ck in by_key] + [""]
    return "\n".join(parts)


# ── the report ─────────────────────────────────────────────────────────────────

def report(cfg, gc, paths, brain, corpus, notes, citekeys, collection) -> int:
    """The fact-base `report`: extract → score → group → map → render. Returns 0 on success."""
    from .render import _paged_reference
    from .summarize import _export_bibtex
    t0 = time.time()
    items = focus_items(cfg.focus)
    topics = resolve_topics(cfg)
    if not getattr(cfg, "topics", None):
        print("  [note] no approved topics in the config — scoring against the research "
              "questions themselves, which is unstable; add `topics:` to the review's config.",
              file=sys.stderr)
    print(f"\n{runlog.stamp()}[2/4] Extracting facts (up to {_MAX_FACTS} per paper, each "
          f"checked against its page)...", flush=True)
    facts = extract(brain, corpus, cfg, paths, collection, citekeys)

    print(f"\n{runlog.stamp()}[3/4] Scoring facts against {len(topics)} topics...", flush=True)
    score(brain, facts, topics, corpus, citekeys, paths)
    derive_focus(facts, topics)

    print(f"\n{runlog.stamp()}[4/4] Grouping facts by topic and theme...", flush=True)
    vectors: dict[str, list[float]] = {}
    for fs in facts.values():
        for f in fs:
            if best(f) >= 2:
                try:
                    vectors[f.id] = brain.embed(f.text)
                except Exception as e:  # noqa: BLE001
                    print(f"  [warn] embed failed for {f.id} ({e})", file=sys.stderr)
    themes: list[Theme] = []
    for n, t in enumerate(topics, 1):
        fs = [f for f_list in facts.values() for f in f_list
              if len(f.scores) >= n and f.scores[n - 1] >= 2 and f.id in vectors]
        print(f"  {runlog.stamp()}T{n} {t.name[:50]}: {len(fs)} facts from "
              f"{len({f.citekey for f in fs})} sources", flush=True)
        themes += themes_for(brain, n, t.name, fs, vectors)
    base = FactBase(focus=items, facts=facts, themes=themes, topics=topics)
    save_sidecar(paths, base, corpus, citekeys, gc)
    cover = coverage(base)

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
    md = build_markdown(cfg, model, base, corpus, citekeys, gc, cover, map_png)
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
          f"verbatim {sum(f.verified for f in all_facts)} | useful-or-central "
          f"{sum(1 for f in all_facts if best(f) >= 2)} | themes {len(themes)} | "
          f"sidecar work/facts.json")
    return 0

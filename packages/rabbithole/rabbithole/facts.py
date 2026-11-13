"""What the litreview's claim grounding reads a paper with: its page-marked text, a quote found
on its page, a passage widened to whole sentences in the paper's own words, a sentence that
reports another work's result, the project's topics and the prompt that scores against them, and
the links a reader checks a claim at.

This module was the FACT BASE (one call per paper extracting up to ten facts, scored, grouped
into themes, rendered as a review). The fact base was retired on 2026-11-13, when claim
grounding (`ground.py`) became the procedure; its pipeline is archived whole, as it last ran, in
`packages/rabbithole/archive/factbase/` (and at the git tag `factbase-retired`). What stays here
is what grounding, its canaries, the topics review and the gate still use.
"""

from __future__ import annotations

import functools
import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

from .models import Candidate


_MIN_QUOTE_WORDS = 5            # shorter than this, a "verbatim" match proves nothing


def focus_items(focus: str) -> list[str]:
    return [x.strip() for x in (focus or "").split(";") if x.strip()]


def research_questions(cfg) -> list[str]:
    """The review's research questions: the config's `questions:` when it has any, else the
    focus items (a focus is search scope, so its items are only a stand-in)."""
    qs = [str(q).strip() for q in (getattr(cfg, "questions", None) or []) if str(q).strip()]
    return qs or focus_items(cfg.focus)


@dataclass
class Topic:
    name: str
    serves: list = field(default_factory=list)     # 1-based research questions
    covers: str = ""                               # what the topic means and does NOT — a


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
                                            if str(x).strip().isdigit()}),
                             covers=str(t.get("covers") or "").strip()))
    if out:
        return out
    return [Topic(name=q, serves=[n]) for n, q in enumerate(research_questions(cfg), 1)]


_PAGE_SPLIT = re.compile(r"\[p\.(\d+)\]")


# A citation or note marker the extraction put back ("benefit[44]", "[note:3]") is the page's,
# not the quoted words: a quote may carry it or drop it, so matching ignores it on both sides.
_CITE_TAG = re.compile(r"\[(?:note:)?\d{1,3}(?:[,–-]\d{1,3})*\]")


def _norm(text: str) -> str:
    """Text as compared for a verbatim match: case, whitespace, hyphenation, quote style,
    ligatures and citation markers do not count; words do."""
    # NFKC folds every compatibility form, so all the ligatures a PDF keeps (ﬁ ﬂ ﬀ ﬃ ﬄ) read as
    # their letters. Folding only ﬁ and ﬂ left "eﬀect" and "eﬃciency" unmatchable (bench X2,
    # 2026-11-08: 10 verifier spans failed on that alone).
    t = unicodedata.normalize("NFKC", _CITE_TAG.sub("", text or ""))
    # A soft hyphen (U+00AD) is an invisible break point the PDF keeps where a word was split
    # across a line: "data\xad\nbase" on the page is "database" in the quote (DigiPros,
    # 2026-10-12). Dropped with the whitespace after it, so the word rejoins.
    t = re.sub(r"\u00ad\s*", "", t)
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


def find_quote(quote: str, pages: dict[int, str], hint: int | None = None,
               across_pages: bool = False) -> int | None:
    """The page the quote appears on verbatim, or None. The model's page is tried first; a quote
    elided with "..." matches when every piece of five or more words is on that one page. With
    `across_pages`, the pieces may sit on different pages of those given (a passage from the
    abstract joined to one from the findings); the page of the first piece is returned."""
    pieces = [p.strip(" .,;:") for p in (_norm(x) for x in re.split(r"\.\.\.|…", quote or ""))]
    pieces = [p for p in pieces if p]
    pieces = [p for p in pieces if len(p.split()) >= _MIN_QUOTE_WORDS]
    if not pieces:
        return None
    order = ([hint] if hint in pages else []) + [n for n in pages if n != hint]
    for n in order:
        if all(p in pages[n] for p in pieces):
            return n
    if across_pages and len(pieces) > 1:
        found = [next((n for n in order if p in pages[n]), None) for p in pieces]
        if all(n is not None for n in found):
            return found[0]
    return None


# ── locating a passage, and swapping in the paper's own words ────────────────────────────────
# A model's quote is evidence that a passage exists, never the passage itself: models tidy what
# they copy (a ligature written out, a line-end hyphen dropped, a word or two of spacing fixed,
# and sometimes a word that matters). So the matcher may tolerate small differences in FINDING a
# passage, but what it hands on is the paper's text at the place found, widened to whole
# sentences. A tidied-in error ("increases" for "decreases") then disappears, and the claim is
# judged against what the paper really says (PLAN_claim_grounding.html, 2026-11-09).
_TOKEN = re.compile(r"\S+")


_ELISION = re.compile(r"\[\s*(?:\.\s*){3}\]|(?:\.\s*){3}|…")


# A sentence ends at . ! ? before a capital or digit, at a blank line, or at a list bullet. A short
# braced section reference after the stop ("…West Antarctica. {4.4} The annual…", IPCC style) stays
# with its sentence: without that, and the bullet, a passage reached back into the previous item.
_SENTENCE_END = re.compile(r"[.!?][\"”’)\]]*(?:\s*\{[^{}\n]{1,40}\})?\s+(?=[\"“‘(\[]?[A-Z0-9])"
                           r"|\n\s*\n|\s[•▪●◦‣∙]\s*")


_WIDEN_CAP = 600          # characters a passage may grow on each side to reach a sentence edge


_LOCATE_MIN_WORDS = 5     # a piece shorter than this is too common to locate


def _tok(raw: str) -> str:
    """One word as compared: ligatures folded, soft and ordinary hyphens and the punctuation
    around it dropped, case ignored. Digits, decimal points and % count."""
    t = unicodedata.normalize("NFKC", _CITE_TAG.sub("", raw)).replace("\u00ad", "").replace("-", "").lower()
    t = t.translate(str.maketrans({"“": "", "”": "", "‘": "", "’": "'", "–": "", "—": ""}))
    return re.sub(r"^[^\w%]+|[^\w%]+$", "", t)


def _words(text: str) -> list[tuple[str, int, int]]:
    """(word, start, end) for each word of `text`, with page markers left out and a word broken
    across a line ("ini-" then "tially", or a soft hyphen) joined into one."""
    out: list[tuple[str, int, int]] = []
    join = False
    for m in _TOKEN.finditer(text or ""):
        raw = m.group(0)
        if _PAGE_SPLIT.fullmatch(raw):
            continue
        w = _tok(raw)
        if not w:
            continue
        if join and out:
            prev = out[-1]
            out[-1] = (prev[0] + w, prev[1], m.end())
        else:
            out.append((w, m.start(), m.end()))
        join = raw.endswith(("-", "\u00ad")) and len(raw) > 1
    return out


def _page_at(marked: str, offset: int) -> int | None:
    page = None
    for m in _PAGE_SPLIT.finditer(marked):
        if m.start() > offset:
            break
        page = int(m.group(1))
    return page


def _widen(marked: str, start: int, end: int) -> tuple[int, int]:
    """Grow [start, end) to the sentence edges around it, at most _WIDEN_CAP each way."""
    lo = max(0, start - _WIDEN_CAP)
    edges = [m.end() for m in _SENTENCE_END.finditer(marked, lo, start + 1)
             if m.end() <= start]
    s = edges[-1] if edges else (0 if lo == 0 else start)     # the paper's first sentence starts at 0
    m = _SENTENCE_END.search(marked, max(end - 1, 0), min(len(marked), end + _WIDEN_CAP))
    e = m.start() + 1 if m and not m.group(0).startswith("\n") else (m.start() if m else end)
    return s, max(e, end)


_SHORT_SHARE = 0.8        # a line shorter than this share of the paper's usual line is not prose


_LIST_ROW = re.compile(r"^\s*(?:\d{1,3}[a-z]?|[a-z])[.)]\s+\S")   # "18b. Driver…", "a) …"


@functools.lru_cache(maxsize=16)
def _usual_line(marked: str) -> float:
    lens = sorted(len(ln.strip()) for ln in marked.split("\n") if len(ln.strip()) > 3)
    return lens[len(lens) // 2] if lens else 0.0


def _trim_lead(marked: str, s: int, e: int) -> int:
    """Start a passage that crosses a page at the new page when all it has before the page is
    table or list rows: a sentence a table interrupts ends up with the table's last row in
    front of it ("18b. Driver and contributor to all SDGs", Raswant et al. 2025)."""
    m = _PAGE_SPLIT.search(marked, s, e)
    if not m:
        return s
    line0 = marked.rfind("\n", 0, s) + 1             # judge whole lines, not the tail the passage starts in
    lines = [ln for ln in marked[line0:m.start()].split("\n") if ln.strip()]
    short = _SHORT_SHARE * _usual_line(marked)
    if lines and all(len(ln.strip()) < short or _LIST_ROW.match(ln) for ln in lines):
        return min(e, m.end() + len(marked[m.end():e]) - len(marked[m.end():e].lstrip()))
    return s


def _page_span(marked: str, s: int, e: int) -> tuple[int | None, list]:
    """The page holding most of [s, e), and the first and last page it touches."""
    cuts = [(m.start(), int(m.group(1))) for m in _PAGE_SPLIT.finditer(marked, s, e)]
    first = _page_at(marked, s)
    bounds = [(s, first)] + cuts + [(e, None)]
    size = {}
    for (a, pg), (b, _) in zip(bounds, bounds[1:]):
        size[pg] = size.get(pg, 0) + (b - a)
    pages = [pg for _, pg in [(s, first)] + cuts]
    best = max(size, key=lambda pg: size[pg]) if size else first
    return best, [pages[0], pages[-1]]


def _passage_text(raw: str) -> str:
    """The paper's characters, made readable: page markers and soft hyphens out, ligatures read
    as their letters, a word broken at a line end ("de-" then "creases") rejoined, whitespace
    collapsed. No word is changed. A passage that starts mid-sentence (lower case, after a page
    break) is shown with a leading "…"."""
    t = _PAGE_SPLIT.sub(" ", raw)
    # NFKC reads ligatures as their letters, but would also flatten a unit's exponent (km² → km2).
    t = "".join(x if x in "²³" else unicodedata.normalize("NFKC", x)
                for x in re.split(r"([²³])", t)).replace("\u00ad", "")
    t = re.sub(r"(\w)-[ \t]*\n\s*(?=[a-z])", r"\1", t)
    t = " ".join(t.split())
    return "…" + t if t[:1].islower() else t


def _best_window(piece: list[str], paper: list[tuple[str, int, int]]) -> tuple[float, int, int]:
    """The paper's best match for `piece`, as (share of the piece's words matched in order,
    first paper word, last paper word + 1)."""
    from difflib import SequenceMatcher
    n = len(piece)
    where: dict[str, list[int]] = {}
    for i, (w, _, _) in enumerate(paper):
        where.setdefault(w, []).append(i)
    # Every shared word votes for where the piece would start; rare words count the most.
    votes: Counter = Counter()
    for j, w in enumerate(piece):
        hits = where.get(w) or []
        if hits and len(hits) <= 50:
            for i in hits:
                votes[i - j] += 1.0 / len(hits)
    words = [w for w, _, _ in paper]
    best = (0.0, 0, 0)
    for start, _ in votes.most_common(8):
        lo, hi = max(0, start - 3), min(len(paper), start + n + 3)
        sm = SequenceMatcher(None, piece, words[lo:hi], autojunk=False)
        blocks = [b for b in sm.get_matching_blocks() if b.size]
        if not blocks:
            continue
        share = sum(b.size for b in blocks) / n
        first, last = lo + blocks[0].b, lo + blocks[-1].b + blocks[-1].size
        if share > best[0]:
            best = (share, first, last)
    if best[0] < 1.0:
        # A scanned paper's text can run words together ("increasinglyrugged", "bushyradiation":
        # Kauffman 1995, Levinthal 1996): spacing is formatting, so the piece also counts as found
        # whole when its letters occur unbroken with the spaces ignored.
        flat, owner = [], []
        for i, w in enumerate(words):
            flat.append(w)
            owner.extend([i] * len(w))
        at = "".join(flat).find("".join(piece))
        if at >= 0:
            best = (1.0, owner[at], owner[at + len("".join(piece)) - 1] + 1)
    return best


def locate_passage(quote: str, marked: str, min_share: float = 0.9) -> dict:
    """Find a model's quote in a [p.N]-marked paper and return the paper's own words there.

    The quote may be elided with "..." (each piece of five or more words is located on its
    own, anywhere in the paper). A piece is found when at least `min_share` of its words occur
    in order at one place (1.0: every word, only formatting forgiven). Returns
    {"found": bool, "passages": [{"page", "pages", "start", "end", "text", "share"}],
    "nearest": [...]} (`page` holds most of the passage; `pages` is the first and last it touches):
    `passages` are the located pieces widened to whole sentences, in the paper's characters,
    when every piece is found; `nearest` is the best match for each piece either way (what a
    later repair of a rejected claim starts from).
    """
    paper = _words(marked)
    pieces = [[w for w, _, _ in _words(p)] for p in _ELISION.split(quote or "")]
    pieces = [p for p in pieces if len(p) >= _LOCATE_MIN_WORDS]
    nearest: list[dict] = []
    for p in pieces:
        share, i, j = _best_window(p, paper) if paper else (0.0, 0, 0)
        if j > i:
            s, e = _widen(marked, paper[i][1], paper[j - 1][2])
            s = _trim_lead(marked, s, e)
            page, pages = _page_span(marked, s, e)
            nearest.append({"page": page, "pages": pages, "start": s, "end": e,
                            "text": _passage_text(marked[s:e]), "share": round(share, 3)})
        else:
            nearest.append({"page": None, "pages": [None, None], "start": 0, "end": 0, "text": "",
                            "share": 0.0})
    found = bool(pieces) and all(n["share"] >= min_share for n in nearest)
    passages: list[dict] = []
    if found:
        for n in sorted(nearest, key=lambda n: n["start"]):
            if passages and n["start"] <= passages[-1]["end"]:   # overlapping pieces: one passage
                last = passages[-1]
                last["end"] = max(last["end"], n["end"])
                last["text"] = _passage_text(marked[last["start"]:last["end"]])
                last["page"], last["pages"] = _page_span(marked, last["start"], last["end"])
                last["share"] = min(last["share"], n["share"])
            else:
                passages.append(dict(n))
    return {"found": found, "passages": passages, "nearest": nearest}


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


# A REPORTED CLAIM is another work's, carried by this source: "In 2005, Smith argued that PPPs
# ..." on Abdel-Latif 2018's p.8 is Smith's point, not Abdel-Latif's (FirmPathways spot-check,
# 2026-11-01). The quote verifies — it IS on that page — so grounding cannot catch it. The
# pattern is narrow on purpose: a cited author (a year, a bracket, or "et al.") as the subject
# of a reporting verb, or "according to" a citation. "We follow X (2007)" is the source's own.
_CITE_VERBS = (r"(?:has |have |had )?(?:argue[sd]?|suggest(?:s|ed)?|f(?:ou|i)nds?|show(?:s|ed|n)?|"
               r"claim(?:s|ed)?|note[sd]?|propose[sd]?|points? out|pointed out|report(?:s|ed)?|"
               r"observe[sd]?|contend(?:s|ed)?|assert(?:s|ed)?|state[sd]?|demonstrate[sd]?|"
               r"conclude[sd]?|maintain(?:s|ed)?|posit(?:s|ed)?)\b")


_NAME = r"[A-Z][\w'’-]+(?:\s+(?:and|&)\s+[A-Z][\w'’-]+)?"


_REPORTED = re.compile(
    r"\bIn (?:19|20)\d\d,\s+" + _NAME + r"(?:\s+et al\.?)?\s+" + _CITE_VERBS
    + r"|\b" + _NAME + r"\s+et al\.?\s*(?:\(\s*(?:19|20)\d\d[a-z]?\s*\)|\[\d+(?:[,–-]\s*\d+)*\])?\s*"
    + _CITE_VERBS
    + r"|\b" + _NAME + r"\s*(?:\(\s*(?:19|20)\d\d[a-z]?\s*\)|\[\d+(?:[,–-]\s*\d+)*\])\s*" + _CITE_VERBS
    + r"|\b(?:[Aa]ccording to|[Aa]s (?:argued|noted|shown|suggested) by)\s+(?:" + _NAME
    + r"(?:\s+et al\.?)?\s*(?:\(\s*(?:19|20)\d\d|\[\d)|\[\d)")


# ...unless the source takes the cited work up as its own: "We adopt therefore his heuristic".
_ADOPTED = re.compile(r"\b(?:[Ww]e|[Oo]ur study|[Tt]his (?:paper|study|article))\s+"
                      r"(?:\w+\s+){0,2}(?:adopts?|follows?|uses?|employs?|applies|apply|builds? on)\b")


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
Where a topic says what it covers, a fact outside that scope scores 0 or 1 on it, however many
words they share.

Return {{"F1": [{n} scores in topic order], "F2": [...], ...}}"""


def topics_signature(topics: list[Topic]) -> str:
    import hashlib
    # A scope is part of what is scored against; a topic without one hashes as before, so
    # adding scopes to some topics leaves the other projects' caches valid.
    return hashlib.sha1("\n".join(t.name + (f" | {t.covers}" if t.covers else "")
                                  for t in topics).encode()).hexdigest()[:12]


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


def zotero_select(item_key: str, gc) -> str:
    """A link that selects the item in Zotero (the page links open its PDF instead)."""
    if not item_key:
        return ""
    lib = (f"groups/{gc.zotero_library_id}"
           if getattr(gc, "zotero_library_type", "user") == "group" else "library")
    return f"zotero://select/{lib}/items/{item_key}"


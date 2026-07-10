"""Relevance ranking for candidate sources.

Methods:
  * embedding  — cosine similarity of (title+abstract) against the topic/focus,
                 using the local mxbai embedder. Default.
  * citations  — fall back to citation count (useful when abstracts are sparse).
  * llm        — embedding pre-sort, then LLM re-rank of the top N.
"""

from __future__ import annotations

import datetime
import math
import re

from . import runlog
from .brain import Brain
from .filters import is_arxiv, is_review, is_systematic_review
from .models import Candidate


# Field-agnostic anchors for top-tier outlets. Prestige is a thumb on the scale,
# not an override — it nudges well-placed work up, it does not rescue off-topic work.
_TOP_VENUES_EXACT = {
    "nature", "science", "cell", "pnas", "lancet", "the lancet",
    "science advances", "nature communications", "scientific reports",
    "psychological review", "cognition", "neuroimage", "neuron",
    "american economic review", "econometrica",
}
_TOP_VENUE_SUBSTR = (
    "physical review",                      # PRE / PRL / PRX
    "proceedings of the national academy",  # PNAS, long form
    "american economic review",
    "quarterly journal of economics",
    "journal of political economy",
    "research policy",
    "new england journal of medicine",
    "nature ",                              # Nature Human Behaviour, Nature Physics, ...
    "trends in cognitive",
)


def _norm_venue(v: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (v or "").lower()).strip()


def _venue_prestige(c: Candidate) -> float:
    """1.0 for a recognised top-tier outlet, else 0.0."""
    v = _norm_venue(c.venue)
    if not v:
        return 0.0
    if v in _TOP_VENUES_EXACT:
        return 1.0
    return 1.0 if any(s in v for s in _TOP_VENUE_SUBSTR) else 0.0


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _doc_text(c: Candidate) -> str:
    kw = "; ".join(c.keywords) if c.keywords else ""
    return ". ".join(p for p in (c.title, c.abstract, kw) if p).strip()


def _quality_weight(c: Candidate) -> float:
    """Source-type quality multiplier: peer-reviewed journals rank highest."""
    if is_arxiv(c):
        return 0.65
    # Review articles are journal-published; OpenAlex types them "review", which must
    # not drop them below an ordinary article (they used to fall through to 0.85).
    if c.item_type in ("journal-article", "review", "review-article") and c.venue:
        return 1.0
    if c.doi:   # conference paper, book chapter, etc. — has DOI but not a journal
        return 0.85
    return 0.80


def _review_bonus(c: Candidate) -> float:
    """Float review articles toward the top of the cut — a synthesis of the field is
    a high-value entry point, and a systematic review / meta-analysis most of all
    (it is also the best snowball seed). Strongest early, harmless later: relevance
    still dominates, this only breaks ties among comparably-relevant papers."""
    if is_systematic_review(c):
        return 1.0
    if is_review(c):
        return 0.6
    return 0.0


def _cites_per_year(c: Candidate) -> float:
    """Citation velocity: citations per year since publication. Levels the field
    between a recent high-impact paper and an old one that simply had more years to
    accrue cites (48 cites in 2 yrs ≈ 24/yr beats 155 cites over 11 yrs ≈ 14/yr)."""
    if not c.cited_by_count:
        return 0.0
    this_year = datetime.date.today().year
    age = max(1, this_year - (c.year or this_year) + 1)
    return c.cited_by_count / age


def _cite_score(c: Candidate, max_cpy: float) -> float:
    """Log-normalised citation *velocity*, 0-1. Rewards recent high-impact work that
    a raw citation total would bury under older papers."""
    if max_cpy <= 0:
        return 0.0
    return math.log1p(_cites_per_year(c)) / math.log1p(max_cpy)


def rank(candidates: list[Candidate], topic: str, focus: str,
         brain: Brain, method: str = "embedding", rerank_top_n: int = 0,
         target: int = 15, domain_anchor: str = "", exclude_topics: str = "") -> list[Candidate]:
    if not candidates:
        return candidates

    if method == "citations":
        for c in candidates:
            c.relevance = float(c.cited_by_count)
        return sorted(candidates, key=lambda c: c.relevance, reverse=True)

    # 1) embedding pre-sort: title + abstract + keywords vs the topic/focus
    query = f"{topic}. {focus}".strip()
    print(f"  {runlog.stamp()}Embedding {len(candidates)} candidates "
          f"for relevance pre-sort...", flush=True)
    try:
        q_emb = brain.embed(query)
        doc_embs = brain.embed_batch([_doc_text(c) for c in candidates])
        cosines = [_cosine(q_emb, e) for e in doc_embs]
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] embedding rank failed ({e}); falling back to citations")
        for c in candidates:
            c.relevance = float(c.cited_by_count)
        return sorted(candidates, key=lambda c: c.relevance, reverse=True)

    max_cpy = max((_cites_per_year(c) for c in candidates), default=0.0)
    for c, cos in zip(candidates, cosines):
        # semantic relevance × source quality, plus signals that surface high-value
        # work: a citation-velocity term (cites/year, so recent impact isn't buried
        # under older totals), a top-tier-venue bonus, and a review bonus. Quality
        # weight keeps arXiv from crowding out peer-reviewed work; these float the
        # high-impact, elite-venue, and synthesis papers toward the top of the pre-sort.
        c.relevance = (cos * _quality_weight(c)
                       + 0.15 * _cite_score(c, max_cpy)
                       + 0.10 * _venue_prestige(c)
                       + 0.12 * _review_bonus(c))

    ranked = sorted(candidates, key=lambda c: c.relevance, reverse=True)

    # 2) expert LLM re-rank of the top N — the relevance/domain gate (default).
    if method == "llm":
        n = min(rerank_top_n or max(target * 2, 25), len(ranked))
        if n > 0:
            print(f"  {runlog.stamp()}Expert LLM re-rank of top {n}...")
            ranked = _llm_rerank(ranked, topic, focus, brain, n,
                                 domain_anchor, exclude_topics, max_cpy)
    return ranked


def _llm_rerank(ranked: list[Candidate], topic: str, focus: str,
                brain: Brain, top_n: int, domain_anchor: str = "",
                exclude_topics: str = "", max_cpy: float = 0.0) -> list[Candidate]:
    head = ranked[:top_n]
    anchor = (f"\nComponent fields and key concepts (covering ANY of these counts as "
              f"relevant background): {domain_anchor}") if domain_anchor else ""
    excl = (f"\nScore LOW papers that are really about: {exclude_topics}") if exclude_topics else ""
    sys = ("You are an expert academic reviewer curating a literature review reading "
           "list. Score 0-10 for how valuable this paper would be as background or "
           "cited work: 10 = essential, directly addresses the research question; "
           "8-9 = covers a core component field or central method; 6-7 = relevant "
           "background covering at least one important aspect of the topic; "
           "4-5 = tangential; 0-3 = unrelated. For interdisciplinary research, a "
           "paper that substantially covers ANY ONE of the component fields, methods, "
           "or concepts is valuable background and should score ≥ 6. A systematic "
           "review, meta-analysis, or literature review OF the topic is an especially "
           "valuable entry point — score it at the high end of whatever its relevance "
           "warrants. Judge by intellectual relevance, not prestige or citation count."
           + anchor + excl +
           "\nRespond with ONLY the number.")
    jobs = []
    for c in head:
        prompt = (f"Topic: {topic}\nFocus: {focus}\n\n"
                  f"Paper title: {c.title}\n"
                  f"Venue: {c.venue}\n"
                  f"Keywords: {'; '.join(c.keywords)}\n"
                  f"Abstract: {c.abstract[:1500]}\n\n"
                  f"Relevance score (0-10):")
        jobs.append((sys, prompt))
    # 2048 ctx is ample for a title + 1500-char abstract; keeps the KV cache small
    # enough that the worker model fits on a single 8 GB card (no cross-card split).
    scores = brain.worker_map(jobs, num_ctx=2048, desc="relevance scored")
    for c, s in zip(head, scores):
        try:
            topical = float(next(tok for tok in s.split() if tok.replace(".", "").isdigit()))
        except (StopIteration, ValueError):
            topical = 0.0  # unscored -> below the floor, so it drops out
        # .relevance holds the pure topical-fit score; the downstream floor is
        # applied to THIS, so prestige can never rescue off-topic work.
        c.relevance = topical
    # But order the survivors by a quality-augmented key, so that AMONG on-topic
    # papers the high-velocity, top-tier-venue, and review/synthesis ones surface to
    # the top of the list.
    head.sort(key=lambda c: c.relevance + 1.0 * _cite_score(c, max_cpy)
              + 1.5 * _venue_prestige(c) + 1.0 * _review_bonus(c), reverse=True)
    return head + ranked[top_n:]

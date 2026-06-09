"""Relevance ranking for candidate sources.

Methods:
  * embedding  — cosine similarity of (title+abstract) against the topic/focus,
                 using the local mxbai embedder. Default.
  * citations  — fall back to citation count (useful when abstracts are sparse).
  * llm        — embedding pre-sort, then LLM re-rank of the top N.
"""

from __future__ import annotations

import math
import re

from .brain import Brain
from .filters import is_arxiv
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
    if c.item_type == "journal-article" and c.venue:
        return 1.0
    if c.doi:   # conference paper, book chapter, etc. — has DOI but not a journal
        return 0.85
    return 0.80


def _cite_score(c: Candidate, max_cites: int) -> float:
    """Log-normalised citation count, 0-1. Gives a small boost to seminal works."""
    if max_cites <= 0:
        return 0.0
    return math.log1p(c.cited_by_count) / math.log1p(max_cites)


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
    try:
        q_emb = brain.embed(query)
        doc_embs = brain.embed_batch([_doc_text(c) for c in candidates])
        cosines = [_cosine(q_emb, e) for e in doc_embs]
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] embedding rank failed ({e}); falling back to citations")
        for c in candidates:
            c.relevance = float(c.cited_by_count)
        return sorted(candidates, key=lambda c: c.relevance, reverse=True)

    max_cites = max((c.cited_by_count for c in candidates), default=0)
    for c, cos in zip(candidates, cosines):
        # semantic relevance × source quality, plus signals that surface high-value
        # work: a stronger citation term and a top-tier-venue bonus. Quality weight
        # keeps arXiv from crowding out peer-reviewed work; the prestige/citation
        # terms float seminal and elite-venue papers toward the top of the pre-sort.
        c.relevance = (cos * _quality_weight(c)
                       + 0.15 * _cite_score(c, max_cites)
                       + 0.10 * _venue_prestige(c))

    ranked = sorted(candidates, key=lambda c: c.relevance, reverse=True)

    # 2) expert LLM re-rank of the top N — the relevance/domain gate (default).
    if method == "llm":
        n = min(rerank_top_n or max(target * 2, 25), len(ranked))
        if n > 0:
            print(f"  Expert LLM re-rank of top {n}...")
            ranked = _llm_rerank(ranked, topic, focus, brain, n,
                                 domain_anchor, exclude_topics, max_cites)
    return ranked


def _llm_rerank(ranked: list[Candidate], topic: str, focus: str,
                brain: Brain, top_n: int, domain_anchor: str = "",
                exclude_topics: str = "", max_cites: int = 0) -> list[Candidate]:
    head = ranked[:top_n]
    anchor = f"\nA paper is ON-TOPIC only if it is about: {domain_anchor}" if domain_anchor else ""
    excl = f"\nTreat as OFF-TOPIC (score low) papers that are really about: {exclude_topics}" if exclude_topics else ""
    sys = ("You are an expert reviewer judging whether a paper genuinely fits a "
           "specific research topic. Score 0-10 for topical relevance to the topic "
           "and focus (10 = directly on-topic and central; 0 = unrelated). Judge by "
           "subject fit, NOT by how famous or highly-cited the paper is — a famous "
           "paper from an adjacent field is still off-topic." + anchor + excl +
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
    scores = brain.worker_map(jobs, num_ctx=4096)
    for c, s in zip(head, scores):
        try:
            topical = float(next(tok for tok in s.split() if tok.replace(".", "").isdigit()))
        except (StopIteration, ValueError):
            topical = 0.0  # unscored -> below the floor, so it drops out
        # .relevance holds the pure topical-fit score; the downstream floor is
        # applied to THIS, so prestige can never rescue off-topic work.
        c.relevance = topical
    # But order the survivors by a quality-augmented key, so that AMONG on-topic
    # papers the seminal and top-tier-venue ones surface to the top of the list.
    head.sort(key=lambda c: c.relevance + 1.0 * _cite_score(c, max_cites)
              + 1.5 * _venue_prestige(c), reverse=True)
    return head + ranked[top_n:]

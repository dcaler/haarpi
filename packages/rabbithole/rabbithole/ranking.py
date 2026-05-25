"""Relevance ranking for candidate sources.

Methods:
  * embedding  — cosine similarity of (title+abstract) against the topic/focus,
                 using the local mxbai embedder. Default.
  * citations  — fall back to citation count (useful when abstracts are sparse).
  * llm        — embedding pre-sort, then LLM re-rank of the top N.
"""

from __future__ import annotations

import math

from .brain import Brain
from .models import Candidate


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


def rank(candidates: list[Candidate], topic: str, focus: str,
         brain: Brain, method: str = "embedding", rerank_top_n: int = 0,
         target: int = 15) -> list[Candidate]:
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
        for c, e in zip(candidates, doc_embs):
            c.relevance = _cosine(q_emb, e)
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] embedding rank failed ({e}); falling back to citations")
        for c in candidates:
            c.relevance = float(c.cited_by_count)
        return sorted(candidates, key=lambda c: c.relevance, reverse=True)
    ranked = sorted(candidates, key=lambda c: c.relevance, reverse=True)

    # 2) optional expert LLM re-rank of the top N — ONLY when method == "llm".
    #    (embedding-only is the default: fast, no LLM inference, no timeouts.)
    if method == "llm":
        n = min(rerank_top_n or max(target * 2, 25), len(ranked))
        if n > 0:
            print(f"  Expert LLM re-rank of top {n}...")
            ranked = _llm_rerank(ranked, topic, focus, brain, n)
    return ranked


def _llm_rerank(ranked: list[Candidate], topic: str, focus: str,
                brain: Brain, top_n: int) -> list[Candidate]:
    head = ranked[:top_n]
    sys = ("You are an expert reviewer judging whether a paper genuinely fits a "
           "specific research topic. Score 0-10 for topical relevance to the topic "
           "and focus (10 = directly on-topic and central; 0 = unrelated). Judge by "
           "subject fit, NOT by how famous or highly-cited the paper is. "
           "Respond with ONLY the number.")
    jobs = []
    for c in head:
        prompt = (f"Topic: {topic}\nFocus: {focus}\n\n"
                  f"Paper title: {c.title}\n"
                  f"Keywords: {'; '.join(c.keywords)}\n"
                  f"Abstract: {c.abstract[:1500]}\n\n"
                  f"Relevance score (0-10):")
        jobs.append((sys, prompt))
    scores = brain.worker_map(jobs, num_ctx=4096)
    for c, s in zip(head, scores):
        try:
            c.relevance = float(next(tok for tok in s.split() if tok.replace(".", "").isdigit()))
        except (StopIteration, ValueError):
            c.relevance = 0.0  # unscored -> below the floor, so it drops out
    head.sort(key=lambda c: c.relevance, reverse=True)
    return head + ranked[top_n:]

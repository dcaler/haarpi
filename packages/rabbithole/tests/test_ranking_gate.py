"""Frozen tests for the reframed relevance gate (DESIGN_corpus_audit.md, part A). GPU-free.

The gate must score CONCEPTUAL TRANSFER to the research question, not lexical coverage — a
shared term used in a different sense (a homograph) is not relevance. The old prompt's leniency
("covers ANY ONE component ... score >= 6") is exactly what let a homograph through, so it must
be gone; and the model's score must still flow to `.relevance` so the downstream floor can drop
a false-friend.
"""
from __future__ import annotations

from rabbithole import ranking
from rabbithole.models import Candidate


class RerankBrain:
    """Captures the (system, prompt) jobs and returns scripted scores in order."""
    def __init__(self, scores):
        self.scores = list(scores)
        self.jobs = []

    def worker_map(self, jobs, **kw):
        self.jobs = list(jobs)
        return [str(s) for s in self.scores]


def _cand(title, abstract="", keywords=()):
    return Candidate(title=title, abstract=abstract, keywords=list(keywords), venue="J")


def test_the_gate_prompt_scores_transfer_and_names_the_homograph_trap():
    brain = RerankBrain([5])
    ranking._llm_rerank([_cand("AutoDock Vina", "molecular docking of ligands")],
                        "agent-based modelling", "docking of adaptive agents", brain, top_n=1)
    sys = brain.jobs[0][0].lower()
    assert "transfer" in sys                              # judges conceptual transfer
    assert "sense" in sys                                 # names the word-sense / homograph trap
    # the old leniency that rewarded lexical coverage must be gone
    assert "any one" not in sys
    assert "≥ 6" not in sys and ">= 6" not in sys


def test_the_model_score_flows_to_relevance_so_the_floor_can_drop_a_false_friend():
    homograph = _cand("AutoDock Vina", "molecular docking")
    transfer = _cand("Adaptive agents that dock", "model-to-model alignment")
    brain = RerankBrain([1, 8])                           # homograph low, transfer high
    out = ranking._llm_rerank([homograph, transfer],
                              "ABM", "agent docking", brain, top_n=2)
    by_title = {c.title: c.relevance for c in out}
    assert by_title["AutoDock Vina"] == 1.0              # pure topical score, not prestige-rescued
    assert by_title["Adaptive agents that dock"] == 8.0


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failures = 0
    for fn in fns:
        try:
            fn(); print(f"  PASS  {fn.__name__}")
        except Exception:  # noqa: BLE001
            failures += 1; print(f"  FAIL  {fn.__name__}"); traceback.print_exc()
    print(f"\n{len(fns) - failures}/{len(fns)} passed")
    raise SystemExit(1 if failures else 0)

"""Ranking has to see what the search was for.

The regression these pin. Moving a cycle's asks out of `focus` (so they stop diluting query
generation) left the RANKER blind to them: `rank` scored every candidate against
"topic. focus" alone. elephantRoom's cycle-7 gather found 243 papers for a consumption-smoothing
ask and 274 for a reshoring ask, scored all of them against a focus about carbon taxes and
border adjustments, ranked them below the 90-slot LLM re-rank head, and curated 1 and 0. The
searches worked; the ranking could not see what they were for.

Runnable two ways:
    pytest tests/test_ask_ranking.py
    python tests/test_ask_ranking.py
"""

from __future__ import annotations

from rabbithole import ranking
from rabbithole.models import Candidate


class _Bag:
    """Bag-of-words embedder over a FIXED vocabulary.

    Fixed because queries and documents are embedded in separate calls: a vocabulary derived
    per call puts them in different spaces and makes the cosines meaningless — which is exactly
    the mistake that made an earlier version of this test disagree with the code for reasons
    that had nothing to do with the code."""

    _VOCAB = sorted({w for t in [
        "carbon taxes climate clubs border adjustments leakage",
        "consumption smoothing household savings unemployment",
        "topic",
    ] for w in t.split()})

    def embed_batch(self, texts):
        return [[1.0 if w in t.lower().split() else 0.0 for w in self._VOCAB] for t in texts]


_FOCUS = "carbon taxes climate clubs border adjustments leakage"
_ASK = "consumption smoothing household savings unemployment"


def _cands():
    return [Candidate(title="Carbon taxes and border adjustments",
                      abstract="carbon taxes climate clubs leakage"),
            Candidate(title="Household savings and consumption smoothing",
                      abstract="consumption smoothing household savings unemployment")]


def test_without_the_asks_an_ask_specific_paper_sinks():
    """The behaviour being fixed, pinned so the regression is recognisable if it returns."""
    ranked = ranking.rank(_cands(), "topic", _FOCUS, _Bag(), method="embedding")
    assert ranked[-1].title.startswith("Household savings")


def test_a_paper_answering_an_ask_is_ranked_on_that_ask():
    ranked = ranking.rank(_cands(), "topic", _FOCUS, _Bag(), method="embedding",
                          gather_topics=[_ASK])
    assert any(c.title.startswith("Household savings") for c in ranked[:1] + ranked[1:2])
    household = next(c for c in ranked if c.title.startswith("Household"))
    assert household.best_query == _ASK, "and it is attributed to the ask it answers"


def test_scoring_is_best_of_not_an_average():
    """A paper answers the standing question or a reviewer's specific one; being irrelevant to
    the other is not evidence against it. Averaging would punish exactly the ask-specific work
    the cycle exists to find."""
    only_ask = ranking.rank(_cands(), "topic", _FOCUS, _Bag(), method="embedding",
                            gather_topics=[_ASK])
    no_ask = ranking.rank(_cands(), "topic", _FOCUS, _Bag(), method="embedding")
    h_with = next(c.relevance for c in only_ask if c.title.startswith("Household"))
    h_without = next(c.relevance for c in no_ask if c.title.startswith("Household"))
    assert h_with > h_without
    f_with = next(c.relevance for c in only_ask if c.title.startswith("Carbon"))
    f_without = next(c.relevance for c in no_ask if c.title.startswith("Carbon"))
    assert f_with >= f_without, "the focus paper is not demoted by the ask existing"


def test_an_empty_ask_list_changes_nothing():
    a = ranking.rank(_cands(), "topic", _FOCUS, _Bag(), method="embedding")
    b = ranking.rank(_cands(), "topic", _FOCUS, _Bag(), method="embedding", gather_topics=[])
    assert [c.title for c in a] == [c.title for c in b]


# ── a fair score is not a fair slot ──────────────────────────────────────────

def _c(title, ask):
    x = Candidate(title=title)
    x.best_query = ask
    return x


def test_every_ask_reaches_the_rerank_head():
    """Asks are not equally well served by the literature. One with hundreds of strong matches
    would fill the head on its own, and the thin ask — the one most likely to be a real gap,
    and the reason the cycle exists — would never reach the judge that decides what is curated."""
    ranked = [_c(f"loud-{i}", "loud ask") for i in range(20)] + [_c("thin-1", "thin ask")]
    head = ranking._head_with_ask_share(ranked, ["loud ask", "thin ask"], 10)[:10]
    assert any(c.title == "thin-1" for c in head)


def test_the_head_keeps_its_size_and_loses_nobody():
    ranked = [_c(f"a-{i}", "ask one") for i in range(8)] + [_c(f"b-{i}", "ask two") for i in range(8)]
    out = ranking._head_with_ask_share(ranked, ["ask one", "ask two"], 6)
    assert len(out) == len(ranked), "reordered, never dropped"
    assert {c.title for c in out} == {c.title for c in ranked}
    assert len({id(c) for c in out}) == len(ranked), "and never duplicated"


def test_overall_rank_still_fills_the_rest_of_the_head():
    """The guarantee is a floor per ask, not a quota that displaces the best papers."""
    ranked = [_c(f"top-{i}", "ask one") for i in range(9)] + [_c("thin-1", "thin ask")]
    head = ranking._head_with_ask_share(ranked, ["ask one", "thin ask"], 10)[:10]
    assert sum(1 for c in head if c.best_query == "ask one") == 9


def test_no_asks_leaves_the_order_untouched():
    ranked = [_c(f"x-{i}", "") for i in range(5)]
    assert ranking._head_with_ask_share(ranked, [], 3) is ranked


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception:  # noqa: BLE001
            failures += 1
            print(f"  FAIL  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failures}/{len(fns)} passed")
    raise SystemExit(1 if failures else 0)

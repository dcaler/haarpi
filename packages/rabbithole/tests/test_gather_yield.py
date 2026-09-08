"""rabbitHole `gather` — per-ask query slots and the yield report that makes a miss visible.

The failure these pin: elephantRoom's fourth gather ran with "consumption smoothing" and
"nation-level supply chain dependency reduction" in its focus, returned 45 curated sources
and ZERO on either topic, and reported success. Two cycles later a section was drafted on
each, out of whatever ranked nearest. A corpus-wide total cannot show that; a per-ask one can.

Runnable two ways:
    pytest tests/test_gather_yield.py
    python tests/test_gather_yield.py
"""

from __future__ import annotations

import types

from rabbithole import discover, filters
from rabbithole.models import Candidate


def _cfg(topics):
    return types.SimpleNamespace(topic="climate policy", focus="carbon taxes",
                                 domain_anchor="", exclude_topics="", gather_topics=topics)


def _cand(title, found_by, doi=""):
    return Candidate(title=title, doi=doi or f"10.1/{title}", found_by=list(found_by))


# ── provenance survives the merge ────────────────────────────────────────────

def test_dedupe_unions_the_queries_that_found_a_paper():
    """A paper reached by both a general query and a reviewer's ask counts toward the ask —
    picking one would under-report exactly the topic the cycle exists to cover."""
    merged = filters.dedupe([_cand("same", ["general query"], doi="10.1/x"),
                             _cand("same", ["ask query"], doi="10.1/x")])
    assert len(merged) == 1
    assert set(merged[0].found_by) == {"general query", "ask query"}


# ── the yield report ─────────────────────────────────────────────────────────

def test_an_ask_that_found_nothing_is_reported_as_nothing():
    cfg = _cfg(["consumption smoothing"])
    origin = {"general": "", "household savings buffer": "consumption smoothing"}
    kept = [_cand("off-topic", ["general"])]
    out = discover._per_topic_yield(cfg, origin, kept, kept)
    assert out["consumption smoothing"] == {"found": 0, "curated": 0}


def test_an_ask_that_found_papers_reports_both_found_and_curated():
    """`found` is what the ask's queries surfaced; `curated` is what survived ranking. A topic
    that finds plenty and curates none is its own signal — the hits were off-domain."""
    cfg = _cfg(["consumption smoothing"])
    origin = {"household savings buffer": "consumption smoothing"}
    a = _cand("kept paper", ["household savings buffer"], doi="10.1/a")
    b = _cand("dropped paper", ["household savings buffer"], doi="10.1/b")
    out = discover._per_topic_yield(cfg, origin, [a, b], [a])
    assert out["consumption smoothing"] == {"found": 2, "curated": 1}


def test_no_asks_means_no_report():
    """A gather with no reviewer asks (a first run) has nothing to report per-ask."""
    assert discover._per_topic_yield(_cfg([]), {}, [], []) == {}


def test_each_ask_is_counted_separately():
    """Three asks fused into one number is the shape that hid the failure; they stay distinct."""
    cfg = _cfg(["ask one", "ask two"])
    origin = {"q1": "ask one", "q2": "ask two"}
    kept = [_cand("p", ["q1"])]
    out = discover._per_topic_yield(cfg, origin, kept, kept)
    assert out["ask one"]["curated"] == 1
    assert out["ask two"]["curated"] == 0


# ── guaranteed per-ask query slots ───────────────────────────────────────────

class _Brain:
    """Coordinator that returns the general set once, then a fixed expansion per ask."""

    def __init__(self):
        self.calls = []

    def coordinator(self, prompt, sys_prompt, **kw):
        self.calls.append(prompt)
        if "Sub-topic to cover" in prompt:
            return '["household savings buffer", "precautionary saving climate"]'
        return '["' + '", "'.join(f"general {i}" for i in range(10)) + '"]'


def test_an_ask_gets_its_own_queries_outside_the_general_cap(monkeypatch):
    """The general set is capped at 10 however many asks there are; per-ask queries are added
    ON TOP of that cap, so an ask cannot be crowded out by the standing scope."""
    monkeypatch.setattr(discover, "_critique_revise_queries", lambda b, c, q: q)
    cfg = _cfg(["consumption smoothing"])
    queries, origin = discover._generate_queries(_Brain(), cfg)
    owned = [q for q in queries if origin.get(q) == "consumption smoothing"]
    assert owned == ["household savings buffer", "precautionary saving climate"]
    assert len(queries) > 10, "the ask's queries are additional, not a slice of the general set"


def test_a_failed_expansion_still_searches_the_ask_verbatim(monkeypatch):
    """Failing here would silently drop the reviewer's request — the exact failure this
    channel exists to close — so it degrades to searching the ask as written."""
    monkeypatch.setattr(discover, "_critique_revise_queries", lambda b, c, q: q)

    class _Broken(_Brain):
        def coordinator(self, prompt, sys_prompt, **kw):
            if "Sub-topic to cover" in prompt:
                raise RuntimeError("model down")
            return super().coordinator(prompt, sys_prompt, **kw)

    _, origin = discover._generate_queries(_Broken(), _cfg(["supply chain dependency"]))
    assert "supply chain dependency" in origin
    assert origin["supply chain dependency"] == "supply chain dependency"


if __name__ == "__main__":
    import traceback

    class _MP:
        def setattr(self, obj, name, val): setattr(obj, name, val)

    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failures = 0
    for fn in fns:
        try:
            fn(_MP()) if fn.__code__.co_argcount else fn()
            print(f"  PASS  {fn.__name__}")
        except Exception:  # noqa: BLE001
            failures += 1
            print(f"  FAIL  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failures}/{len(fns)} passed")
    raise SystemExit(1 if failures else 0)

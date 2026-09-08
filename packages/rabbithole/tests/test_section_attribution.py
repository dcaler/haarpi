"""One reviewer request, one section, one honest answer.

The cycle these pin. Three separate "add a section on X" comments sat on the same heading of
elephantRoom's draft. `revise` grouped section asks by ANCHOR PARAGRAPH, so all three fused
into a single ask; the drafter returned two sections; attribution was positional, so section
one was credited to all three comments in three byte-identical replies and section two — a
whole strand spliced into the review — was named in no reply at all. The completion summary
said "1 paragraph(s) revised" and stopped there.

Underneath that: nothing checked whether the corpus could carry a requested section. A
shortlist is a ranking, and a ranking always has a top, so the drafter was handed the twelve
nearest sources however far away they were and wrote a sound section out of them.

Runnable two ways:
    pytest tests/test_section_attribution.py
    python tests/test_section_attribution.py
"""

from __future__ import annotations

import types

from rabbithole import graft, revise
from rabbithole.summarize import Section


# ── one ask per comment, not per anchor paragraph ────────────────────────────

def _split(anchors, cmap):
    """The production expression, kept in one place so the test pins the real thing."""
    return [({**a, "ids": [i]}, [cmap[i]["text"]])
            for a in anchors for i in a["ids"]
            if i in cmap and cmap[i]["text"] and revise._is_section_ask(cmap[i]["text"])]


def test_three_asks_on_one_heading_stay_three_asks():
    """The elephantRoom shape exactly: a reviewer who leaves three notes in one place has
    made three requests, and fusing them is what left a section belonging to nobody."""
    anchors = [{"para": 14, "ids": [3, 4, 5]}]
    cmap = {3: {"text": "I'd like to add an entire section on household distributional equity."},
            4: {"text": "We can also introduce a section on consumption smoothing."},
            5: {"text": "I'd also like to add a section on supply chain dependency."}}
    asks = _split(anchors, cmap)
    assert len(asks) == 3
    assert [a["ids"] for a, _ in asks] == [[3], [4], [5]]
    assert all(a["para"] == 14 for a, _ in asks), "each keeps the anchor the reviewer chose"


def test_a_non_section_comment_beside_a_section_ask_is_left_for_the_reviser():
    """A paragraph can carry a section ask AND an ordinary edit; only the ask is grafted."""
    anchors = [{"para": 9, "ids": [1, 2]}]
    cmap = {1: {"text": "add a section on carbon leakage"},
            2: {"text": "this sentence is unclear, please reword it"}}
    asks = _split(anchors, cmap)
    assert [a["ids"] for a, _ in asks] == [[1]]


# ── the corpus-support sensor ────────────────────────────────────────────────

class _Brain:
    def __init__(self, verdict): self.verdict = verdict

    def coordinator(self, prompt, sys_prompt, **kw):
        return self.verdict


_CFG = types.SimpleNamespace(topic="climate policy", focus="")


def test_a_section_the_corpus_cannot_carry_is_not_drafted():
    """The supply-chain failure: sources that share the word 'decoupling' in another sense
    rank top precisely BECAUSE they share it, so similarity cannot catch this — only sense can."""
    sec = Section("Supply chain decoupling", "Nations reduce external dependency",
                  candidates=["haberl2020"])
    full = {"haberl2020": "Haberl 2020 — decoupling economic growth from emissions is rare."}
    ok = graft._corpus_supports(_Brain(
        '{"verdict": "UNSUPPORTED", "missing": "papers on supply-chain dependency", '
        '"instead": "decoupling growth from emissions"}'), _CFG, sec, full)
    assert ok is False
    assert "supply-chain dependency" in sec.unsupported
    assert "decoupling growth from emissions" in sec.unsupported, "names what it found instead"


def test_a_supported_section_passes_through_untouched():
    sec = Section("Carbon pricing and innovation", "Pricing redirects R&D",
                  candidates=["aghion2016"])
    full = {"aghion2016": "Aghion 2016 — fuel prices raise clean patenting."}
    assert graft._corpus_supports(_Brain('{"verdict": "SUPPORTED"}'), _CFG, sec, full) is True
    assert sec.unsupported == ""


def test_no_shortlisted_sources_at_all_is_reported_not_drafted():
    sec = Section("Consumption smoothing", "Households buffer shocks from savings")
    assert graft._corpus_supports(_Brain("{}"), _CFG, sec, {}) is False
    assert sec.unsupported


def test_an_unreadable_verdict_drafts_anyway():
    """Fails SAFE toward the reviewer's ask: a wrong decline costs one comment restated next
    cycle; a wrong draft costs a whole revise and reads as finished work."""
    sec = Section("Some section", "Some claim", candidates=["k"])
    assert graft._corpus_supports(_Brain("not json at all"), _CFG, sec, {"k": "a line"}) is True
    assert sec.unsupported == ""


def test_a_brain_that_raises_drafts_anyway():
    class _Broken:
        def coordinator(self, *a, **kw): raise RuntimeError("model down")
    sec = Section("Some section", "Some claim", candidates=["k"])
    assert graft._corpus_supports(_Broken(), _CFG, sec, {"k": "a line"}) is True


# ── the replies that go back ─────────────────────────────────────────────────

def _replies(outcomes):
    """The reply text `revise` would thread onto each comment, captured at the shared writer."""
    import haarpi.redline as hr
    sent = {}
    saved = hr.add_replies
    hr.add_replies = lambda path, replies, *a, **kw: (sent.update(replies), len(replies))[1]
    try:
        revise._reply_to_comments(None, outcomes,
                                  {"queued": False, "tier": None, "needs_report": False})
    finally:
        hr.add_replies = saved
    return sent


def test_each_grafted_section_is_named_in_its_own_reply():
    """Three identical replies naming one section is the shape that hid a whole strand."""
    sent = _replies({"3": "grafted:Household distributional equity",
                     "4": "grafted:Consumption smoothing buffers shocks",
                     "5": "grafted:Supply chain decoupling"})
    assert len(set(sent.values())) == 3, "three asks, three distinct answers"
    assert "Household distributional equity" in sent["3"]
    assert "Consumption smoothing buffers shocks" in sent["4"]
    assert "Supply chain decoupling" in sent["5"]


def test_an_unsupported_section_says_what_was_missing_not_that_it_was_covered():
    """'Already covered' and 'the corpus has nothing' call for opposite next moves. Reporting
    the second as the first is how three days passed with the document looking finished."""
    sent = _replies({"5": "unsupported:papers on supply-chain dependency "
                          "(closest available: decoupling growth from emissions)"})
    reply = sent["5"]
    assert "supply-chain dependency" in reply
    assert "cannot carry" in reply
    assert "already covers" not in reply
    covered = _replies({"6": "section_covered"})["6"]
    assert covered != reply and "already covers" in covered


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
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

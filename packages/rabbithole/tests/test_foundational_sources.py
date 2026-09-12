"""Foundational sources, and the trace that makes a dropped source diagnosable.

A postIneq review shipped on 2026-09-10 without citing Epstein's *Inverse Generative Social
Science* — the single most on-topic paper in its corpus, ranked #1 of 24 candidates, present
in Zotero, indexed at 88 chunks, correctly annotated — in a review whose own section on
inverse generative social science cited Epstein 1999 instead.

Nothing was broken. The annotation said, accurately, that the paper "does not report
quantitative empirical findings, effect sizes, or statistical data", that sentence went
verbatim into the digest line the drafter weighs, and the review is assembled from
triangulatable numeric claims. A framework paper announcing it has no numbers loses that
contest every time. It then lost the second contest too: its digest line is long, and the
shortlist's character budget drops long lines.

So: name the role instead of the absence, reserve shortlist capacity, forbid "no quantitative
findings" as a rejection reason, and record what the synthesis saw so the next one is
diagnosable without reverse-engineering work/located/.

Runnable two ways:
    pytest tests/test_foundational_sources.py
    python tests/test_foundational_sources.py
"""

from __future__ import annotations

import json

from rabbithole import summarize
from rabbithole.summarize import Section

# The real annotation that lost the contest.
EPSTEIN = {
    "argument": "iGSS reverses ABM by evolving micro-rules that generate macro patterns.",
    "findings": ("The paper is a theoretical essay and methodological overview that does not "
                 "report quantitative empirical findings, effect sizes, or statistical data "
                 "from a specific simulation experiment."),
    "relevance": "Foundational framework for inverse modelling.",
    "themes": ["iGSS", "Evolutionary Computing"],
}
EMPIRICAL = {
    "argument": "Portfolio choice drives wealth divergence.",
    "findings": "Top-percentile households allocate 58.76% to equity at a 5.56% return.",
    "relevance": "Quantifies the mechanism.",
    "themes": ["portfolio choice"],
}


class _Src:
    def __init__(self, cites=0):
        self.cited_by_count = cites

    def author_year(self):
        return "Epstein 2023"


# ── detection ────────────────────────────────────────────────────────────────

def test_detects_the_annotation_that_shipped():
    assert summarize.is_foundational(EPSTEIN)


def test_does_not_fire_on_empirical_findings():
    assert not summarize.is_foundational(EMPIRICAL)


def test_does_not_fire_on_a_number_free_but_empirical_finding():
    """Absence of digits is not the signal — plenty of real findings are stated in words."""
    assert not summarize.is_foundational(
        {"findings": "Redistribution prevented collapse in every condition tested."})


def test_detects_named_genres():
    for f in ("A conceptual framework for validation.",
              "This position paper argues for pluralism.",
              "A methodological overview of docking."):
        assert summarize.is_foundational({"findings": f}), f


def test_tolerates_missing_annotation():
    assert not summarize.is_foundational({})
    assert not summarize.is_foundational(None)


QUALITATIVE = {
    "argument": "Intervention effects depend on baseline imbalance.",
    "findings": ("The paper reports that baseline and baseline-with-intervention results are "
                 "identical when no gender imbalance exists, but does not provide specific "
                 "quantitative effect sizes."),
    "relevance": "Bears on the intervention question.",
    "themes": ["intervention"],
}


# ── the three-way split: foundational vs qualitative vs empirical ────────────

def test_a_real_finding_with_a_disclaimer_is_not_foundational():
    """The distinction that stops the fix destroying information. This paper HAS a finding;
    the annotation merely adds that it quotes no effect sizes. Calling it foundational would
    overwrite the finding with a role label."""
    assert not summarize.is_foundational(QUALITATIVE)
    assert summarize.reports_qualitatively(QUALITATIVE)


def test_the_disclaimer_is_excised_and_the_finding_kept():
    kept = summarize._findings_without_the_negation(QUALITATIVE)
    assert "baseline-with-intervention results are identical" in kept
    assert "does not provide" not in kept


def test_excision_handles_an_and_joined_clause():
    """'...synthesis of calibration techniques and does not report quantitative findings' —
    the conjunction that leaked the disclaimer back into the digest on the first pass."""
    note = {"findings": ("The paper provides a qualitative synthesis of calibration "
                         "techniques and does not report quantitative findings.")}
    kept = summarize._findings_without_the_negation(note)
    assert kept == "The paper provides a qualitative synthesis of calibration techniques"


def test_epstein_is_foundational_by_genre_not_by_remainder():
    """Its findings field is nothing but the declaration, and it also names the genre."""
    assert summarize.is_foundational(EPSTEIN)
    assert not summarize.reports_qualitatively(EPSTEIN)


def test_qualitative_digest_keeps_the_finding():
    line = summarize._digest([_Src()], [QUALITATIVE], {0: "bullinaria2018"})
    assert "(qualitative)" in line
    assert "results are identical" in line          # the finding survived
    assert "does not provide specific" not in line  # the disclaimer did not
    assert "cite it for its framework" not in line  # not mislabelled as a framework paper


# ── the digest names the role instead of the absence ─────────────────────────

def test_digest_replaces_the_disqualifying_sentence_with_a_role():
    line = summarize._digest([_Src()], [EPSTEIN], {0: "epsteinInverse2023"})
    assert "(foundational)" in line
    assert "does not report quantitative" not in line
    assert "cite it for its framework" in line


def test_digest_leaves_empirical_findings_alone():
    line = summarize._digest([_Src()], [EMPIRICAL], {0: "zambrano2019"})
    assert "(foundational)" not in line
    assert "58.76%" in line


def test_planner_also_sees_the_marking():
    """The planner works off compact lines; a section plan that never anticipates the
    framework source gives the shortlist nothing to retrieve it into."""
    compact = summarize._compact_lines([_Src()], [EPSTEIN], {0: "epsteinInverse2023"})
    assert "(foundational)" in compact["epsteinInverse2023"]


# ── reserved shortlist capacity ──────────────────────────────────────────────

def _one_hot(i, n=3):
    v = [0.0] * n
    v[i] = 1.0
    return v


class _Brain:
    def __init__(self, vectors):
        self._v = vectors

    def embed_batch(self, texts):
        return [self._v.get(t, [1.0, 0.0, 0.0]) for t in texts]


def test_a_long_foundational_line_survives_the_character_budget():
    """The second contest it lost. A framework paper's digest is long, and the budget drops
    long lines — so it ranked and was still dropped."""
    keys = [f"emp{i}" for i in range(5)] + ["found1"]
    compact = {k: f"- [@{k}] compact" for k in keys}
    full = {k: "- [@%s] " % k + "x" * 3_000 for k in keys}
    full["found1"] = "- [@found1] " + "y" * 3_000
    sec = Section(heading="H", claim="C")
    vectors = {compact[k]: _one_hot(0) for k in keys}
    vectors["H. C"] = _one_hot(0)
    brain = _Brain(vectors)

    summarize._shortlist(brain, [sec], compact, full, top_k=3,
                         foundational={"found1"})
    assert "found1" in sec.candidates, "reserved capacity did not hold the framework source"


def test_a_low_ranked_foundational_source_is_still_reached():
    """The reservation searches deeper than the ordinary window, because ranking low against
    an empirically-phrased section claim is the exact penalty it exists to offset."""
    keys = [f"emp{i}" for i in range(10)] + ["found1"]
    compact = {k: f"- [@{k}] compact" for k in keys}
    full = {k: f"- [@{k}] short" for k in keys}
    sec = Section(heading="H", claim="C")
    vectors = {compact[k]: _one_hot(0) for k in keys}
    vectors[compact["found1"]] = [0.6, 0.8, 0.0]      # genuinely lower cosine, not a tie
    vectors["H. C"] = _one_hot(0)
    summarize._shortlist(_Brain(vectors), [sec], compact, full, top_k=4,
                         foundational={"found1"})
    assert "found1" in sec.candidates


def test_reserving_does_not_reorder_the_shortlist():
    """Reserving must not tell the drafter the framework source ranks first."""
    keys = ["emp0", "emp1", "found1"]
    compact = {k: f"- [@{k}] compact" for k in keys}
    full = {k: f"- [@{k}] short" for k in keys}
    sec = Section(heading="H", claim="C")
    vectors = {compact["emp0"]: _one_hot(0), compact["emp1"]: _one_hot(0),
               compact["found1"]: _one_hot(1), "H. C": _one_hot(0)}
    summarize._shortlist(_Brain(vectors), [sec], compact, full, top_k=3,
                         foundational={"found1"})
    assert sec.candidates[-1] == "found1"      # admitted, but still ranked last


def test_reservation_is_capped():
    """Reserved slots must not crowd out the evidence the section actually argues from."""
    keys = [f"f{i}" for i in range(8)]
    compact = {k: f"- [@{k}] compact" for k in keys}
    full = {k: f"- [@{k}] short" for k in keys}
    sec = Section(heading="H", claim="C")
    vectors = {compact[k]: _one_hot(0) for k in keys}
    vectors["H. C"] = _one_hot(0)
    summarize._shortlist(_Brain(vectors), [sec], compact, full, top_k=6,
                         foundational=set(keys))
    assert len(sec.candidates) == 6


# ── the rejection escape is closed ───────────────────────────────────────────

def test_reject_prompt_forbids_the_lazy_reason():
    p = summarize._REJECT_PROMPT
    assert "NEVER a valid reason to reject" in p
    assert "foundational" in p


def test_draft_prompt_says_what_a_foundational_line_is_for():
    p = summarize._DRAFT_PROMPT
    assert "(foundational)" in p
    assert "never skip it for having no numbers" in p


# ── the synthesis trace (F4) ─────────────────────────────────────────────────

def test_trace_records_why_not_just_what(tmp_path):
    """disposition.json says a source was unplaced. This has to say it was never shortlisted
    — the distinction the postIneq audit could only recover by reverse-engineering located/."""
    import types

    from rabbithole import ledger
    work = tmp_path / "work"; work.mkdir()
    paths = types.SimpleNamespace(work=work, output=tmp_path)

    trace = {
        "sources": {
            "seen": {"foundational": False, "best_similarity": 0.9,
                     "shortlisted_in": [0], "offered_to": [], "refusals": [],
                     "outcome": "cited"},
            "never": {"foundational": True, "best_similarity": 0.1,
                      "shortlisted_in": [], "offered_to": [0],
                      "refusals": [{"section": 0, "heading": "H", "reason": "no data"}],
                      "outcome": "unplaced"},
        },
        "summary": {"never_shortlisted": ["never"]},
    }
    out = ledger.write_synthesis_trace(paths, trace)
    d = json.loads(out.read_text())
    assert d["sources"]["never"]["shortlisted_in"] == []
    assert d["sources"]["never"]["refusals"][0]["reason"] == "no data"
    assert d["summary"]["never_shortlisted"] == ["never"]


def test_synthesize_accepts_a_trace_without_breaking_its_contract():
    """`trace` is opt-in: the return signature is unchanged, so existing callers are safe."""
    import inspect
    sig = inspect.signature(summarize.synthesize)
    assert sig.parameters["trace"].default is None
    src = inspect.getsource(summarize.synthesize)
    assert "return narrative, rejected" in src


def test_report_writes_the_trace():
    import inspect
    src = inspect.getsource(summarize.run)
    assert "write_synthesis_trace" in src
    assert "never_shortlisted" in src


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))

"""Phase one: the paper's sections and subsections, and nothing else.

The structure used to be written in the same pass as the content beats, which made it the
one thing nobody could check until a draft had been written from it — a 19-subsection
outline against a 5,000-word CFP produced a 6,975-word manuscript, and the signal arrived
4.5 GPU-hours later. Headings alone are enough to compute the whole word plan.
"""

from __future__ import annotations

import types

from raconteur import guards, skeleton
from raconteur.config import ProjectConfig, VenueConfig


def _cfg(**venue_kw) -> ProjectConfig:
    c = ProjectConfig(short_title="Chords", title="A Title")
    if venue_kw:
        c.venues = {"css2026": VenueConfig(name="CSS2026", **venue_kw)}
    return c


# ── the spine ────────────────────────────────────────────────────────────────

def test_the_default_structure_is_ibmrdc():
    assert skeleton.spine_for(_cfg()) == (
        "Introduction", "Background", "Methods", "Results", "Discussion", "Conclusion")


def test_a_venue_may_state_its_own_structure():
    """Nature's format is not IBMRDC. A venue that mandates an order is stating a spec."""
    c = _cfg(section_structure="Introduction, Results, Discussion, Methods")
    assert skeleton.spine_for(c, "css2026") == (
        "Introduction", "Results", "Discussion", "Methods")


def test_a_venue_that_states_nothing_keeps_the_default():
    assert skeleton.spine_for(_cfg(word_limit=5000), "css2026") == skeleton.IBMRDC


# ── headings only, and unnumbered ────────────────────────────────────────────

def test_numbers_the_model_wrote_are_stripped():
    """The .docx style numbers the headings, so a literal "2.1" renders as "2.1 2.1". A
    model told not to number will number anyway often enough to matter."""
    got = skeleton.parse_headings(
        "## 1. Introduction\n## 2 Background\n### 2.1 The Lattice\n", skeleton.IBMRDC)
    assert got == [(2, "Introduction"), (2, "Background"), (3, "The Lattice")]


def test_bullets_and_prose_are_not_headings():
    got = skeleton.parse_headings(
        "## Methods\n- a beat the model slipped in\nSome prose too.\n### The Model\n",
        skeleton.IBMRDC)
    assert got == [(2, "Methods"), (3, "The Model")]


def test_the_furniture_is_never_taken_from_the_model():
    """Abstract, Acknowledgements and References are fixed. A model that can add them can
    also forget them, rename them, or number them."""
    got = skeleton.parse_headings(
        "## Abstract\n## Introduction\n## Acknowledgements\n## References\n",
        skeleton.IBMRDC)
    assert got == [(2, "Introduction")]


def test_the_document_carries_the_furniture_anyway():
    doc = skeleton.assemble([(2, "Introduction"), (3, "Motivation")], "A Title")
    assert doc.startswith("# A Title\n\n## Abstract\n")
    assert "## Acknowledgements" in doc and "## References" in doc
    assert "## Introduction" in doc and "### Motivation" in doc
    # nothing numbered — the style does that
    assert "## 1." not in doc and "### 1.1" not in doc


# ── what phase one can already prove ─────────────────────────────────────────

def test_a_missing_section_is_caught():
    f = skeleton.findings([(2, "Introduction")], skeleton.IBMRDC, 0)
    assert {x.where for x in f if x.kind == "missing-section"} == {
        "Background", "Methods", "Results", "Discussion", "Conclusion"}


def test_a_section_nobody_asked_for_is_caught():
    sections = [(2, s) for s in skeleton.IBMRDC] + [(2, "Future Work")]
    f = skeleton.findings(sections, skeleton.IBMRDC, 0)
    assert [x.where for x in f if x.kind == "invented-section"] == ["Future Work"]


def test_too_many_subsections_for_the_share_is_caught_before_a_bullet_exists():
    """The whole argument for phase one. Background carries 600 words of a 4,000-word body;
    five subsections is 120 each, and a paragraph needs 100."""
    sections = [(2, "Background")] + [(3, f"Sub {i}") for i in range(7)]
    f = skeleton.findings(sections, ("Background",), 4000)
    assert [x.kind for x in f] == ["subsections-too-thin"]
    assert "600 words across 7 subsections" in f[0].imperative


def test_a_structure_that_fits_is_silent():
    sections = [(2, "Background"), (3, "A"), (3, "B"), (3, "C")]
    assert skeleton.findings(sections, ("Background",), 4000) == []


def test_a_section_with_no_subsections_is_legal():
    """An Introduction of ~2 paragraphs has nothing to subdivide, and the old pooled share
    punished exactly that."""
    assert skeleton.findings([(2, "Introduction")], ("Introduction",), 4000) == []


def test_no_venue_budget_means_no_width_claim():
    sections = [(2, "Background")] + [(3, f"Sub {i}") for i in range(9)]
    assert [f.kind for f in skeleton.findings(sections, ("Background",), 0)] == []


# ── the plan the author is really approving ──────────────────────────────────

def test_the_word_plan_is_computable_from_headings_alone():
    sections = [(2, "Results"), (3, "A"), (3, "B")]
    table = skeleton.plan_table(sections, 4000)
    assert "1000 words" in table and " 2 sub" in table and "500 each" in table
    assert "3 bullets" in table          # 500 words at ~150 a paragraph


def test_one_bullet_is_one_paragraph():
    assert guards.bullets_for(300) == 2
    assert guards.WORDS_PER_PARAGRAPH == 150


# ── phase two is handed the plan, not asked to derive it ─────────────────────

def test_the_outline_is_given_each_subsections_words_and_bullets(tmp_path):
    """Phase two writes onto a structure the author has already gated, so every allocation
    is computable. Leaving the model to derive them across seventeen subsections is how a
    bullet count comes out wrong and bullet_budget then fails it."""
    from raconteur.outline import _per_subsection_plan
    sk = ("# T\n## Abstract\n## Introduction\n## Background\n### A\n### B\n"
          "## Results\n### C\n## Acknowledgements\n## References\n")
    got = _per_subsection_plan(sk, 4000, None)
    assert "Introduction: 300 words, 2 bullet(s)" in got
    assert "Background / A: 300 words, 2 bullet(s)" in got
    assert "Results / C: 1000 words, 7 bullet(s)" in got
    # the furniture carries no allocation
    assert "Abstract" not in got and "References" not in got


def test_phase_two_is_not_told_to_merge_what_conformance_forbids(tmp_path):
    """The merge advice belongs to the skeleton stage, where the structure is still up for
    revision. At the outline stage it instructed the model to do the one thing
    skeleton_conformance rejects."""
    from raconteur.outline import _per_subsection_plan
    got = _per_subsection_plan("# T\n## Results\n### A\n", 4000, None)
    assert "APPROVED and FIXED" in got
    assert "do not add, remove, merge or rename" in got
    assert "merging related material" not in got

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
    """Columns read left to right and multiply out: 4 bullets x 250 = the 1000 words."""
    sections = [(2, "Results"), (3, "A"), (3, "B")]
    table = skeleton.plan_table(sections, 4000)
    assert "1000 words" in table and " 2 sub" in table
    assert "4 bullets" in table          # MIN_BULLETS_PER_SUBSECTION x 2 subsections
    assert "250 each" in table           # words per PARAGRAPH, not per subsection


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
    # Two bullets at the section's rate — not seven derived from the share. A single
    # subsection carrying a 1000-word share is a structure skeleton.findings rejects
    # (two 500-word paragraphs, far over PARAGRAPH_BAND); the outline reproduces what the
    # skeleton would have pinned rather than quietly planning something else.
    assert "Results / C: 1000 words, 2 bullet(s)" in got
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


# ── a redlined skeleton is answered, not dead-ended ──────────────────────────

def test_the_revise_prompt_treats_annotations_as_instructions():
    """The author owns the structure. A comment asking for a merge is an instruction."""
    p = skeleton._REVISE_PROMPT
    assert "instruction, not a suggestion" in p
    assert "Keep every heading the annotations do not ask you to change" in p
    # still headings only, still unnumbered, still no furniture from the model
    assert "Do NOT write bullets" in p and "Do NOT number any heading" in p
    assert "Do NOT add Abstract, Acknowledgements or References" in p


def test_the_revise_path_exists_and_is_reached(tmp_path):
    import inspect
    src = inspect.getsource(skeleton.run)
    assert "_revise(" in src
    assert "not yet implemented" not in src


def test_a_revision_keeps_the_source_datestamp_and_extends_the_chain():
    """A minor version: the datestamp belongs to the revision cycle, and the chain records
    whose turn it was."""
    from raconteur.naming import minor_name
    assert minor_name("Chords", ["css2026", "skeleton", "ra", "DCR"], "md",
                      "260720") == "260720_Chords_css2026_skeleton_ra_DCR_ra.md"


def test_a_revision_with_no_annotations_changes_nothing(tmp_path, monkeypatch):
    """Exit 3, not a silently regenerated skeleton: nothing was asked for."""
    import types
    import pytest
    monkeypatch.setattr(skeleton, "log", lambda *a: None)
    from raconteur import revise as _revise_mod
    monkeypatch.setattr(_revise_mod, "build_revision_context", lambda p: "")
    with pytest.raises(SystemExit) as e:
        skeleton._revise(tmp_path, _cfg(), types.SimpleNamespace(), tmp_path,
                         tmp_path / "x.docx")
    assert e.value.code == 3


# ── the contract: two bullets a subsection ───────────────────────────────────
# Set by the author, 2026-07-21. A subsection is a heading plus an argument, and an
# argument needs more than one paragraph. Everything else follows: a subsection costs
# MIN_BULLETS_PER_SUBSECTION x WORDS_PER_PARAGRAPH, so how many a section affords is
# derived from its word share rather than asserted by a per-leaf constant.

def _plan(pairs):
    out = []
    for name, n in pairs:
        out.append((2, name))
        out += [(3, f"{name} {i}") for i in range(n)]
    return out


APPROVED = [("Introduction", 0), ("Background", 2), ("Methods", 3),
            ("Results", 3), ("Discussion", 3), ("Conclusion", 0)]


def test_the_approved_word_plan_is_clean_and_adds_up():
    """The plan the author accepted for css2026, pinned. 26 bullets, 4,000 body words,
    every paragraph inside PARAGRAPH_BAND."""
    sections = _plan(APPROVED)
    assert skeleton.findings(sections, tuple(n for n, _ in APPROVED), 4000) == []
    bullets = sum(guards.MIN_BULLETS_PER_SUBSECTION * max(n, 1) for _, n in APPROVED)
    assert bullets == 26
    words = sum(guards.section_words(n, 4000) for n, _ in APPROVED)
    assert words == 4000
    for name, n in APPROVED:
        each = guards.section_words(name, 4000) // (
            guards.MIN_BULLETS_PER_SUBSECTION * max(n, 1))
        assert guards.PARAGRAPH_BAND[0] <= each <= guards.PARAGRAPH_BAND[1], (name, each)


def test_too_few_subsections_commits_you_to_fat_paragraphs():
    """Two subsections in a 900-word Methods is four 225-word paragraphs — over the 200 a
    paragraph may run, and the exact defect draft 8 shipped. Said at the skeleton, where
    splitting a heading is free, not at the draft where it costs a re-run."""
    sections = _plan([("Methods", 2)])
    f = skeleton.findings(sections, ("Methods",), 4000)
    assert [x.kind for x in f] == ["subsections-too-few"]
    assert "225 words" in f[0].imperative and "3 subsection(s)" in f[0].imperative


def test_too_many_subsections_starves_the_paragraphs():
    """The 260721 skeleton as generated: Background's four subsections are 75-word
    paragraphs."""
    sections = _plan([("Background", 4)])
    f = skeleton.findings(sections, ("Background",), 4000)
    assert [x.kind for x in f] == ["subsections-too-thin"]
    assert "75 words" in f[0].imperative and "2 subsection(s)" in f[0].imperative


def test_the_allowance_is_derived_from_the_paragraph_constant():
    """No static per-leaf number. Move WORDS_PER_PARAGRAPH and the whole plan moves with
    it — the 280 that used to sit here agreed with nothing else in the pipeline."""
    assert guards.subsection_words() == (guards.MIN_BULLETS_PER_SUBSECTION
                                         * guards.WORDS_PER_PARAGRAPH)
    allow = guards.leaf_allowance(4000)
    assert (allow["litrev"], allow["methods"], allow["results"], allow["other"]) == (2, 3, 3, 3)


def test_the_plan_table_columns_multiply_out():
    row = [r for r in skeleton.plan_table(_plan(APPROVED), 4000).splitlines()
           if "Methods" in r][0]
    assert "900 words" in row and " 3 sub" in row and "6 bullets" in row and "150 each" in row


# ── the word plan travels on the document ────────────────────────────────────
# A log line is seen once, by whoever was watching the terminal. The .docx is what gets
# opened, marked up and gated, so the plan rides there — one comment per section heading.
# WORDS PER BULLET is the invariant: pinned at generation, carried forward, and the reason
# adding a subsection ADDS words instead of thinning the paragraphs already written.

def test_words_per_bullet_is_pinned_from_the_generated_structure():
    secs = _plan(APPROVED)
    wpb = skeleton.words_per_bullet(secs, 4000)
    assert wpb["Methods"] == 150 and wpb["Results"] == 166
    assert "Abstract" not in wpb and "References" not in wpb   # furniture spends no prose


def test_a_new_subsection_adds_words_rather_than_thinning_the_others():
    """The correction that shaped this design. Recomputing the rate from the share would
    hold Methods at 900 and drop every paragraph to 112 words; pinning it grows the
    section, which is what a structural edit means."""
    wpb = skeleton.words_per_bullet(_plan(APPROVED), 4000)
    before = skeleton.document_words(_plan(APPROVED), wpb)
    wider = [(n, s + 1 if n == "Methods" else s) for n, s in APPROVED]
    after = skeleton.document_words(_plan(wider), wpb)
    assert after - before == guards.MIN_BULLETS_PER_SUBSECTION * wpb["Methods"] == 300
    assert before == 3996 and after == 4296


def test_the_plan_comment_states_the_rate_and_what_an_edit_costs():
    notes = dict(skeleton.plan_notes(_plan(APPROVED), 4000))
    assert "150 each" in notes["Methods"] and "3 sub" in notes["Methods"]
    assert "grows by 300 words" in notes["Methods"]
    assert "Abstract" not in notes


def test_reading_a_plan_back_is_forgiving_of_your_own_notes():
    """You edit these by hand and will annotate the reasoning beside the number."""
    assert skeleton.read_plan("Methods — 900 words · 3 sub · 6 bullets · 150 each") == (150, 3)
    assert skeleton.read_plan("3 sub, 150 each (leaving room for the ablation)") == (150, 3)
    assert skeleton.read_plan("no numbers here") == (None, None)

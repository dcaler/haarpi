"""Guards must reproduce, by machine, the defects a careful human read finds.

Every test here is a pure function over text. If a guard needs a docx, an LLM, or the
filesystem to be tested, it does not belong in guards.py.
"""
from __future__ import annotations

import pytest

from raconteur import guards as g


# ── primitives ────────────────────────────────────────────────────────────────

def test_all_citekeys_splits_grouped_citations():
    # The naive [@([^\]]+)] capture treats "[@a; @b]" as one key and loses b.
    assert g.all_citekeys("x [@smith2020] y [@a; @b; @c] z") == [
        "smith2020", "a", "b", "c"
    ]


def test_all_citekeys_empty():
    assert g.all_citekeys("no citations here") == []


@pytest.mark.parametrize("text", [
    "One. Two! Three?",
    "A single sentence with no terminator",
    "Dr. Smith went home. Then he slept.",
    "",
])
def test_sentence_units_are_lossless(text):
    # Losslessness is the point: it lets an unchanged sentence survive byte-for-byte.
    assert "".join(g.sentence_units(text)) == text


def test_sentence_units_count():
    assert len(g.sentence_units("One. Two. Three.")) == 3


# ── section kinds ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("heading,kind", [
    ("Introduction", "litrev"),
    ("2. Related Work", "litrev"),
    ("Background", "litrev"),
    ("Methods", "methods"),
    ("3. Model and Framework", "methods"),
    ("Results", "results"),
    ("Experimental Findings", "results"),
    ("References", "references"),
    ("5. References", "references"),
    ("Abstract", "abstract"),
    ("Discussion", "other"),
    ("Conclusion", "other"),
])
def test_section_kind(heading, kind):
    assert g.section_kind(heading) == kind


def test_results_wins_over_methods_when_both_match():
    # "experimental design" hits RESULTS_KW (experiment) and CODE_KW (design).
    assert g.section_kind("Experimental Design") == "results"
    assert g.section_kind("Model Evaluation") == "results"


def test_expects_citations_excludes_methods_and_results():
    assert g.expects_citations("litrev")
    assert g.expects_citations("other")
    assert not g.expects_citations("methods")
    assert not g.expects_citations("results")
    assert not g.expects_citations("references")
    assert not g.expects_citations("abstract")


# Both of these were found by running the battery against a real rabbitHole document,
# not by reading the code. They are the reason the doc says to use a real fixture.

def test_front_matter_is_not_flagged_uncited():
    # A title block before the first "## " heading carries section -1. It is not body prose.
    md = "# A Paper\n\n*Project:* Solar *Date:* 2026-05-25\n\n## Background\n\nX [@a].\n"
    front = [p for p in g.parse_paragraphs(md) if p.section < 0]
    assert front, "front matter should still be parsed"
    assert g.uncited_paragraphs(g.parse_paragraphs(md)) == []


def test_abstract_is_not_flagged_uncited():
    md = "## Abstract\n\nWe study trust in AI and report a large effect.\n"
    assert g.uncited_paragraphs(g.parse_paragraphs(md)) == []
    assert g.sparse_paragraphs(g.parse_paragraphs(md)) == []


# ── paragraph parsing ─────────────────────────────────────────────────────────

DOC = """\
# Title

## Introduction

Trust matters [@smith2020].

Explainability is contested [@jones2019; @lee2021].

## Methods

We fit a logistic model with a learning rate of 0.01.

## Results

Accuracy rose to 0.94.

## References

Smith, J. (2020). Trust.
"""


def test_parse_paragraphs_tags_section_and_heading():
    paras = g.parse_paragraphs(DOC)
    assert [p.heading for p in paras] == [
        "Introduction", "Introduction", "Methods", "Results"
    ]
    assert [p.section for p in paras] == [0, 0, 1, 2]
    assert [p.index for p in paras] == [0, 1, 0, 0]


def test_parse_paragraphs_excludes_references():
    # A bibliography entry is not prose; every guard would misfire on it.
    assert all(p.kind != "references" for p in g.parse_paragraphs(DOC))
    assert "Smith, J. (2020)" not in "".join(p.text for p in g.parse_paragraphs(DOC))


def test_parse_paragraphs_extracts_grouped_keys():
    paras = g.parse_paragraphs(DOC)
    assert paras[1].distinct == {"jones2019", "lee2021"}


# ── verifiability: draft phase ────────────────────────────────────────────────

def test_unresolved_keys_flags_dangling_citation():
    f = g.unresolved_keys("Claim [@ghost2020] and [@real2019].", known={"real2019"})
    assert len(f) == 1 and f[0].kind == "unresolved-key"
    assert "[@ghost2020]" in f[0].imperative
    assert "[@real2019]" not in f[0].imperative


def test_unresolved_keys_silent_when_all_resolve():
    assert g.unresolved_keys("[@a] [@b]", known={"a", "b"}) == []


def test_author_year_prose_flags_narrative_citation():
    f = g.author_year_prose("Smith et al. (2020) found an effect.")
    assert len(f) == 1 and f[0].kind == "author-year"


def test_author_year_prose_ignores_plain_years():
    assert g.author_year_prose("The 2020 election was held. See [@smith2020].") == []


def test_uncited_paragraphs_flags_background_prose():
    paras = g.parse_paragraphs("## Background\n\nTrust matters a great deal.\n")
    f = g.uncited_paragraphs(paras)
    assert len(f) == 1 and f[0].kind == "uncited"


def test_uncited_paragraphs_gated_off_for_methods_and_results():
    # THE load-bearing gate. Methods/Results are grounded in the writeup, not the bib.
    md = "## Methods\n\nWe fit a logistic model.\n\n## Results\n\nAccuracy was 0.94.\n"
    assert g.uncited_paragraphs(g.parse_paragraphs(md)) == []


def test_sparse_paragraphs_flags_thin_argument():
    # 6 sentences on 1 source → wants ceil(6/3)=2 distinct sources.
    body = "A [@x]. B. C. D. E. F."
    f = g.sparse_paragraphs(g.parse_paragraphs(f"## Background\n\n{body}\n"))
    assert len(f) == 1 and f[0].kind == "sparse-paragraph"


def test_sparse_paragraphs_satisfied_by_enough_sources():
    body = "A [@x]. B [@y]. C. D. E. F."
    assert g.sparse_paragraphs(g.parse_paragraphs(f"## Background\n\n{body}\n")) == []


def test_sparse_paragraphs_gated_off_for_methods():
    body = "A [@x]. B. C. D. E. F."
    assert g.sparse_paragraphs(g.parse_paragraphs(f"## Methods\n\n{body}\n")) == []


def test_unnumbered_results_paragraph_flagged():
    md = "## Results\n\nPerformance improves substantially across conditions.\n"
    f = g.unnumbered_results_paragraphs(g.parse_paragraphs(md))
    assert len(f) == 1 and f[0].kind == "unnumbered-result"


def test_numbered_results_paragraph_passes():
    md = "## Results\n\nAccuracy rose to 0.94 (p < 0.01).\n"
    assert g.unnumbered_results_paragraphs(g.parse_paragraphs(md)) == []


def test_unnumbered_guard_ignores_non_results_sections():
    md = "## Discussion\n\nThis matters a great deal for practice.\n"
    assert g.unnumbered_results_paragraphs(g.parse_paragraphs(md)) == []


# ── verifiability: redline phase ──────────────────────────────────────────────

def test_dropped_citekeys():
    f = g.dropped_citekeys("A [@x] and [@y].", "A [@x].")
    assert len(f) == 1 and "[@y]" in f[0].imperative


def test_dropped_citekeys_silent_when_preserved():
    assert g.dropped_citekeys("A [@x].", "A revised [@x].") == []


def test_dropped_sentinels():
    f = g.dropped_sentinels("The rule ⟦m:1⟧ holds.", "The rule holds.")
    assert len(f) == 1 and "⟦m:1⟧" in f[0].imperative


def test_invented_sentinels():
    f = g.invented_sentinels("The rule holds.", "The rule ⟦m:9⟧ holds.")
    assert len(f) == 1 and "⟦m:9⟧" in f[0].imperative


def test_sentinels_roundtrip_clean():
    assert g.dropped_sentinels("a ⟦m:1⟧ b", "b ⟦m:1⟧ a") == []
    assert g.invented_sentinels("a ⟦m:1⟧ b", "b ⟦m:1⟧ a") == []


# ── minimality: redline phase ─────────────────────────────────────────────────

def test_touched_indices_finds_the_changed_sentence():
    old = "One. Two. Three."
    new = "One. Two revised. Three."
    assert g.touched_indices(old, new) == {1}


def test_touched_indices_empty_when_identical():
    assert g.touched_indices("One. Two.", "One. Two.") == set()


def test_minimal_edit_violation_flags_collateral_change():
    # Comment anchored to sentence 2 (index 1); reviser also rewrote index 3.
    f = g.minimal_edit_violation(touched={1, 3}, anchored={1}, n_sentences=5)
    assert len(f) == 1 and f[0].kind == "minimal-edit"
    assert "sentence(s) 4" in f[0].imperative      # 1-based in the message


def test_minimal_edit_violation_silent_within_scope():
    assert g.minimal_edit_violation(touched={1}, anchored={1, 2}, n_sentences=5) == []


def test_minimal_edit_violation_inactive_on_whole_paragraph_selection():
    # Reviewer selected everything: nothing to over-reach into.
    assert g.minimal_edit_violation(touched={0, 1}, anchored={0, 1}, n_sentences=2) == []


def test_minimal_edit_violation_inactive_when_nothing_anchored():
    assert g.minimal_edit_violation(touched={0}, anchored=set(), n_sentences=3) == []


# ── grouping + metrics ────────────────────────────────────────────────────────

def test_by_section_drops_manuscript_wide_findings():
    findings = [
        g.Finding("a", "w", "i", section=0),
        g.Finding("b", "w", "i", section=1),
        g.Finding("c", "w", "i", section=None),
    ]
    assert set(g.by_section(findings)) == {0, 1}


def test_metrics_line():
    m = g.metrics(DOC, known={"smith2020", "jones2019"})
    # lee2021 is cited but not in refs.bib
    assert m.citekeys_total == 3
    assert m.citekeys_resolved == 2
    assert m.sections == 3          # Introduction, Methods, Results (References excluded)
    assert "citekeys resolved 2/3" in str(m)

"""The draft stage's whole-document checks: conformance, length, and a band that fits.

Three defects this pins, all observed in one 4.5-hour css2026 run:

  * the draft invented "### 1.2 Tonal Stability Hierarchies" to fill a numbering gap in the
    outline, and nothing compared the manuscript to the contract it was written from;
  * 19 subsections each legal at 150–300 words summed to 6,975 against a 5,000-word cap,
    and the tally line reported it clean because no guard measured length;
  * the band was a hardcoded constant, so a venue's word limit reached the writer as
    ambient fact and never as the number it was actually asked to hit.
"""

from __future__ import annotations

from raconteur import guards, paper


# ── conformance to the approved outline ───────────────────────────────────────

def test_a_section_the_outline_never_named_is_caught():
    outline = "### 1.1 First\n- a beat\n### 1.2 Second\n- a beat\n"
    draft = "### 1.1 First\n\nprose\n\n### 1.2 Second\n\nprose\n\n### 1.3 Invented\n\nprose\n"
    findings = guards.outline_conformance(draft, outline)
    assert [f.kind for f in findings] == ["invented-section"]
    assert "1.3 Invented" in findings[0].where


def test_a_section_the_draft_dropped_is_caught():
    outline = "### 1.1 First\n- a beat\n### 1.2 Second\n- a beat\n"
    draft = "### 1.1 First\n\nprose\n"
    assert [f.kind for f in guards.outline_conformance(draft, outline)] == ["dropped-section"]


def test_numbering_and_punctuation_do_not_count_as_a_mismatch():
    """"3.1. Recovery Landscape" and "3.1 Recovery Landscape" are the same section; a
    conformance guard that cannot see that would fire on every draft."""
    outline = "### 3.1. Recovery Landscape: The Settling Band\n- a beat\n"
    draft = "### 3.1 Recovery Landscape — The Settling Band\n\nprose\n"
    assert guards.outline_conformance(draft, outline) == []


def test_boilerplate_sections_are_not_conformance_failures():
    """References and Acknowledgements are generated, not drafted from outline bullets."""
    outline = "### 1.1 First\n- a beat\n"
    draft = "### 1.1 First\n\nprose\n\n## References\n\n## Acknowledgements\n"
    assert guards.outline_conformance(draft, outline) == []


def test_no_outline_means_no_conformance_claim():
    assert guards.outline_conformance("### 1.1 A\n\nprose\n", "") == []


# ── whole-document length ─────────────────────────────────────────────────────

def test_the_sum_of_legal_sections_can_still_overrun():
    """The defect exactly: every section inside its band, the manuscript 40% over."""
    doc = "\n\n".join(f"### {i}\n\n" + "word " * 290 for i in range(19))
    assert [f.kind for f in guards.over_budget(doc, 3889)] == ["over-budget"]


def test_a_manuscript_within_budget_is_silent():
    assert guards.over_budget("word " * 3000, 3889) == []


def test_no_venue_limit_means_no_length_claim():
    assert guards.over_budget("word " * 99999, 0) == []


def test_headings_figures_and_citekeys_are_not_prose():
    """A caption is charged to the figure reserve and a [@key] renders as "(Author 2020)";
    counting either as words the author wrote would double-charge the budget."""
    doc = "## Heading\n\n![Figure 1: a caption with several words](results/figures/a.png)\n\nfour real words here [@smith2020]\n"
    assert guards.word_count(doc) == 4


# ── the band follows the section's share, not its sibling count ───────────────

def test_results_subsections_get_more_words_than_methods_subsections():
    """A uniform band is how Results ends up written in 18% of the paper."""
    results = guards.section_target("3. Results", 3688, leaves_here=3)
    methods = guards.section_target("2. Methodology", 3688, leaves_here=3)
    assert results[0] > methods[0]


def test_a_wide_section_gets_a_thinner_band():
    """Seven Methods subsections divide the same share seven ways — the arithmetic that
    says the structure is too wide, surfaced as the number the writer is handed."""
    narrow = guards.section_target("2. Methodology", 3688, leaves_here=3)
    wide = guards.section_target("2. Methodology", 3688, leaves_here=7)
    assert wide[1] < narrow[0]


def test_no_venue_limit_means_the_writer_chooses():
    assert guards.section_target("3. Results", 0, leaves_here=3) == (0, 0)


def test_the_band_is_keyed_off_the_section_not_the_subsection_name():
    """"Recovery Landscape" carries no results keyword and would classify as "other" —
    the same misclassification that made a uniform allocation look defensible."""
    counts = guards.section_leaf_counts(
        "## 3. Results\n### 3.1 Recovery Landscape\n- a beat\n### 3.2 Fair Fight\n- a beat\n")
    assert counts == {"3. Results": 2}


# ── the prompts actually carry the numbers ────────────────────────────────────

def test_the_prompts_take_a_derived_band_not_a_constant():
    assert "{words_low}" in paper._DRAFT_SECTION_PROMPT
    assert "{words_high}" in paper._DRAFT_SECTION_PROMPT
    # the critique must judge against the same band it was drafted to, not a fixed 100/500
    assert "{words_low}" in paper._CRITIQUE_SECTION_PROMPT
    assert "150–300" not in paper._DRAFT_SECTION_PROMPT


def test_a_venue_with_no_limit_falls_back_to_the_old_band():
    """Most venues in a slate state no length. Writing to an assumed one is its own defect."""
    assert paper._DEFAULT_BAND == (150, 300)


def test_the_condense_pass_may_not_drop_citations_figures_or_sections():
    p = paper._CONDENSE_PROMPT
    assert "[@citekey]" in p and "![" in p
    assert "subsection" in p and "heading" in p

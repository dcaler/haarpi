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


def test_the_rebalance_pass_may_not_drop_citations_figures_or_sections():
    p = paper._REBALANCE_PROMPT
    assert "[@citekey]" in p and "![" in p
    assert "subsection" in p and "heading" in p


def test_the_rebalance_pass_can_grow_a_section_not_only_cut_it():
    """It was a condense pass: it only ever knew how to remove words, so an under-written
    Results section had no repair available to it."""
    p = paper._REBALANCE_PROMPT
    assert "GROW" in p and "SHRINK" in p
    # growing must not become padding — that trades a thin paper for a verbose one
    assert "Never pad" in p


# ── a range is two numbers, and both of them bind ─────────────────────────────

def test_the_target_sits_inside_a_stated_range():
    """css2026 asks for 3,000–5,000. 60% of the way up is 4,200."""
    assert guards.word_target(3000, 5000, fraction=0.6) == 4200


def test_a_venue_with_only_a_maximum_targets_that_maximum():
    """Most CFPs state a cap and nothing else; inventing a floor under one would write a
    shorter paper than the venue asked for."""
    assert guards.word_target(None, 8000) == 8000


def test_a_manuscript_short_of_its_target_is_caught():
    """The defect the ceiling-only budget allowed: comfortably under, and under-written."""
    assert [f.kind for f in guards.under_budget("word " * 2000, 3000)] == ["under-budget"]


def test_a_manuscript_at_its_target_is_silent_in_both_directions():
    doc = "word " * 2950
    assert guards.over_budget(doc, 3000) == [] and guards.under_budget(doc, 3000) == []


def test_no_venue_limit_means_no_shortness_claim():
    assert guards.under_budget("word " * 10, 0) == []


# ── per-section shape ─────────────────────────────────────────────────────────

def test_a_section_written_below_its_share_is_caught():
    """Results at 17% of a paper that budgeted it 30% — legal on every whole-document
    check, and the section carrying the contribution is the thinnest in the paper."""
    outline = "## 4. Results\n### 4.1 A\n- b\n### 4.2 B\n- b\n### 4.3 C\n- b\n"
    draft = "## 4. Results\n\n" + "word " * 300
    assert [f.kind for f in guards.section_lengths(draft, outline, 3688)] == ["section-thin"]


def test_a_section_written_above_its_share_is_caught():
    outline = "## 2. Background\n### 2.1 A\n- b\n### 2.2 B\n- b\n"
    draft = "## 2. Background\n\n" + "word " * 1200
    assert [f.kind for f in guards.section_lengths(draft, outline, 3688)] == ["section-fat"]


def test_boilerplate_sections_have_no_share_to_miss():
    outline = "## 4. Results\n### 4.1 A\n- b\n"
    draft = "## References\n\nlots\n\n## Acknowledgements\n\n" + "word " * 400
    assert guards.section_lengths(draft, outline, 3688) == []


# ── conformance sees whole sections, not only subsections ─────────────────────

def test_a_dropped_section_with_no_subsections_is_caught():
    """"## 6. Conclusion" carries its bullets directly. The draft stopped after 5.4 and
    conformance reported clean, because a childless section was invisible to a check that
    only ever compared level-3 headings."""
    outline = "## 5. Discussion\n### 5.1 A\n- b\n## 6. Conclusion\n- a beat\n"
    draft = "## 5. Discussion\n\n### 5.1 A\n\nprose\n"
    findings = guards.outline_conformance(draft, outline)
    assert [f.kind for f in findings] == ["dropped-section"]
    assert "6. Conclusion" in findings[0].where


def test_a_section_with_subsections_is_not_itself_a_conformance_item():
    """Otherwise every parent heading doubles as a requirement and a draft that has all
    the subsections still fails for the container that holds them."""
    outline = "## 5. Discussion\n### 5.1 A\n- b\n"
    draft = "## 5. Discussion\n\n### 5.1 A\n\nprose\n"
    assert guards.outline_conformance(draft, outline) == []


def test_two_sections_of_one_kind_split_that_kinds_share():
    """Introduction and Background are both "litrev". Each claiming the full 14% hands that
    kind 28% of the paper and the shares stop summing to the document — the arithmetic
    behind Introduction and Background taking 36% of a manuscript budgeted for 28%."""
    o = ("## 1. Introduction\n### 1.1 A\n- b\n### 1.2 B\n- b\n"
         "## 2. Background\n### 2.1 C\n- b\n### 2.2 D\n- b\n")
    assert guards.kind_leaf_counts(o) == {"litrev": 4}
    intro = guards.section_target("1. Introduction", 3493, 2, kind_leaves=4)
    bg = guards.section_target("2. Background", 3493, 2, kind_leaves=4)
    # 14% of 3493 is 489; four leaves of one kind split it, not two lots of two
    assert intro == bg
    assert abs((intro[0] + intro[1]) / 2 * 4 - 3493 * 0.14) < 5


def test_the_bands_sum_to_the_budget_not_past_it():
    """Shares total 1.0, so the bands' midpoints must total the budget. They summed to
    3,972 against 3,493 while each kind was charged once per section."""
    o = ("## 1. Introduction\n### 1.1 A\n- b\n## 2. Background\n### 2.1 B\n- b\n"
         "## 3. Methods\n### 3.1 C\n- b\n## 4. Results\n### 4.1 D\n- b\n"
         "## 5. Discussion\n### 5.1 E\n- b\n## 6. Conclusion\n- b\n")
    budget = 3493
    counts, by_kind = guards.section_leaf_counts(o), guards.kind_leaf_counts(o)
    total = 0.0
    for h, n in counts.items():
        k = "conclusion" if guards._is_conclusion(h) else guards.section_kind(h)
        lo, hi = guards.section_target(h, budget, n, kind_leaves=by_kind.get(k))
        total += (lo + hi) / 2 * n
    # abstract's 6% share has no section here, so the rest total 94% of the budget
    assert abs(total - budget * 0.94) < 30

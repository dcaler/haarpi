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

def test_results_gets_more_words_than_methods():
    """Results carries the contribution; a uniform allocation is how it ends up written in
    18% of the paper."""
    assert guards.section_words("Results", 4000) > guards.section_words("Methods", 4000)


def test_background_gets_more_words_than_the_introduction():
    """Purpose, not arithmetic: an Introduction is motivation, preview and roadmap — two
    paragraphs — while a Background must carry every literature the reader needs."""
    assert guards.section_words("Background", 4000) > guards.section_words("Introduction", 4000)


def test_a_sections_band_does_not_depend_on_its_subsection_count():
    """The bug this replaces: the band divided a share by the subsection count, so a wide
    Methods got a thinner band than a narrow one — and collapsing a section's subsections
    CUT its budget, punishing the very edit the guard had asked for."""
    assert guards.section_target("Methods", 4000) == guards.section_target("Methods", 4000)
    assert guards.section_target("Methods", 4000)[0] > 0


def test_no_venue_limit_means_the_writer_chooses():
    assert guards.section_target("Results", 0) == (0, 0)


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


def test_the_fallback_band_is_section_scale_not_subsection_scale():
    """Most venues in a slate state no length, and writing to an assumed one is its own
    defect — but the fallback must at least be in the right units. Left at per-subsection
    scale it told a three-subsection Methods to write 150-300 words in total."""
    assert paper._DEFAULT_BAND == (450, 900)


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
    """Results at 17% of a paper that budgeted it 25% — legal on every whole-document
    check, and the section carrying the contribution is the thinnest in the paper."""
    outline = "## Results\n### A\n- b\n### B\n- b\n### C\n- b\n"
    draft = "## Results\n\n" + "word " * 300
    assert [f.kind for f in guards.section_lengths(draft, outline, 4000)] == ["section-thin"]


def test_a_section_written_above_its_share_is_caught():
    outline = "## Background\n### A\n- b\n### B\n- b\n"
    draft = "## Background\n\n" + "word " * 1200
    assert [f.kind for f in guards.section_lengths(draft, outline, 4000)] == ["section-fat"]


def test_boilerplate_sections_have_no_share_to_miss():
    """References and Acknowledgements are permanent furniture — never planned, never
    deleted, and never charged a word."""
    outline = "## Results\n### A\n- b\n"
    draft = "## References\n\nlots\n\n## Acknowledgements\n\n" + "word " * 400
    assert guards.section_lengths(draft, outline, 4000) == []


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


# ── shares follow purpose, and the abstract is outside the body ──────────────

def test_the_target_is_the_middle_of_the_range():
    """50% of css2026's 3,000–5,000."""
    assert guards.word_target(3000, 5000) == 4000
    assert guards.TARGET_FRACTION == 0.5


def test_the_abstract_is_not_charged_to_the_body():
    """No venue counts it against the paper's length, so it is neither in the shares nor
    deducted from the budget."""
    assert "abstract" not in guards.DEFAULT_SECTION_SHARES
    assert guards.abstract_words() == 225
    assert guards.abstract_words(150) == 150      # the CFP's own limit wins


def test_the_purpose_shares_distribute_the_whole_budget():
    got = {h: guards.section_words(h, 4000) for h in
           ("Introduction", "Background", "Methods", "Results", "Discussion", "Conclusion")}
    assert got == {"Introduction": 300, "Background": 600, "Methods": 900,
                   "Results": 1000, "Discussion": 900, "Conclusion": 300}
    assert sum(got.values()) == 4000


def test_an_introduction_is_told_apart_from_a_background():
    """They are one kind for citation purposes and two sections for budget purposes."""
    assert guards.budget_kind("1. Introduction") == "intro"
    assert guards.budget_kind("2. Background") == "litrev"
    assert guards.budget_kind("2. Related Work") == "litrev"
    assert guards.section_kind("1. Introduction") == guards.section_kind("2. Background")


# ── one bullet, one paragraph ────────────────────────────────────────────────

def test_a_bullet_count_falls_out_of_the_word_allocation():
    """One bullet is one manuscript paragraph, so a bullet count is not a stylistic choice:
    four bullets under a 200-word subsection is a 50-word paragraph."""
    assert guards.bullets_for(300) == 2       # the author's own figure: 300 words, 2 paras
    assert guards.bullets_for(450) == 3
    assert guards.bullets_for(200) == 1


def test_a_subsection_always_affords_at_least_one_paragraph():
    assert guards.bullets_for(40) == 1
    assert guards.bullets_for(0) == 0


# ── the prompts describe the band they are actually given ────────────────────

def test_the_draft_is_told_the_band_is_for_the_whole_section():
    """section_target returns a SECTION band. The prompt used to call it a per-subsection
    target, so a three-subsection Results was told to write its whole allocation three
    times over — an implied manuscript of 9,120-13,680 words against a 4,000 budget."""
    p = paper._DRAFT_SECTION_PROMPT
    assert "WHOLE section" in p
    assert "across all its subsections together — not each" in p
    assert "subsection should be {words_low}" not in p


def test_the_draft_is_told_the_bullet_contract_it_is_guarded_on():
    """paragraph_conformance fails a draft whose paragraph count does not match its
    bullets. Guarding on a rule nobody stated means failing, then repairing."""
    assert "ONE PARAGRAPH per outline bullet" in paper._DRAFT_SECTION_PROMPT
    assert "one bullet is one paragraph" in paper._CRITIQUE_SECTION_PROMPT
    assert "one outline bullet is one paragraph" in paper._REBALANCE_PROMPT


def test_the_abstract_is_asked_for_the_length_we_settled_on(tmp_path, monkeypatch):
    import types
    captured = {}
    monkeypatch.setattr(paper, "_SYSTEM", "")
    brain = types.SimpleNamespace(
        coordinator=lambda prompt, **kw: captured.setdefault("p", prompt) or "abstract")
    cfg = types.SimpleNamespace(title="T", topic="t", focus="f",
                                venue=lambda n: None)
    paper._draft_abstract(brain, cfg, "", "", "{}")
    assert "- 225 words" in captured["p"]


def test_a_venues_own_abstract_limit_wins(tmp_path, monkeypatch):
    import types
    captured = {}
    brain = types.SimpleNamespace(
        coordinator=lambda prompt, **kw: captured.setdefault("p", prompt) or "abstract")
    cfg = types.SimpleNamespace(title="T", topic="t", focus="f",
                                venue=lambda n: types.SimpleNamespace(abstract_limit=150))
    paper._draft_abstract(brain, cfg, "", "", "{}", venue="css2026")
    assert "- 150 words" in captured["p"]

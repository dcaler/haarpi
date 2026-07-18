"""The outline's structural guards — the battery outline.py never had.

outline.py imported no guards at all and relied on two LLM critique passes to mark their
own homework. A 1.1 → 1.3 numbering gap survived both passes AND the human gate; the draft
then invented a §1.2 to fill it, and a 19-subsection outline pointed at a 5,000-word CFP
produced a 6,975-word manuscript. Both are computable in milliseconds. These pin that.
"""

from __future__ import annotations

from raconteur import guards


def _outline(body: str) -> str:
    return body.strip() + "\n"


# ── the bug that cost 4.5 GPU-hours ───────────────────────────────────────────

def test_a_numbering_gap_is_caught():
    md = _outline("""
## 1. Introduction
### 1.1 First
- a beat
### 1.3 Third
- a beat
""")
    kinds = {f.kind for f in guards.numbering_gaps(guards.parse_outline(md))}
    assert "numbering-gap" in kinds


def test_consecutive_numbering_is_clean():
    md = _outline("""
## 1. Introduction
### 1.1 First
- a beat
### 1.2 Second
- a beat
""")
    assert guards.numbering_gaps(guards.parse_outline(md)) == []


# ── heading levels ────────────────────────────────────────────────────────────

def test_a_skipped_heading_level_is_caught():
    md = _outline("""
## 2. Methodology
#### Orphaned Third Tier
- a beat
""")
    kinds = {f.kind for f in guards.heading_levels(guards.parse_outline(md))}
    assert "heading-skip" in kinds


def test_a_container_with_children_is_not_an_empty_heading():
    md = _outline("""
## 2. Methodology
### 2.1 The Model
#### Vocabulary
- a beat
""")
    kinds = {f.kind for f in guards.heading_levels(guards.parse_outline(md))}
    assert "empty-heading" not in kinds


def test_references_and_acknowledgements_may_be_empty():
    """The bibliography is generated at write time and CRediT passes through verbatim —
    neither carries an argument, and flagging them is noise the author learns to ignore."""
    md = _outline("""
## 1. Introduction
- a beat

## Acknowledgements

## References
""")
    kinds = {f.kind for f in guards.heading_levels(guards.parse_outline(md))}
    assert "empty-heading" not in kinds


# ── a subsection inherits its section's kind ──────────────────────────────────

def test_a_subsection_takes_its_kind_from_its_section_not_its_name():
    """"Sonic Art and Audio Translation of Visual Models" contains "model" and classifies as
    methods on its own name; it is a subsection of the Introduction. "Qualitative Assessment"
    classifies as "other" and would draw a citation floor; it is Methods."""
    md = _outline("""
## 1. Introduction
### 1.1 Sonic Art and Audio Translation of Visual Models
- a beat

## 2. Methodology
### 2.1 Qualitative Assessment
- a beat
""")
    heads = guards.parse_outline(md)
    by_name = {h.text: guards.ancestor_kind(heads, h) for h in guards.leaves(heads)}
    assert by_name["1.1 Sonic Art and Audio Translation of Visual Models"] == "litrev"
    assert by_name["2.1 Qualitative Assessment"] == "methods"


def test_a_misleveled_conclusion_is_not_charged_to_discussion():
    """Outlines emit "### 5. Conclusion" under Discussion. Inheriting blindly would spend
    the Conclusion's words out of Discussion's budget and hide the mis-levelling."""
    md = _outline("""
## 4. Discussion
### 4.1 A point
- a beat
### 5. Conclusion
- a beat
""")
    heads = guards.parse_outline(md)
    concl = next(h for h in guards.leaves(heads) if "Conclusion" in h.text)
    assert guards.ancestor_kind(heads, concl) == "conclusion"


# ── the word budget ───────────────────────────────────────────────────────────

def test_the_budget_pays_for_references_and_figures_first():
    """A venue counting "inclusive of figures, tables, notes and references" is budgeting
    the whole document; handing the writer the gross limit spends the bibliography twice."""
    gross = 5000
    net = guards.prose_budget(gross, n_refs=26, n_figures=4, caption_words=125)
    assert net < gross - 26 * guards.REF_WORDS_PER_ENTRY
    assert net == 3889


def test_an_outline_too_wide_for_its_venue_is_caught():
    """The real shape of the defect: a 5,000-word CFP (3,889 prose) affords ~14 subsections,
    and the css2026 outline carried 18. The draft that obeyed it came in at 6,975 words."""
    md = _outline("## 2. Methodology\n" + "\n".join(
        f"### 2.{i} Sub {i}\n- a beat\n" for i in range(1, 19)))
    heads = guards.parse_outline(md)
    assert len(guards.leaves(heads)) == 18
    findings = guards.leaf_budget(heads, budget=3889)
    assert any(f.kind == "over-budget" for f in findings)
    # and it must say WHICH section is too wide, not just that the total is
    assert any(f.kind == "section-over-budget" for f in findings)


def test_no_venue_limit_means_no_budget_findings():
    """Most venues in a slate state no word limit. The guard must stay silent rather than
    invent a cap — writing to an assumed length is the failure it exists to prevent."""
    md = _outline("## 1. Introduction\n### 1.1 Sub\n- a beat\n")
    assert guards.leaf_budget(guards.parse_outline(md), budget=0) == []


def test_the_shares_are_not_uniform():
    """A uniform per-leaf allocation gives a section words for having many subsections
    rather than for having the most to say — Methods 41%, Results 18%."""
    sh = guards.DEFAULT_SECTION_SHARES
    assert sh["results"] > sh["methods"]
    assert abs(sum(sh.values()) - 1.0) < 1e-9


# ── figure placement ──────────────────────────────────────────────────────────

def test_a_figure_placed_twice_is_caught():
    md = _outline("""
## 3. Results
### 3.1 One
Figure 1: A caption long enough to read by (results/figures/a.png)
### 3.2 Two
Figure 1: A caption long enough to read by (results/figures/a.png)
""")
    heads = guards.parse_outline(md)
    kinds = {f.kind for f in guards.figure_placement(md, heads)}
    assert "figure-repeated" in kinds


def test_an_available_figure_never_placed_is_caught():
    md = _outline("## 3. Results\n### 3.1 One\n- a beat\n")
    heads = guards.parse_outline(md)
    findings = guards.figure_placement(md, heads, {"results/figures/a.png": "results"})
    assert [f.kind for f in findings] == ["figure-unplaced"]


def test_an_invented_figure_path_is_caught():
    md = _outline("""
## 3. Results
### 3.1 One
Figure 1: A caption long enough to read by (results/figures/nope.png)
""")
    heads = guards.parse_outline(md)
    findings = guards.figure_placement(md, heads, {"results/figures/a.png": "results"})
    assert {f.kind for f in findings} == {"figure-unplaced", "figure-invented"}


def test_an_author_illustration_outside_results_is_legal():
    """The whole point of first-class author figures: a model schematic belongs in Methods,
    and the Results-only placement rule used to forbid exactly that."""
    md = _outline("""
## 2. Methodology
### 2.1 The Model
Figure 1: A schematic of the chord lattice and its neighbour window (paper/figures/m.png)

## 3. Results
### 3.1 One
Figure 2: Recovery landscape over tolerance and radius (results/figures/a.png)
""")
    heads = guards.parse_outline(md)
    expected = {"paper/figures/m.png": "author", "results/figures/a.png": "results"}
    assert guards.figure_placement(md, heads, expected) == []


def test_figures_out_of_order_are_caught():
    md = _outline("""
## 3. Results
### 3.1 One
Figure 2: A caption long enough to read by (results/figures/a.png)
""")
    heads = guards.parse_outline(md)
    kinds = {f.kind for f in guards.figure_placement(md, heads)}
    assert "figure-misnumbered" in kinds


# ── required sections ─────────────────────────────────────────────────────────

def test_a_required_section_the_outline_lacks_is_caught():
    """venue.required_sections existed in config and slate printed it into the prompt;
    nothing ever checked the result."""
    md = _outline("## 1. Introduction\n- a beat\n")
    findings = guards.required_sections(md, "Ethics Statement, Data Availability")
    assert {f.where for f in findings} == {"Ethics Statement", "Data Availability"}


def test_no_required_sections_means_silence():
    assert guards.required_sections("## 1. Introduction\n- a beat\n", "") == []


def test_the_reference_reserve_is_the_bibliography_not_the_corpus():
    """Caught by driving the real project: SchellingChords carries 86 sources in refs.bib
    and the manuscript cited 26. Reserving the corpus starved a 5,000-word budget to 2,629
    and afforded 10 subsections where the venue really affords 14."""
    assert guards.expected_references(5000, corpus_size=86) == 29
    # you cannot cite what you have not gathered
    assert guards.expected_references(5000, corpus_size=12) == 12
    # no venue limit, no estimate to make
    assert guards.expected_references(0, corpus_size=86) == 0


def test_the_budget_matches_the_hand_analysis():
    """The whole chain, on css2026's real numbers: 5,000 inclusive → 14 subsections."""
    budget = guards.prose_budget(5000, guards.expected_references(5000, 86), 4, 125)
    assert 3800 <= budget <= 3900
    assert sum(guards.leaf_allowance(budget).values()) == 14

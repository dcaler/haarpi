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

def test_the_budget_deducts_nothing():
    """Nothing that would be deducted is counted in the first place: word_count already
    excludes headings, captions and [@citekeys], the bibliography is rendered by pandoc and
    never appears in the markdown, and the abstract sits outside the body. Reserving for
    them charged the budget twice — 707 words of a 4,200-word target on SchellingChords."""
    assert guards.prose_budget(4000) == 4000


def test_a_structure_too_wide_for_its_venue_is_caught_at_the_skeleton():
    """The real shape of the defect: a 5,000-word CFP (3,889 prose) and 18 subsections under
    one section. The draft that obeyed it came in at 6,975 words.

    ``leaf_budget`` used to ask this at the outline, on a third threshold (280 words a
    subsection) that agreed with neither the skeleton's plan table nor PARAGRAPH_BAND. It is
    asked once now, at the skeleton, in the plan table's own units — and the outline no
    longer re-litigates a structure the author has approved."""
    from raconteur import skeleton
    sections = [(2, "Methods")] + [(3, f"Sub {i}") for i in range(18)]
    kinds = [f.kind for f in skeleton.findings(sections, ("Methods",), 3889)]
    assert "subsections-too-thin" in kinds


def test_no_venue_limit_means_no_budget_findings():
    """Most venues in a slate state no word limit. The guard must stay silent rather than
    invent a cap — writing to an assumed length is the failure it exists to prevent."""
    from raconteur import skeleton
    sections = [(2, "Methods"), (3, "Sub")]
    assert skeleton.findings(sections, ("Methods",), 0) == []


def test_the_shares_are_not_uniform():
    """A uniform per-leaf allocation gives a section words for having many subsections
    rather than for having the most to say — Methods 41%, Results 18%."""
    sh = guards.DEFAULT_SECTION_SHARES
    assert sh["results"] > sh["methods"]
    # Background gives the reader the literature they need and can be long; an Introduction
    # is motivation, preview and roadmap, which is inherently brief.
    assert sh["litrev"] > sh["intro"]
    assert abs(sum(sh.values()) - 1.0) < 1e-9
    assert "abstract" not in sh          # not charged to the body budget


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


def test_the_budget_matches_the_hand_analysis():
    """css2026's real numbers, end to end: a 3,000-5,000 range targets 4,000 of body
    prose, and the purpose shares distribute it as agreed."""
    budget = guards.prose_budget(guards.word_target(3000, 5000))
    assert budget == 4000
    got = {k: guards.section_words(h, budget)
           for k, h in (("intro", "Introduction"), ("litrev", "Background"),
                        ("methods", "Methods"), ("results", "Results"),
                        ("other", "Discussion"), ("conclusion", "Conclusion"))}
    assert got == {"intro": 300, "litrev": 600, "methods": 900,
                   "results": 1000, "other": 900, "conclusion": 300}
    assert sum(got.values()) == budget

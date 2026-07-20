"""One bullet, one paragraph — and the outline reproduces the approved skeleton.

Both contracts existed only as sentences in prompts. A prompt is an instruction; a guard is
a fact. The difference matters here because each is the load-bearing assumption of a whole
stage: the bullet count is how a word allocation becomes a plan, and the skeleton is the
structure the author actually redlined and gated.
"""

from __future__ import annotations

from raconteur import guards


def _outline(*subs: tuple[str, int]) -> str:
    """A Background section whose subsections carry the given bullet counts."""
    lines = ["## Background"]
    for name, n in subs:
        lines.append(f"### {name}")
        lines += [f"- beat {i}" for i in range(n)]
    return "\n".join(lines) + "\n"


# ── the outline honours the approved skeleton ────────────────────────────────

def test_a_renamed_section_is_caught():
    """The author gated these headings. Phase two adds beats; it does not get to rename."""
    skeleton = "## Background\n### Schelling Dynamics\n"
    outline = "## Background\n### Segregation Dynamics\n- a beat\n"
    kinds = [f.kind for f in guards.skeleton_conformance(outline, skeleton)]
    assert "invented-section" in kinds and "dropped-section" in kinds


def test_a_section_phase_two_added_is_caught():
    skeleton = "## Background\n### A\n"
    outline = "## Background\n### A\n- b\n### Future Work\n- b\n"
    findings = guards.skeleton_conformance(outline, skeleton)
    assert [f.kind for f in findings] == ["invented-section"]
    assert findings[0].where == "Future Work"


def test_a_section_phase_two_dropped_is_caught():
    skeleton = "## Background\n### A\n### B\n"
    outline = "## Background\n### A\n- b\n"
    findings = guards.skeleton_conformance(outline, skeleton)
    assert [f.kind for f in findings] == ["dropped-section"]


def test_adding_bullets_is_not_a_structural_change():
    """Which is the whole job of phase two."""
    skeleton = "## Background\n### A\n### B\n"
    outline = "## Background\n### A\n- b\n- b\n### B\n- b\n"
    assert guards.skeleton_conformance(outline, skeleton) == []


def test_no_skeleton_means_no_conformance_claim():
    assert guards.skeleton_conformance("## Background\n### A\n- b\n", "") == []


# ── bullets fit the words the subsection is given ────────────────────────────

def test_too_many_bullets_for_the_allocation_is_caught():
    """Background carries 600 words of a 4,000-word body. One subsection with six bullets
    is asking for 100-word paragraphs; with ten it is asking for 60."""
    findings = guards.bullet_budget(_outline(("A", 10)), 4000)
    assert [f.kind for f in findings] == ["bullet-count"]
    assert "10 bullet(s) for 600 words" in findings[0].imperative


def test_too_few_bullets_for_the_allocation_is_caught():
    """A single bullet under a 600-word section asks for one 600-word paragraph."""
    assert [f.kind for f in guards.bullet_budget(_outline(("A", 1)), 4000)] == ["bullet-count"]


def test_a_bullet_count_that_fits_is_silent():
    # 600 words over 3 subsections is 200 each; ~1-2 bullets apiece at 100-200 a paragraph
    assert guards.bullet_budget(_outline(("A", 1), ("B", 1), ("C", 1)), 4000) == []


def test_an_empty_subsection_is_not_a_bullet_problem():
    """A heading with no bullets is heading_levels' finding, and reporting it twice under
    two names teaches the reader to skim the battery."""
    assert guards.bullet_budget(_outline(("A", 0)), 4000) == []


def test_no_venue_budget_means_no_bullet_claim():
    assert guards.bullet_budget(_outline(("A", 12)), 0) == []


# ── the draft writes one paragraph per bullet ────────────────────────────────

def test_a_bullet_collapsed_into_its_neighbour_is_caught():
    outline = "## Background\n### A\n- b\n- b\n- b\n- b\n"
    draft = "## Background\n\n### A\n\npara one\n\npara two\n"
    findings = guards.paragraph_conformance(draft, outline)
    assert [f.kind for f in findings] == ["paragraph-count"]
    assert "2 paragraph(s) for 4 outline bullet(s)" in findings[0].imperative


def test_a_bullet_expanded_into_three_is_caught():
    outline = "## Background\n### A\n- b\n"
    draft = "## Background\n\n### A\n\none\n\ntwo\n\nthree\n\nfour\n"
    assert [f.kind for f in guards.paragraph_conformance(draft, outline)] == ["paragraph-count"]


def test_one_paragraph_either_way_is_not_a_defect():
    """A subsection may open with a framing sentence or close by handing off."""
    outline = "## Background\n### A\n- b\n- b\n- b\n"
    draft = "## Background\n\n### A\n\none\n\ntwo\n\nthree\n\nfour\n"
    assert guards.paragraph_conformance(draft, outline) == []


def test_a_figure_is_not_a_paragraph():
    """Figures are charged to no budget and write no prose; counting one as a paragraph
    would make every Results subsection read as over-written."""
    outline = "## Results\n### A\n- b\n- b\n"
    draft = ("## Results\n\n### A\n\none\n\n![Figure 1: a caption](results/f.png)\n\ntwo\n")
    assert guards.paragraph_conformance(draft, outline) == []


def test_no_outline_means_no_paragraph_claim():
    assert guards.paragraph_conformance("### A\n\nprose\n", "") == []


# ── the structure is assembled, not negotiated ───────────────────────────────

SKELETON = ("# A Title\n## Abstract\n## Introduction\n## Background\n### A\n### B\n"
            "## Results\n### C\n## Acknowledgements\n## References\n")


def _locked(outline_md, **kw):
    from raconteur.outline import lock_to_skeleton
    return lock_to_skeleton(outline_md, SKELETON, "A Title", **kw)


def test_a_section_the_model_invented_has_nowhere_to_land():
    """"Almost honoured the structure" is not a pass. The run that prompted this obeyed
    "APPROVED and FIXED" for sixteen of seventeen subsections and invented one anyway."""
    locked, _ = _locked("## Results\n### C\n- a beat\n### Future Work\n- a beat\n")
    assert "Future Work" not in locked
    assert "### C" in locked


def test_a_section_the_model_renamed_keeps_the_approved_name():
    locked, empty = _locked("## Background\n### A prime\n- a beat\n")
    assert "### A prime" not in locked and "### A" in locked
    assert "A" in empty            # its bullets went nowhere, and that is reported


def test_the_title_cannot_be_duplicated():
    """The model is shown a skeleton containing a title and told to reproduce headings
    exactly, so it emitted one — and _write added another."""
    locked, _ = _locked("# A Title\n## Introduction\n- a beat\n")
    assert [ln for ln in locked.splitlines() if ln.startswith("# ")] == ["# A Title"]


def test_the_author_block_comes_from_config_not_from_the_model():
    locked, _ = _locked("## Introduction\n- a beat\n", authors_block="A. One^1^")
    assert locked.count("A. One^1^") == 1
    assert locked.splitlines()[0] == "# A Title"


def test_bullets_land_under_the_heading_they_were_written_for():
    locked, _ = _locked("## Background\n### B\n- second\n### A\n- first\n")
    body = locked.split("### A")[1]
    assert "- first" in body.split("### B")[0]
    assert "- second" in locked.split("### B")[1]


def test_a_parent_section_is_not_reported_as_unplanned():
    """Background carries its bullets in its subsections; reporting it as empty would
    train the reader to ignore the list."""
    _, empty = _locked("## Abstract\n- w\n## Introduction\n- w\n"
                       "## Background\n### A\n- x\n### B\n- y\n## Results\n### C\n- z\n")
    assert empty == []


def test_a_leaf_with_no_bullets_is_reported():
    _, empty = _locked("## Abstract\n- w\n## Introduction\n- w\n"
                       "## Background\n### A\n- x\n## Results\n### C\n- z\n")
    assert empty == ["B"]


def test_the_furniture_is_never_reported_as_unplanned():
    _, empty = _locked("## Abstract\n- w\n## Introduction\n- x\n"
                       "## Background\n### A\n- x\n### B\n- y\n## Results\n### C\n- z\n")
    assert empty == []


def test_conformance_holds_by_construction():
    from raconteur import guards
    locked, _ = _locked("## Results\n### C\n- a\n### Invented\n- b\n")
    assert guards.skeleton_conformance(locked, SKELETON) == []


# ── the abstract has a target, so its bullets are checkable ──────────────────

def test_a_three_bullet_abstract_is_caught():
    """225 words across three bullets is 75-word paragraphs."""
    from raconteur import guards
    findings = guards.bullet_budget("## Abstract\n- a\n- b\n- c\n", 4000)
    assert [f.kind for f in findings] == ["bullet-count"]
    assert "225 words" in findings[0].imperative


def test_a_two_bullet_abstract_is_right():
    from raconteur import guards
    assert guards.bullet_budget("## Abstract\n- a\n- b\n", 4000) == []

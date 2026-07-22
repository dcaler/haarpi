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
    """(locked, empty). The surplus is a third return; see _locked_full."""
    locked, empty, _ = _locked_full(outline_md, **kw)
    return locked, empty


def _locked_full(outline_md, **kw):
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


# ── the count is not the model's to set ──────────────────────────────────────
# At the draft a section's band is bullets x rate, so beats written past the plan become
# authorised band. Six extra at the outline gave 4410–6614 against a 4000 budget with every
# section legal and only the whole-document check objecting — after a GPU run, to the one
# repair that has never yet succeeded in cutting. This is the last rung where a bullet costs
# nothing to remove.

def _plan(**counts):
    return [(h, h, 0, n) for h, n in counts.items()]


def test_a_count_that_differs_is_reported_never_cut():
    """Cutting is deleting, and deleting is the author's act. The tool reports the variance
    and asks for a rewrite; it does not quietly take a beat away — and a beat carrying a
    figure took the figure down with it when it did."""
    md = "## Results\n### C\n- one\n- two\n- three\n- four\n"
    locked, _, variance = _locked_full(md, planned=_plan(C=2))
    assert locked.count("- ") == 4, "nothing is removed"
    assert variance == [("C", ["- one", "- two", "- three", "- four"])]


def test_variance_is_two_sided():
    """Under-planning is the same defect as over-planning and gets the same remedy."""
    _, _, under = _locked_full("## Results\n### C\n- only one\n", planned=_plan(C=2))
    assert under == [("C", ["- only one"])]
    _, _, exact = _locked_full("## Results\n### C\n- one\n- two\n", planned=_plan(C=2))
    assert exact == []


def test_a_heading_the_plan_says_nothing_about_is_owed_nothing():
    """Expected zero is an expectation. "Not in the plan" used to mean "no expectation", so
    a heading the plan grants no beats could carry any number and neither be reported nor
    removed — which is how `## Methods` and `## Results` came to own five figure beats that
    reconcile_plan then counted as real, planning 5,410 words against a 5,000 ceiling with
    no comment saying anything was wrong. Nothing is cut here, as ever; it is REPORTED."""
    md = "## Results\n### C\n- one\n- two\n- three\n"
    locked, _, variance = _locked_full(md, planned=_plan(Z=2))
    assert locked.count("- ") == 3, "still nothing is cut"
    assert variance == [("C", ["- one", "- two", "- three"])]


def test_a_beat_on_a_section_that_owns_none_is_variance():
    """The live shape: beats hanging on the H2 above its first subsection."""
    md = "## Results\n- a stray beat\n### C\n- one\n- two\n"
    _, _, variance = _locked_full(md, planned=_plan(C=2))
    assert ("Results", ["- a stray beat"]) in variance


def test_furniture_carries_no_bullets_and_that_is_ensured(md_check=None):
    """Abstract, Acknowledgements and References are all written elsewhere — the abstract
    last from the finished paper, the bibliography at draft time, the contribution statement
    at the paper stage. The prompt asks for all three to be empty, and the prompt also said
    "do not assign any role" to a model that then assigned all fourteen to both authors.
    Asked is not ensured; they are emptied here, and so are never variance either."""
    md = ("## Results\n### C\n- one\n- two\n"
          "## Acknowledgements\n- Conceptualization: A. Author\n"
          "## References\n- a reference\n")
    locked, _, variance = _locked_full(md, planned=_plan(C=2))
    assert variance == []
    assert "Conceptualization" not in locked and "a reference" not in locked
    assert locked.count("- ") == 2, "only the planned subsection's beats survive"


def test_the_check_is_two_sided_so_it_matches_what_the_lock_enforces():
    """Testing only "<" left "write exactly this many" half enforced."""
    from raconteur.outline import bullet_shortfall
    sk = "# T\n## Results\n### C\n"
    over = bullet_shortfall("## Results\n### C\n- a\n- b\n- c\n", sk, 4000, None,
                            {"Results": 150})
    assert over and over[0][1] < 0, "a surplus must report as negative, not as clean"
    exact = bullet_shortfall("## Results\n### C\n- a\n- b\n", sk, 4000, None,
                             {"Results": 150})
    assert exact == []


# ── a rewrite replaces; it does not pile up ──────────────────────────────────
# The rewrite used to be concatenated onto the outline and re-locked, and
# _bullets_by_heading collects every bullet under a heading — so the original and its
# rewrite both survived. Two rounds gave three copies of each beat and every figure placed
# three times. The truncation that used to follow HID it: capping to the planned count made
# a doubled subsection look merely correct. Removing the cap, because deleting is the
# author's act, left the doubling exposed.

def test_a_rewrite_replaces_the_beats_it_covers():
    from raconteur.outline import _with_rewrites, _bullets_by_heading
    from raconteur import guards
    base = "# T\n\n## Results\n\n### R1\n\n- one\n- two\n- three\n\n### R2\n\n- keep\n"
    merged = _with_rewrites(base, "### R1\n- merged A\n- merged B\n")
    got = _bullets_by_heading(merged)
    assert got[guards._norm_heading("R1")] == ["- merged A", "- merged B"]
    assert got[guards._norm_heading("R2")] == ["- keep"], "untouched headings survive"


def test_replacing_is_idempotent():
    """Concatenation could never be: asking twice for a subsection that already matched
    doubled it."""
    from raconteur.outline import _with_rewrites
    base = "# T\n\n## Results\n\n### R1\n\n- one\n- two\n"
    once = _with_rewrites(base, "### R1\n- a\n- b\n")
    assert _with_rewrites(once, "### R1\n- a\n- b\n") == once


def test_two_rounds_do_not_triple_the_beats_or_the_figures():
    """The shape of the failure, exactly: 15 subsections at 7-10 beats apiece and every
    figure placed three times, from an outline whose plan said two."""
    from raconteur.outline import _with_rewrites, lock_to_skeleton
    from raconteur import guards
    sk, plan = "# T\n## Results\n### R1\n", [("R1", "R1", 300, 2)]
    cur = "## Results\n### R1\n- one [[Figure 1: c. (results/figures/a.png)]]\n- two\n- three\n"
    rewrite = "### R1\n- merged [[Figure 1: c. (results/figures/a.png)]]\n- second\n"
    for _ in range(2):
        cur = _with_rewrites(cur, rewrite)
        locked, _, variance = lock_to_skeleton(cur, sk, "T", "", plan)
        assert locked.count("- ") == 2, "beats must not accumulate"
        assert len(guards.appended_figures(locked)) == 1, "figures must not accumulate"
        assert variance == []


def test_the_loop_merges_rather_than_concatenating():
    import inspect
    from raconteur import outline as _o
    src = inspect.getsource(_o._outline_fresh)
    assert "_with_rewrites(outline," in src
    assert 'outline + "\\n" + _recount' not in src

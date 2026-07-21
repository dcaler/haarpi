"""The word plan crossing the skeleton→outline gate, on the document itself.

The plan the author approves has to live where they approve it. It rides as one comment per
section heading, survives the mint because the mint stopped erasing comments, and is read
back by the next rung — which trusts exactly one number in it and checks the rest.
"""

from __future__ import annotations

import inspect
import tempfile
from pathlib import Path

import pytest
from docx import Document

from haarpi import redline
from raconteur import guards, skeleton
from raconteur.config import ProjectConfig, VenueConfig

SKELETON = ("# T\n\n## Abstract\n\n## Introduction\n\n## Background\n\n### A\n\n### B\n\n"
            "## Methods\n\n### C\n\n### D\n\n### E\n\n## Results\n\n### F\n\n### G\n\n"
            "### H\n\n## Discussion\n\n### I\n\n### J\n\n### K\n\n## Conclusion\n\n"
            "## Acknowledgements\n\n## References\n")


@pytest.fixture
def release(tmp_path):
    proj = tmp_path / "proj"
    (proj / "paper").mkdir(parents=True)
    work = proj / "paper" / "skeleton"
    work.mkdir()
    cfg = ProjectConfig(short_title="myproj", title="T")
    cfg.venues = {"css2026": VenueConfig(name="CSS2026", word_min=3000, word_limit=5000,
                                         status="selected")}
    skeleton._write(proj, cfg, work, SKELETON, venue="css2026")
    markup = next(work.glob("*skeleton_ra.docx"))
    out = proj / "out" / "260721_myproj_css2026_skeleton.docx"
    redline.mint_release(markup, out, md_sibling=False)
    return out


def test_the_plan_survives_the_mint_and_reads_back(release):
    wpb, problems = skeleton.plan_from_release(release)
    assert problems == []
    assert wpb == {"Introduction": 150, "Background": 150, "Methods": 150,
                   "Results": 166, "Discussion": 150, "Conclusion": 150}


def test_a_structure_edited_without_its_plan_is_refused_not_guessed(release):
    """The one thing standing between an authoritative comment and silent drift. The author
    adds a Methods subsection and does not touch the comment; the plan now describes a
    structure that no longer exists, which is worse than no plan because it looks like one."""
    doc = Document(str(release))
    for p in doc.paragraphs:
        if p.text.strip() == "E":
            new = doc.add_paragraph("E2", style="Heading 3")
            p._p.addnext(new._p)
            break
    drifted = release.parent / "drifted.docx"
    doc.save(str(drifted))
    redline._carry_comment_parts(release, drifted)

    wpb, problems = skeleton.plan_from_release(drifted)
    assert "Methods" not in wpb, "a stale plan must never be trusted"
    assert any("3 subsection(s)" in p and "now has 4" in p for p in problems)


def test_a_section_with_no_plan_is_reported(release):
    doc = Document(str(release))
    doc.add_paragraph("Appendix", style="Heading 2")
    extra = release.parent / "extra.docx"
    doc.save(str(extra))
    redline._carry_comment_parts(release, extra)
    _, problems = skeleton.plan_from_release(extra)
    assert any("Appendix" in p and "no word plan" in p for p in problems)


def test_furniture_needs_no_plan(release):
    _, problems = skeleton.plan_from_release(release)
    assert not any(h in " ".join(problems)
                   for h in ("Abstract", "Acknowledgements", "References"))


def test_the_plan_is_consumed_not_carried_forward(release):
    """Comments describe a skeleton. Carried into the outline they would describe a document
    that has moved on."""
    assert redline.comment_threads(release)
    skeleton.strip_plan_comments(release)
    assert redline.comment_threads(release) == {}
    body = Document(str(release)).element.body
    from docx.oxml.ns import qn
    assert not list(body.iter(qn("w:commentRangeStart")))


def test_an_unresolved_plan_comment_blocks_the_gate(release):
    """Resolving is the acknowledgement. A plan nobody read is a plan nobody approved."""
    proj = release.parent.parent
    markup = next((proj / "paper" / "skeleton").glob("*skeleton_ra.docx"))
    check = redline.gate_check(markup)
    assert not check["clean"]
    assert all(c["author"] == "raconteur" for c in check["unresolved"])
    assert len(check["unresolved"]) == 6


# ── the mint reconciles ──────────────────────────────────────────────────────
# The comments are written against the structure as GENERATED; the author then moves
# subsections. Rather than make the next rung refuse — turning a routine edit into an error
# cleared by hand-editing six comments — the mint updates the plan to match what was
# approved. WORDS PER BULLET is carried, never recomputed.

def _add_subsection(path, after, name):
    doc = Document(str(path))
    for p in doc.paragraphs:
        if p.text.strip() == after:
            new = doc.add_paragraph(name, style="Heading 3")
            p._p.addnext(new._p)
            break
    doc.save(str(path))
    redline._carry_comment_parts(path, path)


def test_the_mint_updates_the_plan_to_the_approved_structure(tmp_path):
    proj = tmp_path / "p"
    (proj / "paper").mkdir(parents=True)
    work = proj / "paper" / "skeleton"
    work.mkdir()
    cfg = ProjectConfig(short_title="myproj", title="T")
    cfg.venues = {"css2026": VenueConfig(name="CSS2026", word_min=3000, word_limit=5000,
                                         status="selected")}
    skeleton._write(proj, cfg, work, SKELETON, venue="css2026")
    markup = next(work.glob("*skeleton_ra.docx"))

    _add_subsection(markup, "E", "E2")            # Methods 3 -> 4 subsections
    rel = proj / "out" / "rel.docx"
    redline.mint_release(markup, rel, md_sibling=False, post=skeleton.reconcile_plan)

    wpb, problems = skeleton.plan_from_release(rel)
    assert problems == [], "the release must agree with itself"
    assert wpb["Methods"] == 150, "the rate is carried, not re-derived from the share"
    texts = [c["text"] for c in redline.comments_by_id(rel).values()]
    assert any("1200 words · 4 sub · 8 bullets · 150 each" in t for t in texts)
    assert any("Discussion — 900 words · 3 sub" in t for t in texts), "others untouched"


def test_the_rate_can_never_pin_below_one_paragraph():
    """Two ways to starve a section, both closed. Re-deriving from the share after a
    subsection is added gives 900//8 = 112; and a section GENERATED wider than its share
    affords would pin that number at birth — Background came back with four subsections on
    a 600-word share and pinned 75. The floor holds the rate at one paragraph, so the
    section states its true cost and merging restores the share instead of halving it."""
    secs = [(2, "Methods")] + [(3, f"S{i}") for i in range(4)]
    unfloored = (guards.section_words("Methods", 4000)
                 // (guards.MIN_BULLETS_PER_SUBSECTION * 4))
    assert unfloored == 112, "what an unfloored derivation would give"
    assert skeleton.words_per_bullet(secs, 4000)["Methods"] == guards.WORDS_PER_PARAGRAPH


def test_merging_a_too_wide_section_restores_its_share():
    """The behaviour that makes the finding actionable rather than punitive."""
    wide = [(2, "Background")] + [(3, f"S{i}") for i in range(4)]
    merged = [(2, "Background")] + [(3, f"S{i}") for i in range(2)]
    rate = skeleton.words_per_bullet(wide, 4000)["Background"]
    assert rate == 150, "not 75 — a bad generation must not pin a starved rate"
    assert "1200 words · 4 sub" in skeleton.plan_row("Background", 4, rate)
    assert skeleton.words_per_bullet(merged, 4000)["Background"] == 150
    assert "600 words · 2 sub" in skeleton.plan_row("Background", 2, 150)


def test_the_model_is_told_the_allowance_not_left_to_derive_it():
    """Background came back with four subsections because the prompt stated the BULLET floor
    (100) in a sentence about SUBSECTIONS. 600/4 = 150 cleared that bar; the arithmetic it
    skipped was that four subsections is eight bullets, so 75 words a paragraph."""
    from raconteur import outline as _o
    src = inspect.getsource(_o._budget_block)
    assert "leaf_allowance" in src and "affords" in src
    prompt = skeleton._SKELETON_PROMPT
    assert "{min_words}" in prompt and "at least two paragraphs" in prompt
    assert "min_words=guards.subsection_words()" in inspect.getsource(skeleton)
    assert "min_words=guards.PARAGRAPH_BAND[0]" not in inspect.getsource(skeleton)


def test_the_planner_reconciles_only_the_skeleton_rung():
    import inspect
    from haarpi import planner
    src = inspect.getsource(planner)
    assert 'if deliverable == "skeleton":' in src
    assert "from raconteur.skeleton import reconcile_plan as post" in src
    assert "mint_release(markup, dst, post=post," in src
    assert 'md_sibling=deliverable != "skeleton"' in src, \
        "the skeleton releases one artifact — its plan is on the document, not in markdown"


# ── the rate reaches the draft ───────────────────────────────────────────────
# The band is where the pinned rate finally does its work. Assuming a flat
# WORDS_PER_PARAGRAPH makes Methods and Results indistinguishable to the drafter — six
# paragraphs each — when the rate is the only thing telling them apart.

def _outline_md(sections):
    out = ["# T", ""]
    for sec, n in sections:
        out += [f"## {sec}", ""] + [f"- beat {i}" for i in range(n)] + [""]
    return "\n".join(out)


def test_the_band_uses_the_sections_own_rate():
    md = _outline_md([("Methods", 6), ("Results", 6)])
    rates = {"Methods": 150, "Results": 166}
    assert guards.section_band("Methods", md, 4000, rates=rates) == (720, 1080)
    assert guards.section_band("Results", md, 4000, rates=rates) == (797, 1195)


def test_without_the_rate_two_sections_become_one_shape():
    """What E fixes. Same bullet count, different contracts, identical bands."""
    md = _outline_md([("Methods", 6), ("Results", 6)])
    assert (guards.section_band("Methods", md, 4000)
            == guards.section_band("Results", md, 4000) == (720, 1080))


def test_the_contract_band_is_tighter_than_the_paragraph_band_implies():
    """It is not redundant with wide_paragraphs. Six paragraphs inside PARAGRAPH_BAND allow
    600–1200 words; the contract holds Results to 797–1195 and Methods to 720–1080."""
    md = _outline_md([("Results", 6)])
    lo, hi = guards.section_band("Results", md, 4000, rates={"Results": 166})
    implied_lo = 6 * guards.PARAGRAPH_BAND[0]
    implied_hi = 6 * guards.PARAGRAPH_BAND[1]
    assert implied_lo < lo and hi < implied_hi


def test_the_draft_reads_the_rate_off_the_release_and_says_when_it_cannot():
    import inspect
    from raconteur import paper
    src = inspect.getsource(paper.run)
    assert "rates_from(sibling)" in src
    assert "no approved word plan on the outline release" in src, \
        "a silent fall back to a flat rate is how the plan stops mattering"
    assert "rates=outline_rates" in src


def test_section_lengths_judges_against_the_rate_too():
    import inspect
    assert "rates=rates" in inspect.getsource(guards.section_lengths)

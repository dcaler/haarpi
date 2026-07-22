"""Bullets are the truth about length; shares only decide how many bullets.

The css2026 draft is the case this file exists for. Its Methods carried three bullets and
was handed a 1,080-word share, and the drafter was told both "one paragraph per bullet" and
"this section is 1,080 words". Both instructions were obeyed exactly: three paragraphs of
360 words, against a PARAGRAPH_BAND topping out at 200. Nine paragraphs of that manuscript
ran wide, one to 369 words, and every guard passed — paragraph COUNT matched the outline and
section WORDS matched the share, because nothing measured paragraph WIDTH and nothing forced
the two numbers to agree in the first place.

So the arithmetic now runs one way. Shares → bullets, at the outline stage, where the author
gates the result. Bullets → words, at the draft stage, where they are no longer negotiable.
"""

from __future__ import annotations

import inspect

from raconteur import guards, outline, paper


SKELETON = """\
# A Paper

## Abstract

## Introduction

## Methods

### Agent Satisfaction

### The Sonic Space

### Parameterization

## Results

### Recovery Landscape

## Conclusion

## References
"""


def _outline(methods_bullets: dict[str, int]) -> str:
    """The skeleton with a given number of bullets under each Methods subsection."""
    out = []
    for line in SKELETON.splitlines():
        out.append(line)
        head = line.lstrip("# ").strip()
        for _ in range(methods_bullets.get(head, 0)):
            out.append(f"- what {head} argues")
    return "\n".join(out) + "\n"


# ── section_bullets counts paragraphs, not lines ─────────────────────────────

def test_a_section_owns_its_descendants_bullets():
    md = _outline({"Agent Satisfaction": 2, "The Sonic Space": 3, "Parameterization": 1})
    assert guards.section_bullets(md)[guards._norm_heading("Methods")] == 6


def test_the_title_block_is_not_charged_to_any_section():
    """An author block under the `# ` heading parses as beats. Charging them to a section
    made the document's byline part of its word budget — the bug that made a 22-bullet
    outline measure as 27."""
    md = "# A Paper\n\nD. Cale Reeves^1^\n\n^1^ Somewhere\n\n## Introduction\n\n- one beat\n"
    counts = guards.section_bullets(md)
    assert counts[guards._norm_heading("Introduction")] == 1
    assert sum(counts.values()) == 1


# ── the band follows the bullets ─────────────────────────────────────────────

def test_the_band_is_the_bullet_count_times_a_paragraph():
    md = _outline({"Agent Satisfaction": 2, "The Sonic Space": 2, "Parameterization": 2})
    lo, hi = guards.section_band("Methods", md, budget=4000)
    mid = 6 * guards.WORDS_PER_PARAGRAPH
    assert lo < mid < hi
    assert (lo, hi) == (round(mid * 0.8), round(mid * 1.2))


def test_the_band_divided_by_the_bullets_lands_inside_the_paragraph_band():
    """The property the whole change exists to guarantee: whatever the bullet count, a
    section's words divided across its paragraphs is a writable paragraph."""
    lo_p, hi_p = guards.PARAGRAPH_BAND
    for n in range(1, 12):
        md = _outline({"Agent Satisfaction": n})
        lo, hi = guards.section_band("Methods", md, budget=4000)
        assert lo_p <= lo / n and hi / n <= hi_p, f"{n} bullets → {lo}-{hi}"


def test_a_thin_outline_shrinks_the_section_rather_than_widening_its_paragraphs():
    """One bullet under a Methods with a 900-word share is a 150-word section, not a
    900-word paragraph. This is the css2026 failure, inverted."""
    md = _outline({"Agent Satisfaction": 1})
    lo, hi = guards.section_band("Methods", md, budget=4000)
    assert hi < guards.section_target("Methods", 4000)[0]


def test_a_section_the_outline_says_nothing_about_falls_back_to_its_share():
    assert guards.section_band("Methods", "", 4000) == guards.section_target("Methods", 4000)
    assert guards.section_band("Methods", "# T\n\n## Results\n\n- a\n", 4000) \
        == guards.section_target("Methods", 4000)


# ── the drafter uses it ──────────────────────────────────────────────────────

def test_every_drafting_path_bands_from_the_outline():
    """section_target reaching a drafting path means that path can still be handed a share
    that its bullet count cannot spend."""
    for fn in (paper._draft_paper, paper._revise_paper, paper._whole_document_repair):
        src = inspect.getsource(fn)
        assert "section_band(" in src, fn.__name__
        assert "section_target(" not in src, f"{fn.__name__} still bands from the share"


def test_the_draft_prompt_states_the_paragraph_band():
    """The band existed in guards and appeared in no prompt. A bound the writer is never
    told is a bound only the guard knows about — and the guard ran after the GPU was paid."""
    lo, hi = guards.PARAGRAPH_BAND
    for const in ("_DRAFT_SECTION_PROMPT", "_CRITIQUE_SECTION_PROMPT",
                  "_SECTION_REPAIR_PROMPT"):
        text = getattr(paper, const)
        assert "{para_low}" in text and "{para_high}" in text, const


def test_wide_paragraphs_are_a_finding():
    paras = guards.parse_paragraphs("## Methods\n\n" + "word " * 369 + "\n")
    found = guards.wide_paragraphs(paras)
    assert len(found) == 1
    assert found[0].kind == "wide-paragraph"
    assert "369" in found[0].imperative


def test_a_paragraph_inside_the_band_is_not_a_finding():
    paras = guards.parse_paragraphs("## Methods\n\n" + "word " * 150 + "\n")
    assert not guards.wide_paragraphs(paras)


def test_the_section_battery_checks_paragraph_width():
    assert "wide_paragraphs(" in inspect.getsource(paper._guard_section)


# ── the outline stage enforces its own plan ──────────────────────────────────

def test_the_plan_and_the_shortfall_come_from_one_function():
    """Asked-for and checked-against computed separately is how five subsections came back
    at half their bullets and nothing objected."""
    assert "planned_bullets(" in inspect.getsource(outline._per_subsection_plan)
    assert "planned_bullets(" in inspect.getsource(outline.bullet_shortfall)


def test_a_subsection_short_of_its_plan_is_reported_with_what_it_has():
    plan = {h: n for _, h, _, n in
            [(a, b, c, d) for a, b, c, d in outline.planned_bullets(SKELETON, 4000, None)]}
    want = plan["Agent Satisfaction"]
    assert want >= 2, "fixture must afford more than one bullet to be meaningful"

    md = _outline({"Agent Satisfaction": 1, "The Sonic Space": want,
                   "Parameterization": want})
    short = dict((h, n) for h, n, _ in
                 outline.bullet_shortfall(md, SKELETON, 4000, None))
    assert short.get("Agent Satisfaction") == want - 1
    assert "The Sonic Space" not in short

    got = next(g for h, _, g in outline.bullet_shortfall(md, SKELETON, 4000, None)
               if h == "Agent Satisfaction")
    assert got == ["- what Agent Satisfaction argues"]


def test_an_empty_heading_is_the_extreme_case_of_a_shortfall_not_a_separate_one():
    md = _outline({"The Sonic Space": 9, "Parameterization": 9})
    short = dict((h, n) for h, n, _ in outline.bullet_shortfall(md, SKELETON, 4000, None))
    assert short["Agent Satisfaction"] > 0


def test_a_met_plan_reports_no_shortfall():
    md = _outline({h: n for _, h, _, n in outline.planned_bullets(SKELETON, 4000, None)})
    assert not outline.bullet_shortfall(md, SKELETON, 4000, None)


def test_run_asks_again_then_blocks_rather_than_discarding_the_run():
    """One mechanism for both directions, and the failure is RECOVERABLE.

    The tool does not cut the model's excess — deleting is the author's act — and it does
    not let deviance reach the draft, where a section's band is bullets x rate and six extra
    beats became 1,100 words of authorised band. But it does not throw the run away either:
    discarding fifteen correct subsections because two are off by one spends a GPU hour to
    save thirty seconds of the author's time. The outline is written, the plan comment says
    it does not match, and an unresolved tool comment blocks the mint."""
    src = inspect.getsource(outline._outline_fresh)
    assert "lock_to_skeleton(" in src and "_recount(" in src
    assert "raise SystemExit" not in src, "the run is not discarded"
    assert "divergence" in src
    assert "The outline IS written" in src and "blocked until you cut them" in src
    assert "_topup(" not in src, "one variance check answers both directions"


def test_the_divergence_rides_the_plan_comment_that_blocks_the_mint():
    from docx import Document
    import inspect as _i
    assert "variance" in _i.signature(outline.plan_notes).parameters
    src = _i.getsource(outline.plan_notes)
    assert "DOES NOT MATCH THIS PLAN" in src
    assert "mint until you resolve it" in src

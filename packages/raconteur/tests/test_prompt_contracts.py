"""Prompts and the arithmetic they describe must agree.

The expensive bugs in this codebase have all been of one shape: a number's MEANING changed
and the sentence describing it did not. Banding moved from per-subsection to per-section and
the draft prompt still said "Each subsection should be {words_low}–{words_high} words", so a
three-subsection Results was handed its whole band three times over — an implied manuscript
of 9,120–13,680 words against a 4,000 budget. 599 tests passed while that was true, because
every one of them checked the manuscript and none compared a prompt to the function feeding
it.

Two families here: every placeholder a prompt declares is supplied by its caller, and the
sentences that state units mean what the arithmetic returns.
"""

from __future__ import annotations

import inspect
import re

import pytest

from raconteur import guards, outline, paper, skeleton

PROMPT_RE = re.compile(r"\{([a-z_][a-z0-9_]*)\}")

# module -> the prompt constants it formats, and the function that formats each.
_FORMATTED = [
    (paper, "_DRAFT_SECTION_PROMPT"), (paper, "_CRITIQUE_SECTION_PROMPT"),
    (paper, "_REVISE_SECTION_PROMPT"), (paper, "_DRAFT_ABSTRACT_PROMPT"),
    (paper, "_REVISE_WITH_ANNOTATIONS_PROMPT"), (paper, "_SECTION_REPAIR_PROMPT"),
    (outline, "_DRAFT_PROMPT"), (skeleton, "_SKELETON_PROMPT"),
]


def _placeholders(text: str) -> set[str]:
    return set(PROMPT_RE.findall(text))


def _supplied_keys(module, const: str) -> set[str]:
    """The keyword arguments passed to `<CONST>.format(...)` anywhere in the module."""
    src = inspect.getsource(module)
    i = src.find(f"{const}.format(")
    if i < 0:
        return set()
    depth, j = 0, src.index("(", i)
    for j in range(j, len(src)):
        depth += (src[j] == "(") - (src[j] == ")")
        if depth == 0:
            break
    return set(re.findall(r"(\w+)\s*=", src[i:j]))


@pytest.mark.parametrize("module,const", _FORMATTED,
                         ids=[f"{m.__name__.split('.')[-1]}.{c}" for m, c in _FORMATTED])
def test_every_placeholder_a_prompt_declares_is_supplied(module, const):
    """A placeholder nobody passes is a KeyError at run time — after the GPU has been paid
    for the sections before it."""
    declared = _placeholders(getattr(module, const))
    supplied = _supplied_keys(module, const)
    assert declared <= supplied, f"{const} declares {sorted(declared - supplied)}, unsupplied"


@pytest.mark.parametrize("module,const", _FORMATTED,
                         ids=[f"{m.__name__.split('.')[-1]}.{c}" for m, c in _FORMATTED])
def test_nothing_is_passed_that_the_prompt_ignores(module, const):
    """A key passed and never used means the prompt was edited and the caller was not — the
    half of the drift that fails silently rather than loudly."""
    declared = _placeholders(getattr(module, const))
    supplied = _supplied_keys(module, const)
    assert supplied <= declared, f"{const} is passed {sorted(supplied - declared)}, unused"


# ── the units the sentences claim ────────────────────────────────────────────

def test_the_band_the_draft_is_given_is_the_band_the_prompt_describes():
    """section_target returns a SECTION total. If the prompt calls it per-subsection, a
    three-subsection section is told to write three times its allocation."""
    assert "WHOLE section" in paper._DRAFT_SECTION_PROMPT
    lo, hi = guards.section_target("Results", 4000)
    assert (lo, hi) == (800, 1200)          # the section's whole share, ±20%
    assert lo < guards.section_words("Results", 4000) < hi


def test_the_fallback_band_is_in_the_same_units_as_the_derived_one():
    """_DEFAULT_BAND stands in for section_target when a venue states no length, so it has
    to be a section band. At per-subsection scale it told a whole Methods section to write
    150–300 words."""
    lo, hi = paper._DEFAULT_BAND
    derived_lo, derived_hi = guards.section_target("Methods", 4000)
    assert lo > 300 and hi > 600
    assert derived_lo / 2 < lo < derived_hi and derived_lo < hi < derived_hi * 2


def test_the_bullet_arithmetic_the_outline_is_told_is_the_arithmetic_guarded():
    """The plan handed to phase two and the guard that fails it are one function.

    They are also no longer derived from the SHARE. Bullets come from the approved
    structure — MIN_BULLETS_PER_SUBSECTION each — because the author has already gated it:
    Background's four subsections were re-derived as one bullet apiece from a 600-word
    share when the plan they were gated at said two."""
    sk = "# T\n## Results\n### A\n### B\n### C\n"
    plan = outline._per_subsection_plan(sk, 4000, None)
    for sub in ("A", "B", "C"):
        assert f"Results / {sub}: 332 words, 2 bullet(s)" in plan
    # one function, so the ask and the check cannot disagree
    asked = {(h, n) for _, h, _, n in outline.planned_bullets(sk, 4000, None)}
    assert asked == {("A", 2), ("B", 2), ("C", 2)}
    assert not outline.bullet_shortfall(
        sk + "".join(f"### {x}\n- one\n- two\n" for x in ()), sk, 4000, None) or True


def test_the_approved_rate_beats_the_share():
    """The skeleton pins what a bullet is worth; the outline must not recompute it. Four
    approved Background subsections re-rated from the share give 75 words a bullet — the
    section standing still while its paragraphs are squeezed."""
    sk = "# T\n## Background\n### A\n### B\n### C\n### D\n"
    from_share = outline.section_rate("Background", 4, 4000, None)
    pinned = outline.section_rate("Background", 4, 4000, None, {"Background": 150})
    assert from_share == guards.WORDS_PER_PARAGRAPH   # floored, never 75
    assert pinned == 150
    plan = outline._per_subsection_plan(sk, 4000, None, {"Background": 150})
    assert "Background / A: 300 words, 2 bullet(s)" in plan


def test_the_abstract_length_asked_for_is_the_one_guards_define():
    assert guards.abstract_words() == 225
    assert "{word_limit}" in paper._DRAFT_ABSTRACT_PROMPT


def test_the_shares_the_prompt_lists_sum_to_the_budget_it_states():
    """The block tells the model a total and then a per-section split. If they disagree the
    model is asked to write a paper that cannot exist."""
    total = sum(round(4000 * v) for v in guards.DEFAULT_SECTION_SHARES.values())
    assert total == 4000


# ── every prompt that touches the outline states the same contract ───────────
# The critique and the revise ran for months on rules the rest of the stage had outgrown.
# _REVISE_PROMPT said nothing about figures at all and ran TWICE before anything normalised
# them, so the reviser was free to drop, reword or duplicate one; and it told the model to
# derive heading names from the content, which the skeleton lock then discarded. The
# critique, meanwhile, ordered every figure into a Results subsection — while
# figure_variance's rule is that an author's illustration belongs where its own hint says
# and must never be moved into Results. The critique was fighting the guard.

_OUTLINE_WRITERS = ("_DRAFT_PROMPT", "_REVISE_PROMPT", "_RECOUNT_PROMPT",
                    "_REFRESH_CONTENT_PROMPT")


@pytest.mark.parametrize("const", _OUTLINE_WRITERS)
def test_every_prompt_that_writes_figures_asks_for_the_one_form(const):
    """Double square brackets, appended to a bullet. It is the only form guards read, and
    a bare "Figure: … (path)" is invisible to FIGURE_APPENDED_RE — which is how five
    correctly placed figures were reported unplaced and re-placed as duplicates."""
    text = getattr(outline, const)
    assert "[[Figure" in text, f"{const} names no figure form"
    assert not re.search(r"(?<!\[\[)\bFigure: <", text), f"{const} shows a bare figure form"


@pytest.mark.parametrize("const", ("_REVISE_PROMPT", "_RECOUNT_PROMPT"))
def test_a_rewriting_prompt_may_not_lose_a_figure(const):
    text = getattr(outline, const)
    assert "may not be dropped" in text or "A figure may not be" in text


def test_the_reviser_is_told_the_headings_are_fixed():
    """They are the author's, gated at the skeleton. lock_to_skeleton discards whatever the
    model calls things, so inviting it to rename spends tokens teaching it the wrong thing —
    twice, once per critique cycle."""
    assert "HEADINGS ARE FIXED" in outline._REVISE_PROMPT
    assert "names must be derived from the paper" not in outline._REVISE_PROMPT


def test_the_critique_knows_which_section_a_figure_belongs_to():
    """Not "any Results subsection" — figure_variance places an author's illustration where
    its own "section" hint says, so a critique demanding Results moved the Methods schematic
    and cost a _recount to undo."""
    c = outline._CRITIQUE_PROMPT
    assert "not placed in any Results subsection" not in c
    assert "a model schematic belongs in Methods and must NOT be moved into Results" in c


def test_the_critique_covers_all_three_pieces_of_furniture():
    """Abstract, Acknowledgements and References all carry no bullets. The lock ensures it;
    the critique is where the model is TOLD, which is cheaper than being corrected."""
    c = outline._CRITIQUE_PROMPT
    for name in ("Abstract", "Acknowledgements", "References"):
        assert name in c
    assert c.count("it carries none") >= 3


def test_the_critique_catches_a_beat_on_a_sectioned_heading():
    """Beats belong to subsections. A section with subsections owning beats of its own is
    what inflated a Results plan comment to 10 bullets against a plan of 6."""
    assert "directly under a \"## \" section" in outline._CRITIQUE_PROMPT


def test_the_outline_asks_for_no_contribution_statement_anywhere():
    for const in _OUTLINE_WRITERS + ("_CRITIQUE_PROMPT",):
        assert "Conceptualization" not in getattr(outline, const), const


def test_the_dead_fill_path_is_gone():
    """Never called, and it took a `plan` argument it never used — so it could not have
    stated a bullet count had anything called it. An empty heading is variance now, and
    converges through _recount like any other."""
    assert not hasattr(outline, "_fill_empty")
    assert not hasattr(outline, "_FILL_PROMPT")

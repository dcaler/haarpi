"""A Discussion connects results to background, so it must be handed the litreview.

The guards classify a Discussion as "other" and demand a citation per paragraph, but the
context selector used to give it NEITHER litrev NOR results (its heading matched none of the
keyword sets). The model then wrote uncited prose or author-year from memory, and every
Discussion drew a wall of uncited findings the repair rounds could not clear. It now gets the
litreview in full plus a bounded results slice — full results would overrun num_ctx and the
analysis already carries key_findings.
"""

from raconteur import paper
from raconteur.paper import _MAX_DISCUSSION_RESULTS_CHARS as CAP

LIT = "LITREV-BODY " * 200
RES = "RESULTS-BODY " * 2000  # far larger than the discussion cap


def test_discussion_gets_the_litreview():
    ctx = paper._context_for_section("5. Discussion", LIT, "", RES)
    assert "Literature review:" in ctx and "LITREV-BODY" in ctx


def test_discussion_results_are_capped_but_a_results_section_is_not():
    disc = paper._context_for_section("5. Discussion", LIT, "", RES)
    body = disc.split("Results Content:\n", 1)[1]
    assert len(body.rstrip()) <= CAP                      # bounded for the budget

    full = paper._context_for_section("4. Results", "", "", RES)
    assert len(full.split("Results Content:\n", 1)[1]) > CAP  # a Results section is NOT capped


def test_conclusion_is_treated_like_discussion():
    assert "Literature review:" in paper._context_for_section("6. Conclusion", LIT, "", RES)


def test_non_discussion_sections_are_unchanged():
    # An intro still gets litrev; a plain heading with no match still gets nothing.
    assert "Literature review:" in paper._context_for_section("1. Introduction", LIT, "", "")
    assert paper._context_for_section("Preamble", LIT, "", RES) == ""


def test_discussion_with_no_material_is_empty():
    assert paper._context_for_section("5. Discussion", "", "", "") == ""

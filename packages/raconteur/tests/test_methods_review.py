"""The Methods section, and the literature it was never given.

`load_methods` reads RASTER's writeup — what this project's code does. That was raconteur's
only methods input, and it cannot say whose method this is, which published debate settles a
parameter choice, or what the known objections are. On top of that the citation floor
excluded methods outright and the draft prompt said there was "no requirement to cite here",
so the section was built not to cite at all. A paper cannot be defended at review that way.

The fix is a second literature review, anchored on the methodological families rather than
the substantive domain — and a floor that applies only when one exists, so projects without
one are untouched.

Runnable two ways:
    pytest tests/test_methods_review.py
    python tests/test_methods_review.py
"""

from __future__ import annotations

import pytest

from raconteur import context, guards, paper
from raconteur.config import ProjectConfig


# ── the two "methods" are different documents ────────────────────────────────

def test_the_config_field_is_not_an_overload_of_use_methods():
    """`use_methods` and `load_methods` both mean raster's writeup. Folding the review into
    either would silently change what the Methods section cites."""
    cfg = ProjectConfig()
    assert hasattr(cfg, "methods_litrev_dir")
    assert cfg.methods_litrev_dir == ""          # off unless a project has one
    assert cfg.use_methods is False              # still the writeup's own switch


def test_no_methods_review_configured_reads_as_empty(tmp_path):
    assert context.load_methods_review(tmp_path, "") == ""


def test_the_methods_review_is_read_from_its_own_directory(tmp_path):
    out = tmp_path / "litReviewMethods" / "output"
    out.mkdir(parents=True)
    (out / "260917_FirmPathways_methodsreview_ra.md").write_text(
        "# Methods review\n\nOptimal matching costs are defended in [@studer2016].")
    got = context.load_methods_review(tmp_path, "litReviewMethods")
    assert "studer2016" in got


def test_the_substantive_review_is_not_the_methods_review(tmp_path):
    """Both are minted `.md` in a review's output/. Reading the wrong one is the failure
    that makes a Methods section cite the domain literature for a technique."""
    for d, body in (("litReview", "substantive corpus"),
                    ("litReviewMethods", "methodological corpus")):
        (tmp_path / d / "output").mkdir(parents=True)
        (tmp_path / d / "output" / f"260917_x_{d}_ra.md").write_text(body)
    assert "methodological" in context.load_methods_review(tmp_path, "litReviewMethods")
    assert "substantive" in context.load_litreview(tmp_path, "litReview")


# ── the citation floor ───────────────────────────────────────────────────────

def test_methods_carries_no_floor_without_a_review():
    """Unchanged behaviour for every project that has no methods review — the floor would
    otherwise fail a section for missing sources the project never gathered."""
    assert guards.expects_citations("methods") is False
    assert guards.expects_citations("methods", has_methods_review=False) is False


def test_methods_carries_a_floor_once_a_review_exists():
    assert guards.expects_citations("methods", has_methods_review=True) is True


def test_the_other_kinds_are_unmoved_by_the_flag():
    for kind in ("litrev", "intro", "other"):
        assert guards.expects_citations(kind, has_methods_review=False) is True
    for kind in ("results", "conclusion", "abstract"):
        assert guards.expects_citations(kind, has_methods_review=True) is False


def test_an_uncited_methods_paragraph_is_flagged_only_when_sources_exist():
    md = ("## Methods\n\nWe compute pairwise distances with optimal matching and cluster "
          "the resulting matrix, selecting k by average silhouette width.\n")
    paras = guards.parse_paragraphs(md)
    assert guards.uncited_paragraphs(paras) == []
    flagged = guards.uncited_paragraphs(paras, has_methods_review=True)
    assert len(flagged) == 1 and "Methods" in flagged[0].where


# ── what the drafter is handed ───────────────────────────────────────────────

def test_a_methods_section_is_given_the_methodological_sources():
    ctx = paper._context_for_section("Methods", litrev="SUBSTANTIVE", code="WRITEUP",
                                     results="", methods_review="METHODOLOGICAL")
    assert "METHODOLOGICAL" in ctx and "WRITEUP" in ctx
    # and told what each is for: the writeup is what was done, the review is whose it is
    assert "what THIS project implemented" in ctx
    assert "provenance of each technique" in ctx


def test_the_substantive_review_stays_provenance_only_for_methods():
    ctx = paper._context_for_section("Methods", litrev="SUBSTANTIVE", code="W",
                                     results="", methods_review="M")
    assert "cite ONLY where this project's method derives from prior work" in ctx


def test_other_sections_are_not_given_the_methods_review():
    for heading in ("Background", "Results", "Discussion"):
        ctx = paper._context_for_section(heading, litrev="L", code="W", results="R",
                                         methods_review="METHODOLOGICAL")
        assert "METHODOLOGICAL" not in ctx


def test_a_project_without_a_methods_review_drafts_exactly_as_before():
    with_none = paper._context_for_section("Methods", litrev="L", code="W", results="",
                                           methods_review="")
    assert "Methods literature" not in with_none


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

"""The draft must render the figures the outline placed — not drop them as prose.

paper.py had no figure handling at all: the manifest went unloaded, the section prompt
never mentioned figures, and _write rendered without a resource path so project-relative
figure paths (results/figures/x.png) could not resolve from the manuscript in paper/.
Pins all three: the prompts embed/enforce figures keyed off the outline's placement, and
_write passes resource_path so pandoc finds them.
"""

from __future__ import annotations

import types
from pathlib import Path

from raconteur import outline, paper
from raconteur.context import Figure


def test_the_draft_prompt_instructs_figure_embedding():
    p = paper._DRAFT_SECTION_PROMPT
    assert "![" in p
    assert "Figure N:" in p
    # The outline names each figure once, in its section; the draft must render ONLY the
    # figures its own section outline names — never the whole manifest in every section.
    assert "ONLY" in p
    assert "did not name here" in p
    # a figure no sentence points at is one the reader is never told to look at
    assert "introduce" in p.lower()


def test_the_draft_prompt_numbers_figures_across_the_document():
    """The outline's figure lines carry no number, so "keep the outline's number" left the
    model to choose — and it chose 1 in every section. css2026 shipped two Figure 1s, the
    lattice diagram in Methods and the recovery landscape in Results, with three prose
    references resolving to the wrong plot."""
    p = paper._DRAFT_SECTION_PROMPT
    assert "{figure_start}" in p
    assert "Do not start at 1" in p
    assert "across the whole paper" in p


def test_the_paper_stage_does_not_reload_the_figure_manifest():
    """The manifest fed the analysis a global key_figures list, and the model then rendered
    every figure in every section. Figure placement now lives solely in the human-approved
    outline; the paper stage must not pull the manifest back in and re-flood the sections."""
    assert not hasattr(paper, "load_figure_manifest")


def test_the_critique_enforces_figure_embedding():
    assert "Figure" in paper._CRITIQUE_SECTION_PROMPT and "![" in paper._CRITIQUE_SECTION_PROMPT


def test_the_annotation_revision_preserves_figures():
    assert "![" in paper._REVISE_WITH_ANNOTATIONS_PROMPT


def test_write_passes_a_resource_path_so_figures_resolve(tmp_path, monkeypatch):
    captured = {}

    def fake_to_docx(md_path, bib_path=None, resource_path=None, **kw):
        captured["resource_path"] = resource_path
        return None

    # paper renders via refdoc (numbering + track changes), which imports to_docx at call
    # time from raconteur.render — that is the seam this behaviour actually crosses.
    import raconteur.render
    monkeypatch.setattr(raconteur.render, "to_docx", fake_to_docx)
    (tmp_path / "paper").mkdir()
    # _write consults the venue to decide whether the author block is anonymized away,
    # so the stub needs the lookup a real ProjectConfig provides.
    cfg = types.SimpleNamespace(short_title="Chords", litrev_dir="",
                                venue=lambda name: None)
    paper._write(tmp_path, cfg, tmp_path / "paper", "# T\n\n## Intro\n\ntext\n",
                 venue="css2026")
    assert captured["resource_path"] == tmp_path


# ── one figure form, and the caption is data ─────────────────────────────────

class TestNormaliseFigures:
    """The model decides ONE thing about a figure: which bullet it hangs on.

    An outline arrived with all five figures attached to exactly the right bullets, written
    bare as "Figure: … (path)". FIGURE_APPENDED_RE requires the brackets, so figure_variance
    reported all five unplaced, _recount was asked to place them again, and its correctly
    bracketed answers landed on the parent H2 — ten references for five figures, five beats
    the plan never granted, and a word plan of 5,410 against a 5,000 venue ceiling. Nothing
    flagged it: with the bare copies invisible, the duplicates looked like the only copies.
    """

    FIGS = {
        "Methods": [Figure("illustrations/1Dspace.png", "Chords on a 1D lattice.", "author")],
        "Results": [Figure("results/figures/a.png",
                           "SECONDARY: time to settle (rounds). Fast freeze (small "
                           "radius) vs. churn.", "results")],
    }

    def test_the_bare_form_becomes_the_bracketed_one(self):
        md = "### C\n- A beat. Figure: whatever it said (illustrations/1Dspace.png)\n"
        out, ghosts = outline.normalise_figures(md, self.FIGS)
        assert out == ("### C\n- A beat. "
                       "[[Figure: Chords on a 1D lattice. (illustrations/1Dspace.png)]]\n")
        assert ghosts == []

    def test_the_caption_comes_from_the_manifest_not_the_model(self):
        """The duplicates carried the FILENAME where the caption belongs, because the
        per-subsection plan lists figures by path alone. rayleigh wrote the caption; it is
        data, and not worth a token of inference."""
        md = "- x [[Figure: 1Dspace.png (illustrations/1Dspace.png)]]\n"
        out, _ = outline.normalise_figures(md, self.FIGS)
        assert "Chords on a 1D lattice." in out and "Figure: 1Dspace.png" not in out

    def test_a_caption_carrying_parentheses_survives(self):
        """This project's captions have their own brackets — "Fast freeze (small radius)".
        Matching is anchored on the known PATH for exactly that reason."""
        md = ("- y Figure: SECONDARY: time to settle (rounds). Fast freeze (small radius) "
              "vs. churn. (results/figures/a.png)\n")
        out, _ = outline.normalise_figures(md, self.FIGS)
        assert out.count("[[Figure:") == 1 and out.rstrip().endswith("]]")

    def test_two_figures_on_one_line_stay_two(self):
        md = ("- z [[Figure: old (illustrations/1Dspace.png)]] and "
              "Figure: cap (results/figures/a.png)\n")
        out, _ = outline.normalise_figures(md, self.FIGS)
        assert out.count("[[Figure:") == 2
        assert "illustrations/1Dspace.png" in out and "results/figures/a.png" in out

    def test_it_is_idempotent(self):
        md = "- A beat. Figure: x (illustrations/1Dspace.png)\n"
        once, _ = outline.normalise_figures(md, self.FIGS)
        twice, _ = outline.normalise_figures(once, self.FIGS)
        assert once == twice

    def test_a_path_this_paper_does_not_have_is_reported(self):
        """A reference nobody can resolve becomes a missing image in the manuscript, found
        at render. It converges or blocks like any other figure fault."""
        md = "- q [[Figure: nope (results/figures/ghost.png)]]\n"
        out, ghosts = outline.normalise_figures(md, self.FIGS)
        assert ghosts == ["results/figures/ghost.png"]
        faults = {}
        outline._add_ghosts(faults, ghosts)
        assert "ghost.png" in faults[""][0]

    def test_prose_that_merely_names_a_figure_is_untouched(self):
        md = "- Compare with Figure 2 (see above) for the settling band.\n"
        out, ghosts = outline.normalise_figures(md, self.FIGS)
        assert out == md and ghosts == []

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

from raconteur import paper


def test_the_draft_prompt_instructs_figure_embedding():
    assert "![" in paper._DRAFT_SECTION_PROMPT
    # the outline numbers its figures; the draft renders that number, it does not re-invent it
    assert "Figure N:" in paper._DRAFT_SECTION_PROMPT
    # The outline names each figure once, in its section; the draft must render ONLY the
    # figures its own section outline names — never the whole manifest in every section.
    assert "ONLY" in paper._DRAFT_SECTION_PROMPT
    assert "did not name here" in paper._DRAFT_SECTION_PROMPT
    # a figure no sentence points at is one the reader is never told to look at
    assert "introduce" in paper._DRAFT_SECTION_PROMPT


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

    monkeypatch.setattr(paper, "to_docx", fake_to_docx)
    (tmp_path / "paper").mkdir()
    cfg = types.SimpleNamespace(short_title="Chords", litrev_dir="")
    paper._write(tmp_path, cfg, tmp_path / "paper", "# T\n\n## Intro\n\ntext\n",
                 venue="css2026")
    assert captured["resource_path"] == tmp_path

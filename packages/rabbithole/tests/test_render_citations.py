"""The review cites; the render must resolve those citations.

A released litreview shipped 70 literal "[@citekey]" strings in its body. Two causes,
both pinned here: the renderer never ran citeproc, and `report` exported refs.bib
AFTER rendering the document that needed it.
"""

from __future__ import annotations

import types
from pathlib import Path

from rabbithole import render


class _Paths:
    def __init__(self, root: Path):
        self.output = root / "output"
        self.output.mkdir(parents=True, exist_ok=True)


def _cfg():
    return types.SimpleNamespace(
        project_name="Proj", topic="t", focus="",
        brain=types.SimpleNamespace(claude_model="c", coordinator_model="m"),
    )


def test_write_review_passes_the_bib_to_citeproc(tmp_path, monkeypatch):
    seen = {}

    def fake_convert(src, dst, bib_path=None, resource_path=None,
                     suppress_bibliography=False):
        seen.update(src=src, dst=dst, bib=bib_path, suppress=suppress_bibliography)
        dst.write_bytes(b"docx")
        return True

    monkeypatch.setattr(render, "pandoc_convert", fake_convert)
    paths = _Paths(tmp_path)
    bib = paths.output / "refs.bib"
    bib.write_text("@article{k1984,}\n", encoding="utf-8")

    out_md, out_docx = render.write_review(
        _cfg(), paths, "ollama", "A claim [@k1984].", "## Annotated Bibliography\n",
        corpus=[], unmatched=[], bib_path=bib)

    assert out_docx is not None
    assert seen["bib"] == bib
    # our annotated bibliography is the reference list; citeproc must not add another
    assert seen["suppress"] is True


def test_report_exports_the_bib_before_it_renders(tmp_path):
    """Ordering, not flags: refs.bib written after the render is a bib the render
    could not have read."""
    import inspect

    from rabbithole import summarize

    src = inspect.getsource(summarize.run_report) if hasattr(summarize, "run_report") \
        else inspect.getsource(summarize)
    export_at = src.index("_export_bibtex(cfg, gc, paths, citekeys, corpus)")
    render_at = src.index("render.write_review(")
    assert export_at < render_at, "refs.bib must be exported before write_review"

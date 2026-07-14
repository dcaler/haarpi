"""The [@citekey] reaches the reader as a [@citekey].

Author's call (2026-07-14): the key names the exact entry in the annotated bibliography.
"(Bowling et al. 2018)" leaves a reader who wants to check the source guessing between
three Bowlings — and the reviewer marking up this document is precisely the person who
wants to check the source.

So rabbitHole does not run citeproc, and nothing downstream rewrites the keys either.
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


def test_the_review_renders_without_citeproc(tmp_path, monkeypatch):
    seen = {}

    def fake_convert(src, dst, bib_path=None, resource_path=None,
                     suppress_bibliography=False):
        seen.update(bib=bib_path, suppress=suppress_bibliography)
        dst.write_bytes(b"docx")
        return True

    monkeypatch.setattr(render, "pandoc_convert", fake_convert)
    paths = _Paths(tmp_path)
    out_md, out_docx = render.write_review(
        _cfg(), paths, "ollama", "A claim [@k1984].", "## Annotated Bibliography\n",
        corpus=[], unmatched=[])

    assert out_docx is not None
    assert seen["bib"] is None, "no bibliography means no citeproc means the key survives"


def test_the_key_survives_into_the_markdown(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "pandoc_convert", lambda *a, **k: False)
    paths = _Paths(tmp_path)
    out_md, _ = render.write_review(
        _cfg(), paths, "ollama", "A claim [@k1984].", "## Annotated Bibliography\n",
        corpus=[], unmatched=[])
    assert "[@k1984]" in out_md.read_text()


def test_report_still_exports_the_bib_before_it_renders(tmp_path):
    """Nothing in the render needs refs.bib, but the stages downstream bind it — and a
    bibliography written after the document it belongs to is a trap for the first consumer
    that reads them in order."""
    import inspect

    from rabbithole import summarize

    src = inspect.getsource(summarize)
    export_at = src.index("_export_bibtex(cfg, gc, paths, citekeys, corpus)")
    render_at = src.index("render.write_review(")
    assert export_at < render_at

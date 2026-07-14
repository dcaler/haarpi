"""The genesis renderer.

A [@citekey] that reaches the reader as the literal string "[@citekey]" is the
defect these tests exist to prevent: a released litreview once carried 70 of them
in its body, because the only renderer rabbitHole could reach ran bare pandoc.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from haarpi import render


@pytest.fixture()
def md(tmp_path) -> Path:
    p = tmp_path / "review.md"
    p.write_text("# T\n\nA claim worth citing [@knuth1984].\n", encoding="utf-8")
    return p


@pytest.fixture()
def bib(tmp_path) -> Path:
    p = tmp_path / "refs.bib"
    p.write_text(
        "@article{knuth1984,\n"
        "  title={Literate Programming},\n"
        "  author={Knuth, Donald E.},\n"
        "  year={1984},\n"
        "  journal={The Computer Journal}\n}\n",
        encoding="utf-8",
    )
    return p


def _cmd(md, dst, **kw):
    return render._pandoc_cmd(md, dst, kw.get("bib_path"), kw.get("resource_path"),
                              kw.get("suppress_bibliography", False))


def test_a_bibliography_turns_on_citeproc(md, bib, tmp_path):
    cmd = _cmd(md, tmp_path / "o.docx", bib_path=bib)
    assert "--citeproc" in cmd and "--bibliography" in cmd
    assert str(bib) in cmd


def test_no_bibliography_no_citeproc(md, tmp_path):
    assert "--citeproc" not in _cmd(md, tmp_path / "o.docx")


def test_a_missing_bib_does_not_reach_pandoc(md, tmp_path):
    """A stale path must not become a pandoc error — the caller may legitimately
    have no bibliography yet."""
    cmd = _cmd(md, tmp_path / "o.docx", bib_path=tmp_path / "absent.bib")
    assert "--citeproc" not in cmd


def test_suppress_bibliography_only_with_a_bib(md, bib, tmp_path):
    """rabbitHole writes its own annotated bibliography; citeproc must not append
    a second reference list."""
    cmd = _cmd(md, tmp_path / "o.docx", bib_path=bib, suppress_bibliography=True)
    assert "suppress-bibliography=true" in cmd
    # nothing to suppress when citeproc is not running
    bare = _cmd(md, tmp_path / "o.docx", suppress_bibliography=True)
    assert "suppress-bibliography=true" not in bare


@pytest.mark.skipif(not render.check_pandoc(), reason="pandoc not installed")
def test_citeproc_resolves_the_key_for_real(md, bib, tmp_path):
    """End to end: the reader must never see [@knuth1984]."""
    import re
    import zipfile

    dst = tmp_path / "out.docx"
    assert render.pandoc_convert(md, dst, bib_path=bib, suppress_bibliography=True)
    body = zipfile.ZipFile(dst).read("word/document.xml").decode()
    text = re.sub(r"<[^>]+>", "", body)
    assert "[@knuth1984]" not in text
    assert "Knuth" in text and "1984" in text


@pytest.mark.skipif(not render.check_pandoc(), reason="pandoc not installed")
def test_without_a_bib_the_raw_key_survives(md, tmp_path):
    """What rabbitHole relies on: no bibliography, no citeproc, and the [@citekey] reaches
    the reader intact — which is what the author wants (it names the exact bib entry)."""
    import re
    import zipfile

    dst = tmp_path / "bare.docx"
    assert render.pandoc_convert(md, dst)
    text = re.sub(r"<[^>]+>", "", zipfile.ZipFile(dst).read("word/document.xml").decode())
    assert "[@knuth1984]" in text

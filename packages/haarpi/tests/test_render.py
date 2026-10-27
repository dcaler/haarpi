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


def test_a_citekey_pandoc_cannot_read_is_renamed_for_the_render_only(tmp_path):
    """Zotero spelled O'Sullivan with a prime (U+2032), the key inherited it, and pandoc
    refused the whole bibliography: the DigiPros one-pager rendered no .docx at all."""
    bib = tmp_path / "refs.bib"
    bib.write_text("@article{o′sullivanSchellings2009,\n  title={Schelling′s model},\n"
                   "  author={O′Sullivan, Arthur},\n  year={2009}\n}\n"
                   "@article{knuth1984,\n  title={LP},\n  year={1984}\n}\n", encoding="utf-8")
    md = tmp_path / "d.md"
    md.write_text("As shown [@o′sullivanSchellings2009; @knuth1984].\n", encoding="utf-8")
    src, bib2, scratch = render._pandoc_ready(md, bib)
    assert "@article{osullivanSchellings2009," in bib2.read_text(encoding="utf-8")
    assert "Schelling′s model" in bib2.read_text(encoding="utf-8")   # only the key changes
    assert src.read_text(encoding="utf-8") == "As shown [@osullivanSchellings2009; @knuth1984].\n"
    assert "o′sullivan" in bib.read_text(encoding="utf-8")            # originals untouched
    assert "o′sullivan" in md.read_text(encoding="utf-8")
    assert set(scratch) == {src, bib2}


def test_a_clean_bibliography_is_handed_over_as_is(md, bib):
    assert render._pandoc_ready(md, bib) == (md, bib, [])


def test_a_repaired_key_never_collides_with_an_existing_one():
    keys = render._pandoc_safe_keys("@article{ab,\n}\n@article{a′b,\n}\n")
    assert keys == {"a′b": "ab2"}


@pytest.mark.skipif(not render.check_pandoc(), reason="pandoc not installed")
def test_pandoc_renders_past_a_bad_key_and_cleans_up(tmp_path):
    bib = tmp_path / "refs.bib"
    bib.write_text("@article{o′sullivan2009,\n  title={T},\n  author={O, A},\n"
                   "  year={2009},\n  journal={J}\n}\n", encoding="utf-8")
    md = tmp_path / "d.md"
    md.write_text("# T\n\nClaim [@o′sullivan2009].\n", encoding="utf-8")
    assert render.pandoc_convert(md, tmp_path / "d.docx", bib_path=bib)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["d.docx", "d.md", "refs.bib"]

"""Heading numbers come from the STYLE, never from digits in the text.

A "2.1" typed into a heading is a number the author renumbers by hand every time a section
moves — and it is the number a drafting model reads as a contract, which is how an outline
running 1.1, 1.3 got a §1.2 invented to fill the gap. Word's outline numbering has neither
problem.
"""

from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path

import pytest

from raconteur import refdoc

pytestmark = pytest.mark.skipif(shutil.which("pandoc") is None,
                                reason="pandoc not installed")


def _styles(docx: Path) -> str:
    with zipfile.ZipFile(docx) as z:
        return z.read("word/styles.xml").decode()


def _numbering(docx: Path) -> str:
    with zipfile.ZipFile(docx) as z:
        return z.read("word/numbering.xml").decode()


def _numpr(style_xml: str, style_id: str) -> str:
    m = re.search(rf'<w:style [^>]*w:styleId="{style_id}".*?</w:style>', style_xml, re.S)
    if not m:
        return ""
    got = re.search(r"<w:numPr>.*?</w:numPr>", m.group(0), re.S)
    return got.group(0) if got else ""


def test_the_heading_styles_carry_the_numbering(tmp_path):
    ref = refdoc.build(tmp_path / "ref.docx")
    assert ref is not None
    st = _styles(ref)
    # sections are Heading 2 (the paper's title occupies Heading 1)
    assert f'w:numId w:val="{refdoc.NUM_ID}"' in _numpr(st, "Heading2")
    assert 'w:ilvl w:val="0"' in _numpr(st, "Heading2")
    assert 'w:ilvl w:val="1"' in _numpr(st, "Heading3")
    assert 'w:ilvl w:val="2"' in _numpr(st, "Heading4")


def test_the_title_level_is_never_numbered(tmp_path):
    ref = refdoc.build(tmp_path / "ref.docx")
    assert _numpr(_styles(ref), "Heading1") == ""


def test_the_list_is_multilevel_and_dotted(tmp_path):
    ref = refdoc.build(tmp_path / "ref.docx")
    num = _numbering(ref)
    block = re.search(rf'<w:abstractNum w:abstractNumId="{refdoc.ABSTRACT_NUM_ID}".*?'
                      rf'</w:abstractNum>', num, re.S).group(0)
    assert 'w:multiLevelType w:val="multilevel"' in block
    assert 'w:lvlText w:val="%1"' in block
    assert 'w:lvlText w:val="%1.%2"' in block
    assert f'<w:num w:numId="{refdoc.NUM_ID}">' in num


def test_building_twice_does_not_duplicate_the_list(tmp_path):
    """numbering.xml with two abstractNums of one id is a part Word rejects outright, and a
    rejected part means a document that will not open."""
    dest = tmp_path / "ref.docx"
    refdoc.build(dest)
    once = _numbering(dest)
    refdoc.build(dest)
    assert _numbering(dest).count(
        f'w:abstractNumId="{refdoc.ABSTRACT_NUM_ID}"') == once.count(
        f'w:abstractNumId="{refdoc.ABSTRACT_NUM_ID}"')


def test_the_abstract_and_furniture_are_not_numbered(tmp_path):
    """They sit at the same heading level as a numbered section and pandoc gives them the
    same style, so the style alone cannot tell them apart."""
    from docx import Document
    from docx.oxml.ns import qn
    from haarpi.render import to_docx

    md = tmp_path / "s.md"
    md.write_text("# A Title\n\n## Abstract\n\n## Introduction\n\n### The Model\n\n"
                  "## Acknowledgements\n\n## References\n")
    out = to_docx(md, reference_doc=refdoc.build(tmp_path / "ref.docx"))
    assert refdoc.unnumber_furniture(out) == 3

    suppressed, inherited = set(), set()
    for p in Document(str(out)).paragraphs:
        if not p.text.strip():
            continue
        pPr = p._p.find(qn("w:pPr"))
        npr = pPr.find(qn("w:numPr")) if pPr is not None else None
        if npr is not None and npr.find(qn("w:numId")).get(qn("w:val")) == "0":
            suppressed.add(p.text.strip())
        elif (p.style.name or "").startswith("Heading") and p.style.name != "Heading 1":
            inherited.add(p.text.strip())
    assert suppressed == {"Abstract", "Acknowledgements", "References"}
    # the real sections carry NO paragraph-level numbering — they take it from the style
    assert inherited == {"Introduction", "The Model"}


def test_a_hand_edited_reference_doc_is_never_overwritten(tmp_path):
    """A project may carry a venue's house style. Rebuilding over it would silently discard
    the author's formatting."""
    (tmp_path / "paper").mkdir()
    theirs = tmp_path / "paper" / "reference.docx"
    theirs.write_bytes(b"not really a docx, but it is theirs")
    assert refdoc.reference_for(tmp_path) == theirs
    assert theirs.read_bytes() == b"not really a docx, but it is theirs"

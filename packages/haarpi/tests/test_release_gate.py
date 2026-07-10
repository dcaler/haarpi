"""The mechanical gate + release minting, against real OOXML.

Documents are built with python-docx (comments via Document.add_comment,
tracked changes via the engine's own tracked_replace), and resolved flags are
injected into commentsExtended.xml the way Word writes them."""

import zipfile

import pytest
from docx import Document
from docx.oxml.ns import qn
from lxml import etree

from haarpi import redline

W14 = "http://schemas.microsoft.com/office/word/2010/wordml"
W15 = "http://schemas.microsoft.com/office/word/2012/wordml"


def _make_markup(path, comment_author="DCR", resolved=False, tracked=False):
    """A doc with one paragraph, one comment, optionally a tool tracked change,
    optionally the comment marked done in commentsExtended."""
    doc = Document()
    p = doc.add_paragraph("One sentence. Two sentence.")
    doc.add_comment(runs=[p.runs[0]], text="more on X", author=comment_author)
    if tracked:
        ids = redline.ids_for(doc)
        redline.tracked_replace(p._p, "One rewritten. Two sentence.", "rabbitHole", ids)
    doc.save(str(path))

    # give the comment paragraphs w14:paraId + write commentsExtended, as Word does
    with zipfile.ZipFile(path) as z:
        parts = {n: z.read(n) for n in z.namelist()}
    croot = etree.fromstring(parts["word/comments.xml"])
    pids = []
    for i, cp in enumerate(croot.iter(qn("w:p"))):
        pid = f"0000000{i+1}"
        cp.set(f"{{{W14}}}paraId", pid)
        pids.append(pid)
    parts["word/comments.xml"] = etree.tostring(croot, xml_declaration=True,
                                                encoding="UTF-8", standalone=True)
    ce = etree.Element(f"{{{W15}}}commentsEx", nsmap={"w15": W15})
    for pid in pids:
        cex = etree.SubElement(ce, f"{{{W15}}}commentEx")
        cex.set(f"{{{W15}}}paraId", pid)
        cex.set(f"{{{W15}}}done", "1" if resolved else "0")
    parts["word/commentsExtended.xml"] = etree.tostring(
        ce, xml_declaration=True, encoding="UTF-8", standalone=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for n, b in parts.items():
            z.writestr(n, b)
    return path


def test_unresolved_comment_blocks_the_gate(tmp_path):
    p = _make_markup(tmp_path / "m.docx", resolved=False)
    check = redline.gate_check(p)
    assert not check["clean"]
    assert check["unresolved"][0]["text"] == "more on X"
    assert check["unresolved"][0]["author"] == "DCR"


def test_resolved_comment_passes_the_gate(tmp_path):
    p = _make_markup(tmp_path / "m.docx", resolved=True)
    assert redline.gate_check(p)["clean"]


def test_tool_comments_never_block(tmp_path):
    p = _make_markup(tmp_path / "m.docx", comment_author="rabbitHole", resolved=False)
    assert redline.gate_check(p)["clean"]


def test_reviewer_tracked_change_blocks_even_without_comments(tmp_path):
    doc = Document()
    para = doc.add_paragraph("Original text here.")
    ids = redline.ids_for(doc)
    redline.tracked_replace(para._p, "Reviewer rewrote this.", "DCR", ids)
    doc.save(str(tmp_path / "m.docx"))
    check = redline.gate_check(tmp_path / "m.docx")
    assert not check["clean"] and check["reviewer_changes"] > 0


def test_tool_tracked_changes_do_not_block(tmp_path):
    p = _make_markup(tmp_path / "m.docx", resolved=True, tracked=True)
    assert redline.gate_check(p)["clean"]


def test_mint_release_accepts_strips_and_writes_md(tmp_path):
    src = _make_markup(tmp_path / "m.docx", resolved=True, tracked=True)
    dst = tmp_path / "out" / "260715_myproj_litreview.docx"
    result = redline.mint_release(src, dst)
    assert dst.exists() and result["md"].exists()

    out = Document(str(dst))
    text = "\n".join(par.text for par in out.paragraphs)
    assert "One rewritten." in text            # insertion accepted
    assert "One sentence." not in text         # deletion applied
    body = out.element.body
    assert not list(body.iter(qn("w:ins"))) and not list(body.iter(qn("w:del")))
    assert not list(body.iter(qn("w:commentRangeStart")))
    assert "One rewritten." in result["md"].read_text()


def test_minted_release_is_itself_gate_clean(tmp_path):
    src = _make_markup(tmp_path / "m.docx", resolved=True, tracked=True)
    dst = tmp_path / "260715_myproj_litreview.docx"
    redline.mint_release(src, dst, md_sibling=False)
    assert redline.gate_check(dst)["clean"]


def test_release_markdown_renders_headings(tmp_path):
    doc = Document()
    doc.add_heading("Results", level=2)
    doc.add_paragraph("Body text.")
    md = redline.release_markdown(doc)
    assert "## Results" in md and "Body text." in md

"""rabbitHole `graft` — add a section without disturbing the rest.

The contract these pin: existing paragraphs come through BYTE-IDENTICAL, the reviewer's comment
threads survive, and the new strand arrives as a tracked insertion at a position chosen from the
comment's own anchor first.

Runnable two ways:
    pytest tests/test_graft.py
    python tests/test_graft.py
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rabbithole import graft, summarize
from rabbithole.summarize import Section

docx = pytest.importorskip("docx")


# ── the round-trip that makes a graft possible ───────────────────────────────

def test_sections_round_trip_through_markdown():
    secs = [Section("First idea", "", text="Para one [@a1].\n\nPara two [@b2]."),
            Section("Second idea", "", text="Only para [@c3].")]
    md = summarize._assemble(secs)
    back = summarize.sections_from_markdown(md)
    assert [s.heading for s in back] == ["First idea", "Second idea"]
    assert [s.text for s in back] == [s.text for s in secs]
    assert summarize._assemble(back) == md, "the inverse must be exact, or a graft loses prose"


def test_sections_from_markdown_drops_the_bibliography_and_the_wrapper():
    md = ("## Narrative Review\n\n## Real section\n\nBody [@a1].\n\n"
          "## Annotated Bibliography\n\n**Some Author (2020).**\n\n- a claim")
    secs = summarize.sections_from_markdown(md)
    assert [s.heading for s in secs] == ["Real section"]


def test_sections_from_markdown_drops_a_heading_with_no_body():
    md = "## Empty one\n\n## Real one\n\nBody [@a1]."
    assert [s.heading for s in summarize.sections_from_markdown(md)] == ["Real one"]


# ── where it goes ────────────────────────────────────────────────────────────

class _Brain:
    def __init__(self, vectors=None):
        self._v = vectors or {}

    def embed_batch(self, texts):
        return [self._v.get(t, [0.0, 0.0, 1.0]) for t in texts]


def _existing():
    return [Section("Alpha", "about alpha", text="a"),
            Section("Beta", "about beta", text="b"),
            Section("Gamma", "about gamma", text="c")]


def test_the_comments_anchor_wins():
    """Where the reviewer wrote is a statement about where the ask belongs."""
    at, why = graft.choose_position(_Brain(), _existing(), Section("New", "n"), anchor=1)
    assert at == 1 and why == "the comment's own anchor"


def test_an_anchor_above_the_first_section_opens_the_review():
    at, why = graft.choose_position(_Brain(), _existing(), Section("New", "n"), anchor=-1)
    assert at == -1 and "anchor" in why


def test_without_an_anchor_it_falls_back_to_the_nearest_section():
    vecs = {"Alpha. about alpha": [1.0, 0.0, 0.0],
            "Beta. about beta": [0.0, 1.0, 0.0],
            "Gamma. about gamma": [0.0, 0.0, 1.0],
            "New. about beta too": [0.0, 1.0, 0.0]}
    at, why = graft.choose_position(_Brain(vecs), _existing(),
                                    Section("New", "about beta too"), anchor=None)
    assert at == 1 and why == "nearest existing section"


def test_appending_at_the_end_is_never_silent():
    """A tacked-on section breaks the through line, so the last resort has to be reported."""
    class _Dead:
        def embed_batch(self, texts):
            raise RuntimeError("no embeddings")
    at, why = graft.choose_position(_Dead(), _existing(), Section("New", "n"), anchor=None)
    assert at == 2 and "NO position signal" in why


def test_an_out_of_range_anchor_is_not_trusted():
    at, why = graft.choose_position(_Brain(), _existing(), Section("New", "n"), anchor=99)
    assert why != "the comment's own anchor"


# ── the insertion itself ─────────────────────────────────────────────────────

def _doc_with_sections(path: Path, headings: list[str]) -> Path:
    d = docx.Document()
    for h in headings:
        d.add_heading(h, level=2)
        d.add_paragraph(f"Body of {h} [@k1].")
    d.save(str(path))
    return path


def _paragraph_texts(path: Path) -> list[str]:
    """Paragraph prose with insertions read as accepted.

    `python-docx`'s own `paragraph.text` walks only direct `w:r` children, so a paragraph whose
    runs sit inside a `w:ins` reads as empty — invisible exactly where a graft puts its work.
    """
    from haarpi.redline import flatten_paragraph
    return [flatten_paragraph(p._p) for p in docx.Document(str(path)).paragraphs]


def test_insertion_leaves_every_existing_paragraph_byte_identical(tmp_path):
    """The whole contract. If this fails the reviewer is re-reading the document."""
    src = _doc_with_sections(tmp_path / "d.docx", ["Alpha", "Beta", "Gamma"])
    before = _paragraph_texts(src)
    sec = Section("Inserted", "c", text="First new para [@k1].\n\nSecond new para [@k1].")
    assert graft._insert_section(src, 0, sec, "rabbitHole")
    after = _paragraph_texts(src)
    for original in before:
        assert original in after, f"lost or altered: {original!r}"
    assert len(after) == len(before) + 3          # heading + two paragraphs


def test_the_new_section_lands_after_the_anchored_one(tmp_path):
    src = _doc_with_sections(tmp_path / "d.docx", ["Alpha", "Beta", "Gamma"])
    sec = Section("Inserted", "c", text="New para [@k1].")
    graft._insert_section(src, 0, sec, "rabbitHole")     # after section 0 (Alpha)
    texts = _paragraph_texts(src)
    assert texts.index("Inserted") > texts.index("Body of Alpha [@k1].")
    assert texts.index("Inserted") < texts.index("Beta")


def test_the_new_section_is_a_tracked_insertion_by_the_tool(tmp_path):
    """It arrives as a change the reviewer can accept or reject, not as settled prose."""
    import re
    src = _doc_with_sections(tmp_path / "d.docx", ["Alpha", "Beta"])
    graft._insert_section(src, 0, Section("Inserted", "c", text="New para [@k1]."),
                          "rabbitHole")
    import zipfile
    xml = zipfile.ZipFile(str(src)).read("word/document.xml").decode()
    assert "<w:ins " in xml
    assert re.search(r'<w:ins [^>]*w:author="rabbitHole"', xml)


def test_a_graft_past_the_last_section_appends(tmp_path):
    src = _doc_with_sections(tmp_path / "d.docx", ["Alpha", "Beta"])
    sec = Section("Tail", "c", text="Appended para [@k1].")
    assert graft._insert_section(src, 1, sec, "rabbitHole")   # after the LAST section
    texts = _paragraph_texts(src)
    assert texts.index("Tail") > texts.index("Body of Beta [@k1].")


def test_a_section_with_no_prose_is_not_inserted(tmp_path):
    src = _doc_with_sections(tmp_path / "d.docx", ["Alpha"])
    before = _paragraph_texts(src)
    assert not graft._insert_section(src, 0, Section("Empty", "c", text="  "), "rabbitHole")
    assert _paragraph_texts(src) == before


# ── comments survive, and carry their anchor ─────────────────────────────────

def test_a_comment_reports_the_section_it_sits_in(tmp_path):
    """`read_comments` used to return {author, text} and drop the position on the floor."""
    from rabbithole import docxio
    d = docx.Document()
    d.add_heading("Alpha", level=2)
    d.add_paragraph("Body of Alpha.")
    d.add_heading("Beta", level=2)
    p = d.add_paragraph("Body of Beta.")
    fp = tmp_path / "c.docx"
    d.save(str(fp))
    try:
        doc2 = docx.Document(str(fp))
        target = [q for q in doc2.paragraphs if q.text == "Body of Beta."][0]
        doc2.add_comment(runs=target.runs, text="add a section on supply chains",
                         author="D. Cale Reeves")
        doc2.save(str(fp))
    except (AttributeError, TypeError):
        pytest.skip("python-docx build has no comment API")
    got = docxio.read_comments(fp)
    assert got and got[0]["text"] == "add a section on supply chains"
    assert got[0]["section"] == 1, "the comment sits in the second section"


def test_the_document_title_is_not_counted_as_a_section(tmp_path):
    """The anchor index must line up with `sections_from_markdown`, which starts at the first
    `## `. Counting the H1 title shifted every anchor by one — two off-by-ones that happened to
    cancel in the .docx, which is luck rather than correctness."""
    from rabbithole import docxio
    d = docx.Document()
    d.add_heading("Literature Review: a topic", level=1)     # what build_markdown writes as `# `
    d.add_heading("Alpha", level=2)
    d.add_paragraph("Body of Alpha.")
    d.add_heading("Beta", level=2)
    d.add_paragraph("Body of Beta.")
    fp = tmp_path / "titled.docx"
    d.save(str(fp))
    try:
        doc2 = docx.Document(str(fp))
        target = [q for q in doc2.paragraphs if q.text == "Body of Beta."][0]
        doc2.add_comment(runs=target.runs, text="add a section here", author="R")
        doc2.save(str(fp))
    except (AttributeError, TypeError):
        pytest.skip("python-docx build has no comment API")
    assert docxio.read_comments(fp)[0]["section"] == 1, "Beta is section 1, not 2"


def test_insertion_indexes_sections_the_same_way_the_anchor_does(tmp_path):
    """One index space, or the graft lands in the wrong place."""
    d = docx.Document()
    d.add_heading("Title", level=1)
    for h in ("Alpha", "Beta", "Gamma"):
        d.add_heading(h, level=2)
        d.add_paragraph(f"Body of {h}.")
    fp = tmp_path / "t.docx"
    d.save(str(fp))
    graft._insert_section(fp, 0, Section("New", "c", text="New prose."), "rabbitHole")
    texts = _paragraph_texts(fp)
    assert texts.index("New") > texts.index("Body of Alpha.")
    assert texts.index("New") < texts.index("Beta"), "section 0 is Alpha, not the title"

"""The deck stage's gate: PowerPoint modern comments are the review surface (a .pptx has no tracked
changes), and the gate reuses the redline procedure — clean ⟺ every comment resolved."""

from __future__ import annotations

import zipfile
from pathlib import Path

from haarpi import naming, planner, project, redline

_P188 = "http://schemas.microsoft.com/office/powerpoint/2018/8/main"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"


def _cm(cid: str, text: str, *, resolved: bool = False) -> str:
    status = ' status="resolved" complete="100000"' if resolved else ""
    return (f'<p188:cm id="{cid}" authorId="A1"{status}>'
            f'<p188:txBody><a:bodyPr/><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p188:txBody>'
            f'</p188:cm>')


def _write_deck_pptx(path: Path, comments: list[str], *, slide_assoc: bool = True) -> None:
    """A minimal .pptx-shaped zip carrying only the modern-comment parts the reader needs."""
    authors = (f'<p188:authorLst xmlns:p188="{_P188}">'
               f'<p188:author id="A1" name="D. Cale Reeves" initials="DCR"/></p188:authorLst>')
    cmlst = (f'<p188:cmLst xmlns:p188="{_P188}" xmlns:a="{_A}">' + "".join(comments) + "</p188:cmLst>")
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("ppt/authors.xml", authors)
        z.writestr("ppt/comments/modernComment_1_0.xml", cmlst)
        if slide_assoc:
            z.writestr("ppt/slides/_rels/slide1.xml.rels",
                       '<Relationships><Relationship Id="rId1" '
                       'Target="../comments/modernComment_1_0.xml"/></Relationships>')


def test_reader_reads_text_author_resolved_and_slide(tmp_path):
    p = tmp_path / "d.pptx"
    _write_deck_pptx(p, [_cm("C1", "fix this"), _cm("C2", "ok now", resolved=True)])
    threads = redline.pptx_comment_threads(p)
    assert [t["text"] for t in threads] == ["fix this", "ok now"]
    assert all(t["author"] == "D. Cale Reeves" for t in threads)
    assert [t["resolved"] for t in threads] == [False, True]
    assert all(t["slide"] == "slide1" for t in threads)   # associated via the slide rels


def test_gate_blocks_on_any_open_comment(tmp_path):
    p = tmp_path / "d.pptx"
    _write_deck_pptx(p, [_cm("C1", "only citations here"), _cm("C2", "legacy bits?", resolved=True)])
    check = redline.gate_check(p)
    assert not check["clean"]
    assert check["reviewer_changes"] == 0                  # a .pptx has no tracked changes
    assert [c["text"] for c in check["unresolved"]] == ["only citations here"]


def test_gate_clean_when_all_resolved(tmp_path):
    p = tmp_path / "d.pptx"
    _write_deck_pptx(p, [_cm("C1", "a", resolved=True), _cm("C2", "b", resolved=True)])
    assert redline.gate_check(p)["clean"]


def _deck_project(tmp_path) -> project.Manifest:
    m = project.Manifest(name="demo", short_title="demo", brief="x")
    project.save_manifest(m, tmp_path)
    (tmp_path / "slides" / "shorttalk").mkdir(parents=True)
    return m


def test_find_finished_markup_surfaces_a_commented_deck(tmp_path):
    m = _deck_project(tmp_path)
    deck = tmp_path / "slides" / "shorttalk" / naming.major_name("demo", "pptx", infix="deck")
    _write_deck_pptx(deck, [_cm("C1", "reword the title")])
    found = planner.find_finished_markup(tmp_path, m)
    assert found is not None and found[0] == "deck" and found[1] == deck


def test_find_finished_markup_ignores_an_uncommented_draft(tmp_path):
    m = _deck_project(tmp_path)
    deck = tmp_path / "slides" / "shorttalk" / naming.major_name("demo", "pptx", infix="deck")
    _write_deck_pptx(deck, [])                              # a tool draft nobody has reviewed
    assert planner.find_finished_markup(tmp_path, m) is None


def test_find_finished_markup_ignores_a_release(tmp_path):
    m = _deck_project(tmp_path)
    rel = tmp_path / "slides" / "shorttalk" / naming.release_name("demo", "pptx", infix="deck")
    _write_deck_pptx(rel, [_cm("C1", "x", resolved=True)])  # bare chain == a release, never markup
    assert planner.find_finished_markup(tmp_path, m) is None

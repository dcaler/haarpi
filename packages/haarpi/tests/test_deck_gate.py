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


def test_render_pptx_pdf_skips_when_no_libreoffice(tmp_path, monkeypatch):
    """A missing converter never blocks the mint — the PDF twin is best-effort."""
    monkeypatch.setattr(planner.shutil, "which", lambda *_: None)
    assert planner._render_pptx_pdf(tmp_path / "d.pptx") is None


def test_render_pptx_pdf_returns_the_pdf_on_success(tmp_path, monkeypatch):
    """When LibreOffice is present, the PDF twin lands beside the .pptx with the same stem."""
    pptx = tmp_path / "260710_x_deck.pptx"
    pptx.write_bytes(b"x")
    monkeypatch.setattr(planner.shutil, "which", lambda *_: "/usr/bin/soffice")

    def fake_run(cmd, **kw):
        pptx.with_suffix(".pdf").write_bytes(b"%PDF-1.4")   # emulate soffice --convert-to pdf
        return None

    monkeypatch.setattr(planner.subprocess, "run", fake_run)
    out = planner._render_pptx_pdf(pptx)
    assert out == pptx.with_suffix(".pdf") and out.is_file()


# ── razzle's illustration note: a tool's comment holds the mint but is not the author's review ──

def _write_deck_by(path: Path, comments: list[tuple[str, str, bool]]) -> None:
    """[(author, text, resolved)] — authors as they would be signed in PowerPoint."""
    names = sorted({a for a, _, _ in comments})
    ids = {a: f"A{i}" for i, a in enumerate(names)}
    authors = (f'<p188:authorLst xmlns:p188="{_P188}">'
               + "".join(f'<p188:author id="{ids[a]}" name="{a}" initials="x"/>' for a in names)
               + "</p188:authorLst>")
    cms = "".join(_cm(f"C{i}", t, resolved=r).replace('authorId="A1"', f'authorId="{ids[a]}"')
                  for i, (a, t, r) in enumerate(comments))
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("ppt/authors.xml", authors)
        z.writestr("ppt/comments/modernComment_1_0.xml",
                   f'<p188:cmLst xmlns:p188="{_P188}" xmlns:a="{_A}">{cms}</p188:cmLst>')
        z.writestr("ppt/slides/_rels/slide1.xml.rels",
                   '<Relationships><Relationship Id="rId1" '
                   'Target="../comments/modernComment_1_0.xml"/></Relationships>')


def _deck(tmp_path):
    m = _deck_project(tmp_path)
    return m, tmp_path / "slides" / "shorttalk" / naming.major_name("demo", "pptx", infix="deck")


def _brief_and_pick(deck_dir: Path, *, placed: bool = False) -> None:
    from razzle import illustrate
    spec = [{"role": "title", "title": "T"},
            {"role": "content", "title": "C", "illustration": "a lighthouse in fog",
             "illustration_kind": "pictorial"}]
    illustrate.prepare(deck_dir, spec)
    key = illustrate.briefs(spec)[0]["key"]
    (illustrate.home(deck_dir) / key).mkdir(parents=True, exist_ok=True)
    (illustrate.home(deck_dir) / key / illustrate.CHOSEN).write_bytes(b"png")
    if placed:
        ill = illustrate.prepare(deck_dir, spec)
        illustrate.record_placed(deck_dir, ill["placements"], ill["briefs"])


def test_razzles_open_note_alone_is_not_the_author_having_reviewed(tmp_path):
    m, deck = _deck(tmp_path)
    _write_deck_by(deck, [("razzle", "Illustrations pending.", False)])
    assert planner.find_finished_markup(tmp_path, m) is None


def test_resolving_razzles_note_is_the_authors_act(tmp_path):
    m, deck = _deck(tmp_path)
    _write_deck_by(deck, [("razzle", "Illustrations pending.", True)])
    assert planner.find_finished_markup(tmp_path, m) == ("deck", deck)


def test_a_pick_not_yet_on_the_deck_is_the_authors_act(tmp_path):
    m, deck = _deck(tmp_path)
    _write_deck_by(deck, [("razzle", "Illustrations pending.", False)])
    _brief_and_pick(deck.parent)
    assert planner.find_finished_markup(tmp_path, m) == ("deck", deck)


def test_a_pick_already_on_the_deck_is_not(tmp_path):
    m, deck = _deck(tmp_path)
    _write_deck_by(deck, [("razzle", "Illustrations pending.", False)])
    _brief_and_pick(deck.parent, placed=True)
    assert planner.find_finished_markup(tmp_path, m) is None


def _route(tmp_path, monkeypatch, m, deck):
    queued = []
    monkeypatch.setattr(planner.trundlr, "TrundlrClient", lambda *a, **k: object())
    monkeypatch.setattr(planner, "queue_chain",
                        lambda client, pid, stage, steps, tr_cfg, **kw:
                        queued.append((stage, steps, kw.get("venue"))) or
                        {"tasks": [{"title": s} for s in steps]})
    out = planner._route_deck(tmp_path, m, deck, "shorttalk", redline.gate_check(deck),
                              {}, True, False, "")
    return out, queued


def test_the_authors_comments_are_what_the_classifier_reads(tmp_path, monkeypatch):
    m, deck = _deck(tmp_path)
    _write_deck_by(deck, [("razzle", "Illustrations pending.", False),
                          ("D. Cale Reeves", "cut slide 4", False)])
    out, queued = _route(tmp_path, monkeypatch, m, deck)
    assert [c["text"] for c in out["unresolved"]] == ["cut slide 4"]
    assert not out["clean"] and queued == []


def test_picks_not_on_the_deck_re_render_it_rather_than_mint(tmp_path, monkeypatch):
    m, deck = _deck(tmp_path)
    _write_deck_by(deck, [("razzle", "Illustrations pending.", True)])   # even resolved
    _brief_and_pick(deck.parent)
    out, queued = _route(tmp_path, monkeypatch, m, deck)
    assert out is None
    assert queued == [("deck", ["place", "comment"], "shorttalk")]


def test_only_razzles_note_open_hands_the_deck_back_to_the_author(tmp_path, monkeypatch):
    m, deck = _deck(tmp_path)
    _write_deck_by(deck, [("razzle", "Illustrations pending.", False),
                          ("D. Cale Reeves", "fine", True)])
    out, queued = _route(tmp_path, monkeypatch, m, deck)
    assert out is None
    assert queued == [("deck", ["comment"], "shorttalk")]


def test_nothing_open_and_nothing_to_place_goes_to_the_gate(tmp_path, monkeypatch):
    m, deck = _deck(tmp_path)
    _write_deck_by(deck, [("razzle", "Illustrations pending.", True)])
    _brief_and_pick(deck.parent, placed=True)
    out, queued = _route(tmp_path, monkeypatch, m, deck)
    assert out["clean"] and queued == []


def test_the_deck_chain_draws_before_the_author_reviews():
    assert planner.STAGE_STEPS["deck"]["illustrate"].resource == "gpu"
    assert planner.STAGE_STEPS["deck"]["place"].command == "haarpi razzle render"
    assert planner.STAGE_TIERS["deck"]["revise"] == ["deck_session", "illustrate", "comment"]
    assert planner._venued("haarpi razzle illustrate", "css2026").endswith("--venue css2026")
    assert planner._venued("haarpi razzle render", "css2026").endswith("--venue css2026")

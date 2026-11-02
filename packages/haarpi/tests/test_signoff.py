"""Signing off an interview's document in the terminal, after the Claude session exits.

haarpi asks, never Claude; only `yes` or `no` count; a yes is keyed on the file's content, so
the gate accepts that `_ra` file as finished markup and a re-render under the same name is not.
"""

from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path

from haarpi import planner, project, signoff


def _project(tmp_path: Path) -> project.Manifest:
    m = project.Manifest(name="Schelling Chords", short_title="Chords")
    project.save_manifest(m, tmp_path)
    (tmp_path / "paper").mkdir(exist_ok=True)
    return m


def _docx(path: Path, headings: list[tuple[int, str]] = ()) -> Path:
    body = "".join(f'<w:p><w:pPr><w:pStyle w:val="Heading{lvl}"/></w:pPr>'
                   f'<w:r><w:t>{text}</w:t></w:r></w:p>' for lvl, text in headings)
    body += "<w:p><w:r><w:t>Body text.</w:t></w:r></w:p>"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", f"<w:document><w:body>{body}</w:body></w:document>")
    return path


def _answers(*replies):
    it = iter(replies)

    def prompt(_q):
        try:
            return next(it)
        except StopIteration:
            raise EOFError
    return prompt


def test_only_exact_yes_or_no_counts_and_anything_else_is_asked_again(capsys):
    assert signoff.ask(_answers("y", "Yes", "YES", "ok", "yes")) is True
    assert capsys.readouterr().out.count("Please type yes or no") == 4
    assert signoff.ask(_answers("nope", "no")) is False
    assert signoff.ask(_answers("maybe")) is False          # end of input is a no


def test_a_signoff_is_keyed_on_content_not_the_name(tmp_path):
    _project(tmp_path)
    doc = _docx(tmp_path / "paper" / "260718_Chords_scope_ra.docx", [(1, "Scope")])
    assert not signoff.signed_off(tmp_path, doc)
    signoff.record(tmp_path, doc, "design")
    assert signoff.signed_off(tmp_path, doc)
    _docx(doc, [(1, "Scope, re-rendered")])
    assert not signoff.signed_off(tmp_path, doc)


def test_the_gate_accepts_a_signed_off_tool_draft(tmp_path):
    m = _project(tmp_path)
    doc = _docx(tmp_path / "paper" / "260718_Chords_css2026_ra.docx")
    assert planner.find_finished_markup(tmp_path, m) is None
    signoff.record(tmp_path, doc, "paper")
    found = planner.find_finished_markup(tmp_path, m)
    assert found is not None and found[1] == doc


def test_the_outline_reads_headings_by_level(tmp_path):
    doc = _docx(tmp_path / "x.docx", [(1, "Intro"), (2, "Aim"), (1, "Methods")])
    assert signoff.outline(doc) == ["Intro", "  Aim", "Methods"]
    assert signoff.outline(tmp_path / "missing.docx") == []


def _with_draft(tmp_path: Path) -> Path:
    _project(tmp_path)
    doc = _docx(tmp_path / "paper" / "260718_Chords_css2026_ra.docx", [(1, "Intro")])
    os.utime(doc, (2000, 2000))
    return doc


def test_no_terminal_means_no_question_and_the_normal_review(tmp_path):
    doc = _with_draft(tmp_path)
    asked = []
    assert not signoff.after_session(tmp_path, "paper", 1000, interactive=False,
                                     prompt=lambda q: asked.append(q) or "yes")
    assert asked == [] and not signoff.signed_off(tmp_path, doc)


def test_a_yes_after_the_session_records_the_signoff(tmp_path, capsys):
    doc = _with_draft(tmp_path)
    assert signoff.after_session(tmp_path, "paper", 1000, interactive=True,
                                 prompt=_answers("yes"))
    assert signoff.signed_off(tmp_path, doc)
    out = capsys.readouterr().out
    assert "Intro" in out and "Signed off" in out


def test_a_draft_older_than_the_session_is_not_offered(tmp_path):
    _with_draft(tmp_path)
    assert not signoff.after_session(tmp_path, "paper", 3000, interactive=True,
                                     prompt=_answers("yes"))


def test_a_failure_never_fails_the_session(tmp_path):
    assert signoff.after_session(tmp_path / "not-a-project", "paper", 0,
                                 interactive=True, prompt=_answers("yes")) is False

"""Every .docx this pipeline renders opens with revision recording on.

Not a raconteur concern: rabbitHole's litreview drafts and candidate lists are redlined
too, and the redline contract is the same everywhere — a tracked insertion is an atom the
tool preserves and may never author. An author who forgets to switch tracking on loses that
protection silently, and the only defence left is freezing whole paragraphs.
"""

from __future__ import annotations

import shutil
import zipfile

import pytest

from haarpi.render import enable_track_changes, to_docx

pytestmark = pytest.mark.skipif(shutil.which("pandoc") is None,
                                reason="pandoc not installed")


def _settings(docx) -> str:
    with zipfile.ZipFile(docx) as z:
        return z.read("word/settings.xml").decode()


def _md(tmp_path):
    p = tmp_path / "d.md"
    p.write_text("# T\n\n## A section\n\nsome prose\n")
    return p


def test_a_rendered_document_records_revisions(tmp_path):
    assert "<w:trackRevisions/>" in _settings(to_docx(_md(tmp_path)))


def test_a_caller_that_does_not_want_it_can_say_so(tmp_path):
    """A submission is not a document anyone marks up. package.py builds its own with a
    separate pandoc invocation, but the opt-out exists rather than being assumed."""
    out = to_docx(_md(tmp_path), track_changes=False)
    assert "<w:trackRevisions/>" not in _settings(out)


def test_enabling_it_twice_does_not_duplicate_the_flag(tmp_path):
    out = to_docx(_md(tmp_path))
    assert enable_track_changes(out) is False
    assert _settings(out).count("<w:trackRevisions") == 1


def test_a_file_that_is_not_a_docx_is_refused_quietly(tmp_path):
    """Rendering already failed if we are here; raising on top of it loses the real error."""
    junk = tmp_path / "not.docx"
    junk.write_bytes(b"nope")
    assert enable_track_changes(junk) is False


def test_the_flag_is_the_element_word_actually_reads(tmp_path):
    """w:trackChanges is not an element of CT_Settings. Word ignored it in silence, so
    every document this pipeline rendered opened with recording OFF and every author
    switched it on by hand — while the check that read the flag back reported it on,
    because it was reading its own invention. Ground truth is Word's own settings.xml on a
    redlined skeleton: … stylePaneFormatFilter, trackRevisions, defaultTabStop …"""
    s = _settings(to_docx(_md(tmp_path)))
    assert "<w:trackRevisions/>" in s
    assert "<w:trackChanges" not in s


def test_the_flag_lands_in_schema_order(tmp_path):
    """CT_Settings is a sequence; the flag cannot simply go first. It belongs after
    stylePaneFormatFilter and before doNotTrackMoves / defaultTabStop."""
    import re
    s = _settings(to_docx(_md(tmp_path)))
    kids = re.findall(r"<w:([A-Za-z]+)[ />]", s.split("<w:settings", 1)[1])
    i = kids.index("trackRevisions")
    assert i > 0, "must not be the first child"
    following = {"doNotTrackMoves", "doNotTrackFormatting", "documentProtection",
                 "defaultTabStop"}
    assert following & set(kids[i + 1:]), "nothing that must follow it does"
    assert not (following & set(kids[:i])), "it sits after an element it must precede"


def test_a_word_saved_flag_is_not_duplicated():
    """Word writes trackRevisions itself. The old guard tested for trackChanges, so on a
    document Word had already flagged it would have inserted a second, bogus element."""
    from haarpi.render import _with_track_changes
    already = ('<w:settings xmlns:w="x"><w:zoom/><w:trackRevisions/>'
               '<w:defaultTabStop/></w:settings>')
    assert _with_track_changes(already) == already

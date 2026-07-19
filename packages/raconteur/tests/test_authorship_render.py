"""Authorship reaches the page from the manifest, not from prose.

The co-author's name used to exist in exactly one place: text typed into an annotated
.docx and carried forward by the redline. Every pre-annotation `_ra` outline in
SchellingChords has zero occurrences of it. A major revision regenerates that prose, so the
name survived by luck — and a re-think triggered by the collaborator's own arrival is
precisely the revision that would drop them.

It is now read at render time from haarpi.yaml, above every stage, by all three documents
that carry a title.
"""

from __future__ import annotations

import types
from pathlib import Path

from haarpi import project as hproject
from raconteur import guards, outline, paper
from raconteur.context import load_authors_block


def _manifest(tmp_path: Path, *authors) -> None:
    m = hproject.Manifest(name="Chords", short_title="Chords", authors=list(authors))
    hproject.save_manifest(m, tmp_path)


def _cfg(**venues):
    return types.SimpleNamespace(short_title="Chords", title="A Title", litrev_dir="",
                                 venue=lambda n: venues.get(n))


ONE = {"name": "D. Cale Reeves", "initials": "DCR", "affiliation": "Alpha",
       "email": "a@x.edu", "corresponding": True}
TWO = {"name": "J. Rodenberg", "initials": "JR", "affiliation": "Beta"}


# ── the shared reader ────────────────────────────────────────────────────────

def test_the_block_is_read_from_the_project_manifest(tmp_path):
    _manifest(tmp_path, ONE, TWO)
    block = load_authors_block(tmp_path)
    assert "D. Cale Reeves" in block and "J. Rodenberg" in block


def test_outside_a_haarpi_project_there_is_simply_no_block(tmp_path):
    """raconteur runs standalone. A paper written by a tool with no manifest has no
    recorded authors — and must not invent one."""
    assert load_authors_block(tmp_path) == ""


def test_a_malformed_manifest_does_not_cost_the_render(tmp_path):
    """Losing a finished draft to a YAML typo is a worse failure than an unnamed one."""
    (tmp_path / "haarpi.yaml").write_text("authors: [oh no: [\n")
    assert load_authors_block(tmp_path) == ""


# ── the draft ────────────────────────────────────────────────────────────────

def test_the_block_lands_under_the_title_not_above_it():
    out = paper._insert_authors("# A Title\n\n## 1. Intro\n\nprose\n", "Someone")
    assert out.splitlines()[0] == "# A Title"
    assert out.splitlines()[2] == "Someone"


def test_a_manuscript_with_no_title_still_gets_its_authors():
    assert paper._insert_authors("prose\n", "Someone").startswith("Someone")


def test_an_empty_block_changes_nothing():
    text = "# A Title\n\nprose\n"
    assert paper._insert_authors(text, "") == text


def test_the_draft_renders_the_authors(tmp_path, monkeypatch):
    _manifest(tmp_path, ONE)
    monkeypatch.setattr(paper, "to_docx", lambda *a, **k: None)
    (tmp_path / "paper").mkdir()
    paper._write(tmp_path, _cfg(), tmp_path / "paper", "# A Title\n\n## 1. Intro\n\nx\n")
    written = next((tmp_path / "paper").glob("*.md")).read_text()
    assert "D. Cale Reeves" in written and "Corresponding author" in written


def test_an_anonymized_venue_gets_no_authors_in_the_draft(tmp_path, monkeypatch):
    """A desk reject on a rule the CFP stated plainly — and the reason authorship is data."""
    _manifest(tmp_path, ONE)
    monkeypatch.setattr(paper, "to_docx", lambda *a, **k: None)
    (tmp_path / "paper").mkdir()
    blind = types.SimpleNamespace(anonymized=True)
    paper._write(tmp_path, _cfg(acm=blind), tmp_path / "paper",
                 "# A Title\n\n## 1. Intro\n\nx\n", venue="acm")
    written = next((tmp_path / "paper").glob("*.md")).read_text()
    assert "Reeves" not in written and "a@x.edu" not in written


# ── the outline ──────────────────────────────────────────────────────────────

def test_the_outline_renders_the_authors(tmp_path, monkeypatch):
    _manifest(tmp_path, ONE, TWO)
    monkeypatch.setattr(outline, "to_docx", lambda *a, **k: None)
    (tmp_path / "paper").mkdir()
    outline._write(tmp_path, _cfg(), tmp_path / "paper", "## 1. Intro\n- a beat\n")
    written = next((tmp_path / "paper").glob("*.md")).read_text()
    assert written.startswith("# A Title") and "J. Rodenberg" in written


def test_the_outline_hands_the_credit_statement_its_names(tmp_path):
    """Names from the list; roles left blank. The tool cannot know who did what, and a
    plausible guess at authorship credit is worse than a blank."""
    _manifest(tmp_path, ONE, TWO)
    block = outline._credit_authors(tmp_path)
    assert "D. Cale Reeves: " in block and "J. Rodenberg: " in block
    assert "Conceptualization" not in block


def test_no_recorded_authors_means_no_credit_names(tmp_path):
    _manifest(tmp_path)
    assert outline._credit_authors(tmp_path) == ""


# ── the budget does NOT pay for it ───────────────────────────────────────────

def test_the_title_never_counts_against_the_limit():
    """It is a heading, and headings are not writing."""
    assert guards.word_count("# A New Sense of Schelling Segregation\n\nfour real words here"
                             ) == 4


def test_the_author_block_never_counts_against_the_limit():
    """No venue counts a byline, however inclusive its rule. In-pipeline the guards measure
    the assembled manuscript and the block is rendered later — but anything measuring a
    file on disk must exclude it explicitly."""
    block = "A. One^1^, B. Two^2^\n\n^1^ Alpha\n\n^2^ Beta"
    doc = f"# A Title\n\n{block}\n\nfour real words here\n"
    assert guards.word_count(doc, front_matter=block) == 4


def test_measuring_without_the_block_still_works():
    assert guards.word_count("# T\n\nfour real words here") == 4


# ── the guard ────────────────────────────────────────────────────────────────

def test_a_named_manuscript_at_an_anonymized_venue_is_caught():
    findings = guards.authorship("# T\n\nD. Cale Reeves\n\nprose",
                                 "D. Cale Reeves", anonymized=True)
    assert [f.kind for f in findings] == ["identity-leak"]


def test_a_manuscript_missing_its_recorded_authors_is_caught():
    findings = guards.authorship("# T\n\nprose", "D. Cale Reeves")
    assert [f.kind for f in findings] == ["missing-authors"]


def test_a_correctly_named_manuscript_is_silent():
    assert guards.authorship("# T\n\nD. Cale Reeves\n\nprose", "D. Cale Reeves") == []


def test_an_anonymous_manuscript_at_an_anonymized_venue_is_silent():
    assert guards.authorship("# T\n\nprose", "D. Cale Reeves", anonymized=True) == []


def test_a_project_with_no_recorded_authors_makes_no_claim():
    assert guards.authorship("# T\n\nprose", "") == []

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


def _drafted(tmp_path, monkeypatch, cfg, text, venue=""):
    """The markdown paper._write hands pandoc.

    It is no longer a file to read afterwards: the deliverable is the .docx and the
    markdown is unlinked once it renders. paper renders through refdoc, which imports
    to_docx at call time from raconteur.render — that is the seam this crosses.
    """
    seen = {}
    import raconteur.render

    def fake_to_docx(md_path, **kw):
        seen["text"] = Path(md_path).read_text(encoding="utf-8")
        return None                     # no toolchain: the markdown survives, unrendered

    monkeypatch.setattr(raconteur.render, "to_docx", fake_to_docx)
    (tmp_path / "paper").mkdir(exist_ok=True)
    paper._write(tmp_path, cfg, tmp_path / "paper", text, venue=venue)
    return seen["text"]


def test_the_draft_renders_the_authors(tmp_path, monkeypatch):
    _manifest(tmp_path, ONE)
    written = _drafted(tmp_path, monkeypatch, _cfg(), "# A Title\n\n## 1. Intro\n\nx\n")
    assert "D. Cale Reeves" in written and "Corresponding author" in written


def test_a_rendered_draft_leaves_no_markdown_behind(tmp_path, monkeypatch):
    """The deliverable is the .docx and it is the only file this stage leaves. A second
    copy of an approved contract goes stale the moment its deriver is fixed — an outline
    release sat carrying zero bullets for a day after release_markdown learned to keep
    them."""
    _manifest(tmp_path, ONE)
    (tmp_path / "paper").mkdir()
    import raconteur.render
    monkeypatch.setattr(raconteur.render, "to_docx",
                        lambda md_path, **kw: Path(md_path).with_suffix(".docx"))
    monkeypatch.setattr("raconteur.refdoc.unnumber_furniture", lambda p: None)
    monkeypatch.setattr(paper, "_flag_figure_numbers", lambda *a, **k: None)
    paper._write(tmp_path, _cfg(), tmp_path / "paper", "# A Title\n\n## 1. Intro\n\nx\n")
    assert list((tmp_path / "paper").glob("*.md")) == []


def test_the_markdown_survives_a_failed_render(tmp_path, monkeypatch):
    """Without pandoc it is the only output there is, and deleting it would leave the
    author nothing to look at."""
    _manifest(tmp_path, ONE)
    _drafted(tmp_path, monkeypatch, _cfg(), "# A Title\n\n## 1. Intro\n\nx\n")
    assert len(list((tmp_path / "paper").glob("*.md"))) == 1


def test_an_anonymized_venue_gets_no_authors_in_the_draft(tmp_path, monkeypatch):
    """A desk reject on a rule the CFP stated plainly — and the reason authorship is data."""
    _manifest(tmp_path, ONE)
    blind = types.SimpleNamespace(anonymized=True)
    written = _drafted(tmp_path, monkeypatch, _cfg(acm=blind),
                       "# A Title\n\n## 1. Intro\n\nx\n", venue="acm")
    assert "Reeves" not in written and "a@x.edu" not in written


# ── the outline ──────────────────────────────────────────────────────────────

def test_the_outline_carries_no_byline(tmp_path):
    """Removed for the reasons it was removed from the skeleton: authorship is derived from
    the manifest and regenerated at every stage that needs it — paper.py inserts it into the
    manuscript at write time regardless. A copy riding the outline is a second, older home
    for it, five paragraphs the author cannot usefully review, and a named byline on the
    working files of a double-blind submission."""
    from docx import Document
    _manifest(tmp_path, ONE, TWO)
    (tmp_path / "paper").mkdir()
    outline._write(tmp_path, _cfg(), tmp_path / "paper", "## 1. Intro\n- a beat\n")
    docx = next((tmp_path / "paper").glob("*outline*.docx"))
    text = "\n".join(p.text for p in Document(str(docx)).paragraphs)
    assert "A Title" in text
    assert "J. Rodenberg" not in text and "Corresponding author" not in text


def test_the_outline_keeps_its_markdown_when_the_render_fails(tmp_path, monkeypatch):
    """Without pandoc the .md is the only output there is; deleting it would leave the
    author nothing to look at."""
    from raconteur import refdoc
    _manifest(tmp_path, ONE, TWO)
    monkeypatch.setattr(refdoc, "render", lambda *a, **k: None)
    (tmp_path / "paper").mkdir()
    outline._write(tmp_path, _cfg(), tmp_path / "paper", "## 1. Intro\n- a beat\n")
    written = next((tmp_path / "paper").glob("*outline*.md")).read_text()
    assert written.startswith("# A Title") and "Rodenberg" not in written


def test_the_outline_asks_for_no_contribution_statement(tmp_path):
    """Acknowledgements is an empty heading here; the CRediT block belongs at the paper
    stage, where the paper is. Asking the outline for it bought fourteen role bullets with
    both authors' names on every one, against a prompt that said "do not assign any role" —
    and roles are a thing only the authors know."""
    from raconteur.outline import _DRAFT_PROMPT
    assert "{credit_roles}" not in _DRAFT_PROMPT
    assert "{credit_authors}" not in _DRAFT_PROMPT
    assert '"## Acknowledgements" heading with no bullets' in _DRAFT_PROMPT
    assert "Conceptualization" not in _DRAFT_PROMPT


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


# ── the contribution statement, at the stage that owns it ────────────────────

class TestCreditStatement:
    """It was asked of the outline and came back as fourteen role bullets with both
    authors' names on every one, against a prompt that said "do not assign any role". So it
    is WRITTEN, from the manifest, at the paper stage — an outline is a plan for prose, and
    a contribution statement is neither. Names only: who did what is a thing only the
    authors know, and a plausible guess at authorship credit is worse than a blank."""

    def test_it_names_the_recorded_authors_and_assigns_nothing(self, tmp_path):
        _manifest(tmp_path, ONE, TWO)
        block = paper._credit_statement(tmp_path)
        assert block.splitlines()[0] == "- CRediT authorship contribution statement"
        assert "- D. Cale Reeves: [Choose from below]" in block
        assert "- J. Rodenberg: [Choose from below]" in block

    def test_the_taxonomy_is_the_last_line_and_complete(self, tmp_path):
        from raconteur.outline import _CREDIT_ROLES
        _manifest(tmp_path, ONE)
        last = paper._credit_statement(tmp_path).rstrip().splitlines()[-1]
        assert last == ", ".join(_CREDIT_ROLES) + ";"
        assert len(_CREDIT_ROLES) == 14

    def test_an_anonymized_venue_gets_none_of_it(self, tmp_path):
        """The block is a byline by another name."""
        _manifest(tmp_path, ONE, TWO)
        assert paper._credit_statement(tmp_path, anonymized=True) == ""

    def test_no_recorded_authors_means_no_block(self, tmp_path):
        _manifest(tmp_path)
        assert paper._credit_statement(tmp_path) == ""

    def test_a_malformed_manifest_does_not_cost_the_draft(self, tmp_path):
        (tmp_path / "haarpi.yaml").write_text("authors: [oh no: [\n")
        assert paper._credit_statement(tmp_path) == ""

    def test_it_replaces_whatever_the_model_put_under_acknowledgements(self, tmp_path):
        _manifest(tmp_path, ONE)
        text = ("# T\n\n## Acknowledgements\n\nWe thank the reviewers.\n\n"
                "## References\n\nrefs\n")
        out = paper._with_credit_statement(text, tmp_path)
        assert "We thank the reviewers." not in out
        assert "CRediT authorship contribution statement" in out
        assert out.index("CRediT") < out.index("## References")

    def test_it_adds_the_heading_before_references_when_there_is_none(self, tmp_path):
        _manifest(tmp_path, ONE)
        out = paper._with_credit_statement("# T\n\n## References\n\nrefs\n", tmp_path)
        assert out.index("## Acknowledgements") < out.index("## References")

    def test_a_draft_with_no_authors_is_left_exactly_as_it_was(self, tmp_path):
        _manifest(tmp_path)
        text = "# T\n\n## Acknowledgements\n\nkeep me\n"
        assert paper._with_credit_statement(text, tmp_path) == text

"""Who the paper is by, and whose turn it is — two facts the pipeline held badly.

Authorship lived only in prose: a name typed into an annotated .docx and carried forward by
the redline. A major revision regenerates that prose, so a co-author's name survived by
luck. It now lives in the manifest, above every stage, where a re-think that regenerates
the litreview, the build, the experiments and every document below them cannot reach it.

"Whose turn" was worse. ``find_finished_markup`` asked whether the chain ended in ONE named
person's initials, so a co-author with the final pass left a fully annotated document the
planner could not see: "nothing to do", exit 0, ladder stalled.
"""

from __future__ import annotations

import os
from pathlib import Path

from haarpi import naming, planner, project


def _project(tmp_path: Path, **kw) -> project.Manifest:
    m = project.Manifest(name="Schelling Chords", short_title="Chords", **kw)
    project.save_manifest(m, tmp_path)
    (tmp_path / "paper").mkdir(exist_ok=True)
    return m


def _docx(tmp_path: Path, name: str, age: int = 0) -> Path:
    p = tmp_path / "paper" / name
    p.write_bytes(b"x")
    os.utime(p, (1000 + age, 1000 + age))
    return p


# ── whose turn it is ─────────────────────────────────────────────────────────

def test_a_co_author_with_the_final_pass_is_seen():
    """The defect exactly. `_ra_DCR_JR` is finished markup; the old test asked whether the
    last token was DCR and answered no, silently."""
    assert naming.parse(Path("260718_Chords_css2026_ra_DCR_JR.docx"), "Chords") is not None
    _, chain, _ = naming.parse(Path("260718_Chords_css2026_ra_DCR_JR.docx"), "Chords")
    assert chain[-1].lower() != "ra" and not naming.is_release(chain)


def test_the_planner_finds_markup_a_co_author_touched_last(tmp_path):
    m = _project(tmp_path)
    _docx(tmp_path, "260718_Chords_css2026_ra_DCR_JR.docx")
    found = planner.find_finished_markup(tmp_path, m)
    assert found is not None and found[1].name.endswith("_ra_DCR_JR.docx")


def test_the_planner_still_finds_the_ordinary_single_reviewer_chain(tmp_path):
    m = _project(tmp_path)
    _docx(tmp_path, "260718_Chords_css2026_ra_DCR.docx")
    found = planner.find_finished_markup(tmp_path, m)
    assert found is not None and found[1].name.endswith("_ra_DCR.docx")


def test_a_tool_draft_is_not_finished_markup(tmp_path):
    """`_ra` is the tool's turn — the ball is in nobody's court but its own."""
    m = _project(tmp_path)
    _docx(tmp_path, "260718_Chords_css2026_ra.docx")
    assert planner.find_finished_markup(tmp_path, m) is None


def test_a_release_is_not_markup_on_itself(tmp_path):
    """A release ends in a deliverable word — not `ra`, and emphatically not a reviewer.
    Without the release check the new, looser test reads it as finished markup."""
    m = _project(tmp_path)
    _docx(tmp_path, "260718_Chords_outline.docx")
    assert planner.find_finished_markup(tmp_path, m) is None


def test_the_newest_markup_wins(tmp_path):
    m = _project(tmp_path)
    _docx(tmp_path, "260718_Chords_css2026_ra_JR.docx", age=0)
    _docx(tmp_path, "260718_Chords_css2026_ra_JR_DCR.docx", age=5)
    found = planner.find_finished_markup(tmp_path, m)
    assert found[1].name.endswith("_ra_JR_DCR.docx")


# ── who the paper is by ──────────────────────────────────────────────────────

def test_authors_persist_through_a_save_load_round_trip(tmp_path):
    """save_manifest enumerates its keys rather than using asdict(), so a field that is not
    listed there loads correctly and silently fails to persist — the edit looks like it
    never happened."""
    m = _project(tmp_path)
    planner.run_authors(tmp_path, "add", name="D. Cale Reeves", initials="DCR",
                        affiliation="UT Austin")
    assert project.authors(project.load_manifest(tmp_path)) == [
        {"name": "D. Cale Reeves", "initials": "DCR", "affiliation": "UT Austin"}]


def test_a_co_author_joins_without_disturbing_authorship_order(tmp_path):
    _project(tmp_path)
    planner.run_authors(tmp_path, "add", name="D. Cale Reeves", initials="DCR")
    planner.run_authors(tmp_path, "add", name="Joshua Isaiah Rodenberg", initials="JR")
    names = [a["name"] for a in project.authors(project.load_manifest(tmp_path))]
    assert names == ["D. Cale Reeves", "Joshua Isaiah Rodenberg"]


def test_an_author_can_be_inserted_at_a_position(tmp_path):
    """Authorship order carries meaning and is the author's to set — never sorted."""
    _project(tmp_path)
    planner.run_authors(tmp_path, "add", name="Second")
    planner.run_authors(tmp_path, "add", name="First", position=1)
    names = [a["name"] for a in project.authors(project.load_manifest(tmp_path))]
    assert names == ["First", "Second"]


def test_adding_the_same_author_twice_is_refused(tmp_path):
    _project(tmp_path)
    assert planner.run_authors(tmp_path, "add", name="D. Cale Reeves") == 0
    assert planner.run_authors(tmp_path, "add", name="d. cale reeves") == 2


def test_an_author_with_no_name_is_refused(tmp_path):
    """The one field with nothing to fall back on. A blank author is worse than none."""
    _project(tmp_path)
    assert planner.run_authors(tmp_path, "add", name="  ") == 2


def test_an_affiliation_can_be_corrected_without_retyping_the_author(tmp_path):
    _project(tmp_path)
    planner.run_authors(tmp_path, "add", name="J. Rodenberg", initials="JR",
                        affiliation="Wrong")
    planner.run_authors(tmp_path, "set", initials="JR", affiliation="Right")
    a = project.authors(project.load_manifest(tmp_path))[0]
    assert a["affiliation"] == "Right" and a["name"] == "J. Rodenberg"


def test_an_author_can_be_removed(tmp_path):
    _project(tmp_path)
    planner.run_authors(tmp_path, "add", name="Departed")
    planner.run_authors(tmp_path, "remove", name="Departed")
    assert project.authors(project.load_manifest(tmp_path)) == []


def test_editing_someone_not_listed_is_refused_not_silently_created(tmp_path):
    _project(tmp_path)
    assert planner.run_authors(tmp_path, "set", name="Nobody", affiliation="X") == 2


def test_an_empty_author_list_is_the_honest_default(tmp_path):
    """A project predating this field has no authors recorded. Synthesizing one from
    `initials` would be the tool inventing a name, which is the whole thing it must not do."""
    _project(tmp_path, initials="DCR")
    assert project.authors(project.load_manifest(tmp_path)) == []


def test_the_reviewers_include_the_driver_even_when_unlisted(tmp_path):
    _project(tmp_path, initials="DCR")
    planner.run_authors(tmp_path, "add", name="J. Rodenberg", initials="JR")
    m = project.load_manifest(tmp_path)
    assert project.reviewer_initials(m) == ["JR", "DCR"]


def test_unknown_keys_in_a_hand_edited_manifest_are_dropped(tmp_path):
    """The manifest is a file a human edits. A stray key must not reach a render step."""
    assert project.normalize_author(
        {"name": "X", "affiliation": "Y", "twitter": "@z"}) == {"name": "X",
                                                               "affiliation": "Y"}


# ── correspondence ───────────────────────────────────────────────────────────

def _two_authors(tmp_path: Path, corr_a=False, corr_b=False) -> project.Manifest:
    _project(tmp_path)
    planner.run_authors(tmp_path, "add", name="A. One", email="a@x.edu",
                        affiliation="Alpha", corresponding=corr_a or None)
    planner.run_authors(tmp_path, "add", name="B. Two", email="b@y.edu",
                        affiliation="Beta", corresponding=corr_b or None)
    return project.load_manifest(tmp_path)


def test_one_corresponding_author_is_named_in_the_singular(tmp_path):
    block = project.authors_block(_two_authors(tmp_path, corr_a=True))
    assert "Corresponding author: A. One (a@x.edu)" in block
    assert "Co-corresponding" not in block


def test_two_corresponding_authors_are_co_corresponding(tmp_path):
    """"Corresponding author: A, B" reads as one person with two names."""
    block = project.authors_block(_two_authors(tmp_path, corr_a=True, corr_b=True))
    assert "Co-corresponding authors: A. One (a@x.edu), B. Two (b@y.edu)" in block


def test_a_non_corresponding_authors_email_is_recorded_but_never_published(tmp_path):
    """The flag's whole effect. B's address is in the manifest and must not reach the page."""
    m = _two_authors(tmp_path, corr_a=True)
    assert project.authors(m)[1]["email"] == "b@y.edu"
    assert "b@y.edu" not in project.authors_block(m)


def test_no_corresponding_author_means_no_contact_line(tmp_path):
    block = project.authors_block(_two_authors(tmp_path))
    assert "orresponding" not in block and "@" not in block


def test_the_flag_can_be_taken_away_again(tmp_path):
    """`if corresponding:` would make --no-corresponding silently do nothing."""
    _two_authors(tmp_path, corr_a=True)
    planner.run_authors(tmp_path, "set", name="A. One", corresponding=False)
    assert project.corresponding_authors(project.load_manifest(tmp_path)) == []


def test_a_corresponding_author_with_no_email_still_renders_their_name(tmp_path):
    _project(tmp_path)
    planner.run_authors(tmp_path, "add", name="A. One", corresponding=True)
    assert "Corresponding author: A. One" in project.authors_block(
        project.load_manifest(tmp_path))


# ── the block under the title ────────────────────────────────────────────────

def test_an_anonymized_venue_gets_no_author_block(tmp_path):
    """The reason this is data and not prose: a double-blind submission must be strippable
    on the venue's say-so, and prose cannot be."""
    m = _two_authors(tmp_path, corr_a=True)
    assert project.authors_block(m, anonymized=True) == ""


def test_one_shared_affiliation_is_printed_without_markers(tmp_path):
    _project(tmp_path)
    planner.run_authors(tmp_path, "add", name="A. One", affiliation="Alpha")
    planner.run_authors(tmp_path, "add", name="B. Two", affiliation="Alpha")
    block = project.authors_block(project.load_manifest(tmp_path))
    assert "A. One, B. Two" in block and "^1^" not in block


def test_differing_affiliations_get_numbered_markers(tmp_path):
    block = project.authors_block(_two_authors(tmp_path))
    assert "A. One^1^, B. Two^2^" in block
    assert "^1^ Alpha" in block and "^2^ Beta" in block


def test_an_empty_author_list_renders_nothing_rather_than_an_empty_heading(tmp_path):
    _project(tmp_path)
    assert project.authors_block(project.load_manifest(tmp_path)) == ""


# ── the wizard ───────────────────────────────────────────────────────────────

def test_a_bare_call_with_nobody_at_the_terminal_lists_instead_of_blocking(tmp_path):
    """A queued task inherits a non-tty. Blocking on input() nobody will type hangs the
    runner and holds the GPU resource behind it."""
    _project(tmp_path)
    assert planner.run_authors(tmp_path, interactive=False) == 0


def test_the_wizard_matches_an_author_by_position_initials_or_name():
    people = [{"name": "D. Cale Reeves", "initials": "DCR"},
              {"name": "Joshua Isaiah Rodenberg", "initials": "JR"}]
    assert planner._match_author(people, "2")["initials"] == "JR"
    assert planner._match_author(people, "jr")["initials"] == "JR"
    assert planner._match_author(people, "Rodenberg")["initials"] == "JR"
    assert planner._match_author(people, "nobody") is None


def test_the_wizard_writes_after_each_change(tmp_path, monkeypatch):
    """Add one author, then quit. The manifest must already hold them — a wizard that
    saves only on a clean exit loses the work when the terminal closes."""
    m = _project(tmp_path)
    answers = iter(["a", "J. Rodenberg", "JR", "Beta", "", "jr@y.edu", "y", "d"])
    monkeypatch.setattr(planner, "_ask", lambda *a, **k: next(answers))
    planner._author_wizard(tmp_path, m)
    saved = project.authors(project.load_manifest(tmp_path))
    assert saved == [{"name": "J. Rodenberg", "initials": "JR", "affiliation": "Beta",
                      "email": "jr@y.edu", "corresponding": True}]

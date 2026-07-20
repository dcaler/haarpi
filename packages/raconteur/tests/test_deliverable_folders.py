"""One folder per deliverable per venue, and the one-time move into it.

Flat, `paper/` held every generation of every deliverable for every venue in one directory
— four cycles of five documents, where knowing what you were looking at meant parsing a
filename. The chain still carries the same tokens; the folders are where a document lives,
not what it is called.

The risk this pins is discovery. Every stall this pipeline has had was a reader that looked
in one place, found nothing, and reported success — so the tests below check that what was
findable before the move is findable after it.
"""

from __future__ import annotations

from pathlib import Path

from haarpi import naming as hnaming
from haarpi import planner, project as hproject
from raconteur import migrate
from raconteur.naming import deliverable_dir


# ── where a deliverable lives ────────────────────────────────────────────────

def test_each_deliverable_has_its_own_folder(tmp_path):
    p = tmp_path / "paper"
    assert deliverable_dir(p, "onepager") == p / "onepager"
    assert deliverable_dir(p, "venue") == p / "venue"
    assert deliverable_dir(p, "outline", "css2026") == p / "css2026" / "outline"
    assert deliverable_dir(p, "manuscript", "css2026") == p / "css2026" / "manuscript"


def test_the_manuscript_is_the_deliverable_with_no_word_of_its_own(tmp_path):
    """Its chain carries no deliverable token, so "" must resolve to the manuscript rather
    than to the paper root — which would put a draft back in the flat layout."""
    p = tmp_path / "paper"
    assert deliverable_dir(p, "", "css2026") == p / "css2026" / "manuscript"


def test_the_narrative_and_the_slate_precede_the_venue_fork(tmp_path):
    """A one-pager belongs to the work, not to whoever might publish it, so it is never
    venue-scoped even when a venue is passed."""
    p = tmp_path / "paper"
    assert deliverable_dir(p, "onepager", "css2026") == p / "onepager"
    assert deliverable_dir(p, "venue", "css2026") == p / "venue"


def test_a_project_with_no_slate_still_has_somewhere_to_put_a_manuscript(tmp_path):
    p = tmp_path / "paper"
    assert deliverable_dir(p, "manuscript") == p / "manuscript"
    assert deliverable_dir(p, "outline") == p / "outline"


def test_two_venues_do_not_share_a_folder(tmp_path):
    p = tmp_path / "paper"
    assert deliverable_dir(p, "outline", "ismir") != deliverable_dir(p, "outline", "jasss")


# ── the migration ────────────────────────────────────────────────────────────

def _flat(tmp_path: Path) -> Path:
    paper = tmp_path / "paper"
    (paper / "output").mkdir(parents=True)
    (paper / "old").mkdir()
    for name in ("260715_Chords_onepager_ra.docx",
                 "260717_Chords_venue_ra.docx",
                 "260718_Chords_css2026_outline_ra_DCR.docx",
                 "260719_Chords_css2026_ra.md"):
        (paper / name).write_text("x")
    for name in ("260717_Chords_onepager.docx", "260719_Chords_css2026_outline.md"):
        (paper / "output" / name).write_text("x")
    (paper / "old" / "260710_Chords_onepager_ra.docx").write_text("x")
    (paper / "venue_analysis.md").write_text("x")
    (paper / "raconteur.yaml").write_text(
        "short_title: Chords\nvenues:\n  css2026:\n    name: CSS2026\n")
    return paper


def test_every_deliverable_lands_in_its_folder(tmp_path):
    paper = _flat(tmp_path)
    moves = dict(migrate.plan(paper, "Chords", ["css2026"]))
    got = {s.name: str(d.relative_to(paper).parent) for s, d in moves.items()}
    assert got["260715_Chords_onepager_ra.docx"] == "onepager"
    assert got["260717_Chords_venue_ra.docx"] == "venue"
    assert got["260718_Chords_css2026_outline_ra_DCR.docx"] == "css2026/outline"
    assert got["260719_Chords_css2026_ra.md"] == "css2026/manuscript"
    assert got["venue_analysis.md"] == "venue"


def test_a_release_goes_to_its_deliverables_output(tmp_path):
    paper = _flat(tmp_path)
    moves = dict(migrate.plan(paper, "Chords", ["css2026"]))
    got = {s.name: str(d.relative_to(paper).parent) for s, d in moves.items()}
    assert got["260717_Chords_onepager.docx"] == "onepager/output"
    assert got["260719_Chords_css2026_outline.md"] == "css2026/outline/output"


def test_a_discard_stays_a_discard(tmp_path):
    """`old/` is where a discard goes, and it must not come back as live work."""
    paper = _flat(tmp_path)
    moves = dict(migrate.plan(paper, "Chords", ["css2026"]))
    got = {s.name: str(d.relative_to(paper).parent) for s, d in moves.items()}
    assert got["260710_Chords_onepager_ra.docx"] == "onepager/old"


def test_the_config_is_not_a_deliverable(tmp_path):
    paper = _flat(tmp_path)
    moved = [s.name for s, _ in migrate.plan(paper, "Chords", ["css2026"])]
    assert "raconteur.yaml" not in moved


def test_a_file_outside_the_naming_chain_is_left_alone(tmp_path):
    """A hand-made note is not ours to file, and guessing where it goes is how one ends up
    misfiled as a draft."""
    paper = _flat(tmp_path)
    (paper / "notes to self.md").write_text("x")
    moved = [s.name for s, _ in migrate.plan(paper, "Chords", ["css2026"])]
    assert "notes to self.md" not in moved


def test_migration_refuses_to_clobber(tmp_path):
    """Two generations coexisting is the whole point of the chain; a silent overwrite of
    one is unrecoverable."""
    paper = _flat(tmp_path)
    (paper / "onepager").mkdir()
    (paper / "onepager" / "260715_Chords_onepager_ra.docx").write_text("EXISTING")
    assert migrate.run(tmp_path, dry_run=False) == 1
    assert (paper / "onepager" / "260715_Chords_onepager_ra.docx").read_text() == "EXISTING"
    assert (paper / "260715_Chords_onepager_ra.docx").exists()   # nothing moved


def test_migrating_twice_is_a_no_op(tmp_path):
    paper = _flat(tmp_path)
    assert migrate.run(tmp_path) == 0
    assert migrate.plan(paper, "Chords", ["css2026"]) == []


# ── discovery survives the move ──────────────────────────────────────────────

def _manifest(tmp_path: Path) -> hproject.Manifest:
    m = hproject.Manifest(name="Chords", short_title="Chords")
    hproject.save_manifest(m, tmp_path)
    return m


def test_the_planner_finds_markup_inside_a_deliverable_folder(tmp_path):
    """The stall this codebase keeps having to design against: a reader that looks one
    level deep, finds nothing, and reports success."""
    _flat(tmp_path)
    m = _manifest(tmp_path)
    migrate.run(tmp_path)
    found = planner.find_finished_markup(tmp_path, m)
    assert found is not None
    assert found[1].name == "260718_Chords_css2026_outline_ra_DCR.docx"
    assert found[1].parent.name == "outline"


def test_an_archived_file_is_never_live_markup(tmp_path):
    paper = _flat(tmp_path)
    (paper / "old" / "260709_Chords_onepager_ra_DCR.docx").write_text("x")
    m = _manifest(tmp_path)
    migrate.run(tmp_path)
    found = planner.find_finished_markup(tmp_path, m)
    assert "old" not in found[1].parts


def _release_names(tmp_path: Path, m: hproject.Manifest) -> set[str]:
    return {p.name
            for d in hproject.stage_search_dirs(tmp_path, m, "paper")
            for p in d.glob("*")
            if p.is_file() and (parsed := hnaming.parse(p, m.short_title))
            and hnaming.is_release(parsed[1])}


def test_no_release_is_lost_in_the_move(tmp_path):
    """Which release is "latest" across two directories is an mtime coin-toss; that every
    release is still discoverable is the property that matters."""
    _flat(tmp_path)
    m = _manifest(tmp_path)
    before = _release_names(tmp_path, m)
    migrate.run(tmp_path)
    assert before and _release_names(tmp_path, m) == before
    assert hproject.latest_release(tmp_path, m, "paper") is not None


def test_in_flight_work_is_still_seen_after_the_move(tmp_path):
    _flat(tmp_path)
    m = _manifest(tmp_path)
    migrate.run(tmp_path)
    assert hproject.in_flight(tmp_path, m, "paper") is not None


# ── the venue's template belongs to the venue ────────────────────────────────

def test_a_venues_template_lives_in_its_venue_folder(tmp_path):
    """A template IS the venue's house style. The old paper/templates/<venue>/ named the
    venue twice — once in a shared folder, once inside it."""
    from raconteur.package import _template_dir
    assert _template_dir(tmp_path, "css2026") == tmp_path / "paper" / "css2026" / "templates"


def test_the_template_folder_moves_under_its_venue(tmp_path):
    paper = _flat(tmp_path)
    (paper / "templates" / "css2026").mkdir(parents=True)
    (paper / "templates" / "css2026" / "llncs.cls").write_text("x")
    migrate.run(tmp_path)
    assert (paper / "css2026" / "templates" / "llncs.cls").is_file()
    assert not (paper / "templates" / "css2026").exists()


def test_a_template_for_an_unknown_venue_is_left_alone(tmp_path):
    """Filing a template under a venue the slate has never heard of invents a venue folder."""
    paper = _flat(tmp_path)
    (paper / "templates" / "mystery").mkdir(parents=True)
    (paper / "templates" / "mystery" / "x.cls").write_text("x")
    migrate.run(tmp_path)
    assert (paper / "templates" / "mystery" / "x.cls").is_file()

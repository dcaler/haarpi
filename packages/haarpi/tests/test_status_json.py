"""`.haarpi/status.json`: the full milestone list raDash reads, derived from what is on disk."""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

from haarpi import cli, project, status


def _proj(tmp_path: Path, **kw) -> project.Manifest:
    m = project.Manifest(name="Chords", short_title="Chords", **kw)
    project.save_manifest(m, tmp_path)
    return project.load_manifest(tmp_path)


def _touch(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x")
    return p


def _ids(doc):
    return {r["id"]: r for r in doc["milestones"]}


def test_a_new_project_lists_every_milestone_and_none_is_done(tmp_path):
    m = _proj(tmp_path)
    _touch(tmp_path / "litReview" / "output" / "261026_Chords_litreview_ra.md")
    doc = status.build(tmp_path, m, venues=["css2026"])
    rows = _ids(doc)
    assert list(rows)[:5] == ["litreview", "design", "build", "experiments", "paper.onepager"]
    assert {"paper.css2026.outline", "paper.css2026.draft", "paper.css2026.submission"} <= set(rows)
    lit = rows["litreview"]
    assert lit["state"] == "in_flight" and lit["waiting_on"] == "you"     # a tool draft waits on you
    assert lit["cycle_started"] == "2026-10-26" and lit["rounds"] == 0
    assert rows["design"]["state"] == "waiting" and rows["design"]["blocked_by"] == ["litreview"]
    assert rows["paper.css2026.submission"]["state"] == "waiting"
    assert not any(r["state"] == "done" for r in doc["milestones"])
    assert doc["tracks"] == [{"venue": "css2026", "outcome": None, "milestones": [
        "paper.css2026.outline", "paper.css2026.draft", "paper.css2026.submission"]}]


def test_your_markup_waits_on_the_tool_and_counts_a_round(tmp_path):
    m = _proj(tmp_path)
    _touch(tmp_path / "litReview" / "output" / "261013_Chords_litreview_ra_DCR_ra_DCR.docx")
    lit = _ids(status.build(tmp_path, m, venues=[]))["litreview"]
    assert lit["waiting_on"] == "tool" and lit["rounds"] == 2


def test_a_submission_makes_its_track_a_success_and_implies_what_came_before(tmp_path):
    """SchellingChords → CSS 2026: the rungs before the submission predate the file layout, but
    the submission proves they happened. A second venue is a separate, unfinished process."""
    m = _proj(tmp_path)
    _touch(tmp_path / "litReview" / "output" / "260717_Chords_litreview.md")
    _touch(tmp_path / "paper" / "submission" / "css2026" / "submission_DCR.pdf")
    doc = status.build(tmp_path, m, venues=["css2026", "leonardo"])
    rows = _ids(doc)
    assert rows["paper.css2026.submission"]["state"] == "done"
    for mid in ("paper.css2026.outline", "paper.css2026.draft", "paper.onepager", "paper.venue",
                "design", "build", "experiments"):
        assert rows[mid]["state"] == "done" and rows[mid]["implied"], mid
    assert rows["litreview"]["state"] == "done" and not rows["litreview"]["implied"]
    assert rows["paper.leonardo.outline"]["state"] != "done"
    tracks = {t["venue"]: t["outcome"] for t in doc["tracks"]}
    assert tracks == {"css2026": "submitted", "leonardo": None}


def test_a_deck_is_its_venues_milestone_with_its_date_as_target(tmp_path):
    m = _proj(tmp_path, decks={"longtalk": {"venue": "CSS2026", "date": "31 October 2026"}})
    _touch(tmp_path / "slides" / "CSS2026" / "260905_Chords_deck_ra.pptx")
    doc = status.build(tmp_path, m, venues=["css2026"], today=date(2026, 10, 26))
    deck = _ids(doc)["deck.longtalk"]
    assert deck["track"] == "css2026" and deck["state"] == "in_flight"
    assert deck["target"] == "2026-10-31" and deck["health"] == "on_track"
    assert "deck.longtalk" in doc["tracks"][0]["milestones"]


def test_open_tasks_say_who_it_waits_on_and_project_a_date(tmp_path):
    m = _proj(tmp_path, milestones={"litreview": "2026-10-20", "design": "2027-01-01"})
    _touch(tmp_path / "litReview" / "output" / "261026_Chords_litreview_ra.md")
    tasks = [{"id": 5, "title": "rabbithole report 2", "status": "todo", "resource_ids": [2],
              "duration": 3.0},
             {"id": 6, "title": "rabbithole comment 2", "status": "todo", "resource_ids": [1],
              "duration": 0.2},
             {"id": 4, "title": "rabbithole next 1", "status": "done", "resource_ids": [2]}]
    doc = status.build(tmp_path, m, tasks=tasks, human_ids={1}, venues=[],
                       today=date(2026, 10, 26))
    lit = _ids(doc)["litreview"]
    assert [t["id"] for t in lit["open_tasks"]] == [5, 6]
    assert lit["waiting_on"] == "queue" and lit["projected"]["basis"] == "estimate"
    assert lit["health"] == "late"                                        # past its target
    assert _ids(doc)["design"]["health"] == "on_track"
    tasks[0]["status"] = "in_progress"
    lit = _ids(status.build(tmp_path, m, tasks=tasks, human_ids={1}, venues=[]))["litreview"]
    assert lit["waiting_on"] == "running"


def test_a_projection_past_the_target_is_at_risk_and_a_late_release_says_by_how_much(tmp_path):
    r = status._row("x", target="2026-11-01", projected={"date": "2026-11-05", "basis": "estimate"})
    status._health(r, date(2026, 10, 26))
    assert r["health"] == "at_risk"
    r = status._row("x", state="done", target="2026-11-01",
                    released={"file": "f", "date": "2026-11-04"})
    status._health(r, date(2026, 11, 10))
    assert r["health"] == "done" and r["late_by_days"] == 3


def test_targets_persist_in_the_manifest(tmp_path):
    _proj(tmp_path, milestones={"paper.css2026.submission": "2026-12-01"})
    assert project.load_manifest(tmp_path).milestones == {"paper.css2026.submission": "2026-12-01"}
    _proj(tmp_path)
    assert "milestones" not in (tmp_path / project.MANIFEST).read_text()


def test_the_file_is_written_whole_and_a_failure_never_raises(tmp_path, monkeypatch):
    _proj(tmp_path)
    fp = status.write_status(tmp_path, generated_by="haarpi status")
    doc = json.loads(fp.read_text())
    assert doc["schema"] == status.SCHEMA and doc["generated_by"] == "haarpi status"
    assert doc["trundlr"] is False                     # no trundlr project: nothing projected
    assert not [p for p in fp.parent.iterdir() if p.name.endswith(".tmp")]
    assert status.write_status(tmp_path / "nowhere") is None
    monkeypatch.setattr(status, "build", lambda *a, **k: 1 / 0)
    assert status.write_status(tmp_path) is None


def test_every_haarpi_verb_refreshes_the_file(tmp_path, monkeypatch):
    _proj(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["status"]) == 0
    assert status.status_path(tmp_path).is_file()
    os.remove(status.status_path(tmp_path))
    monkeypatch.setattr(cli, "_pipeline_verb", lambda *a: 1 / 0)
    try:
        cli.main(["next"])
    except ZeroDivisionError:
        pass
    assert status.status_path(tmp_path).is_file()       # written on the error path too

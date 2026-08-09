"""razzle.interview — the pure-python (no-LLM) deck configurator: scripted `input()`, config written
to the manifest, per-format consumption by gather, and best-effort session queueing."""

from __future__ import annotations

import pytest

from haarpi import project
from razzle import gather, interview


def _feed(monkeypatch, answers):
    it = iter(answers)
    monkeypatch.setattr("builtins.input", lambda *a: next(it))


def _project(tmp_path):
    m = project.Manifest(
        name="demo", short_title="demo", brief="x",
        authors=[{"name": "Ada", "affiliations": ["UC Berkeley"]},
                 {"name": "Bo", "affiliations": ["Cambridge"]}],
        funders=[{"name": "Sloan"}])
    project.save_manifest(m, tmp_path)
    return tmp_path


def test_interview_writes_deck_config(tmp_path, monkeypatch):
    monkeypatch.setenv("RAZZLE_HOME", str(tmp_path / "razzle_home"))   # empty registry → no logos
    root = _project(tmp_path)
    _feed(monkeypatch, [
        "2",              # formats: shorttalk (longtalk/shorttalk/lecture/poster → 2)
        "ISMIR 2026",     # venue
        "2026-11-01",     # date
        "",               # presenting authors: Enter = all
        "y",              # include UC Berkeley logo
        "n",              # skip Cambridge logo
        "",               # funders: Enter = all
    ])
    out = interview.run(root, queue=False)
    assert out["formats"] == ["shorttalk"]

    m = project.load_manifest(root)
    assert m.deck_formats == ["shorttalk"]
    d = m.decks["shorttalk"]
    assert d == {"venue": "ISMIR 2026", "date": "2026-11-01",
                 "authors": ["Ada", "Bo"], "affiliations": ["UC Berkeley"], "funders": ["Sloan"]}


def test_gather_scopes_byline_and_logos_to_the_deck(tmp_path, monkeypatch):
    home = tmp_path / "razzle_home"
    (home / "logos").mkdir(parents=True)
    (home / "logos" / "ucb.png").write_bytes(b"x")
    (home / "affiliations.yaml").write_text("UC Berkeley:\n  logo: logos/ucb.png\n")
    monkeypatch.setenv("RAZZLE_HOME", str(home))

    m = project.Manifest(
        name="demo", short_title="demo", brief="x",
        authors=[{"name": "Ada", "affiliations": ["UC Berkeley"]},
                 {"name": "Bo", "affiliations": ["Cambridge"]}],
        decks={"shorttalk": {"venue": "ISMIR", "date": "2026-11-01",
                             "authors": ["Ada"], "affiliations": ["UC Berkeley"], "funders": []}})
    project.save_manifest(m, tmp_path)

    # byline is the deck's chosen presenter, not all authors
    assert gather.byline(tmp_path, "shorttalk") == "Ada"
    # logos are the deck's chosen affiliation (UC Berkeley has a registered logo; Cambridge excluded)
    logos = gather.logos(tmp_path, "shorttalk")
    assert [p.name for p in logos] == ["ucb.png"]
    b = gather.bundle(tmp_path, "shorttalk")
    assert b["venue"] == "ISMIR" and b["byline"] == "Ada"


def test_apply_byline_stamps_the_title_slide():
    spec = [{"role": "title", "title": "T", "subtitle": "was"}, {"role": "content", "title": "C"}]
    gather.apply_byline(spec, "Ada, Bo")
    assert spec[0]["subtitle"] == "Ada, Bo"
    assert "subtitle" not in spec[1]                       # only the title slide is stamped


def test_queue_sessions_creates_per_format_and_removes_the_prompt(tmp_path, monkeypatch):
    """_queue_sessions posts one authoring task per format and deletes the pick-format prompt."""
    m = project.Manifest(name="demo", short_title="demo", brief="x", trundlr_project_id=7)

    created, deleted = [], []

    class FakeClient:
        def __init__(self, url): pass
        def create_task(self, title, pid, **kw): created.append((title, pid)); return {"id": 1}
        def list_tasks(self, pid):
            return [{"id": 99, "title": "razzle deck: pick format(s)", "status": "todo"}]
        def delete_task(self, tid): deleted.append(tid)

    monkeypatch.setattr(interview._hconfig, "merged_config",
                        lambda *a, **k: {"trundlr": {"url": "http://x", "human_resource": 1}})
    monkeypatch.setattr(interview._trundlr, "TrundlrClient", FakeClient)

    queued = interview._queue_sessions(m, ["shorttalk", "longtalk"])
    assert queued == ["shorttalk", "longtalk"]
    assert [t for t, _ in created] == ["razzle deck shorttalk", "razzle deck longtalk"]
    assert deleted == [99]                                 # the fulfilled prompt is removed

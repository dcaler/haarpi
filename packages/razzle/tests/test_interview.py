"""razzle.interview — the pure-python (no-LLM) deck configurator: scripted `input()`, config written
to the manifest, per-format consumption by gather. It writes config ONLY — task creation belongs to
haarpi, so the interview must never touch trundlr."""

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
    out = interview.run(root)
    assert out["formats"] == ["shorttalk"]

    m = project.load_manifest(root)
    assert m.deck_formats == ["shorttalk"]
    d = m.decks["shorttalk"]
    assert d == {"venue": "ISMIR 2026", "date": "2026-11-01",
                 "authors": ["Ada", "Bo"], "affiliations": ["UC Berkeley"], "funders": ["Sloan"]}


def test_interview_offers_every_authors_affiliation_logo_not_just_the_presenter(tmp_path, monkeypatch):
    """A co-author's affiliation logo must be offered even when they are not presenting — the title
    slide shows all co-authors' affiliations. Presenting = Ada alone, but both UC Berkeley (Ada) and
    Cambridge (Bo) are asked."""
    monkeypatch.setenv("RAZZLE_HOME", str(tmp_path / "razzle_home"))   # empty registry
    root = _project(tmp_path)                                          # Ada@UC Berkeley, Bo@Cambridge
    asked: list[str] = []
    real_input = _feed  # noqa: F841
    it = iter(["2", "ISMIR 2026", "2026-11-01", "1",   # formats, venue, date, presenting = Ada only
               "y", "y", ""])                            # UC Berkeley y, Cambridge y, funders all
    def _rec(prompt=""):
        if "affiliation logo" in prompt:
            asked.append(prompt)
        return next(it)
    monkeypatch.setattr("builtins.input", _rec)

    out = interview.run(root)
    assert out["decks"]["shorttalk"]["authors"] == ["Ada"]            # only Ada presents
    assert any("UC Berkeley" in q for q in asked) and any("Cambridge" in q for q in asked)
    assert out["decks"]["shorttalk"]["affiliations"] == ["UC Berkeley", "Cambridge"]   # both offered + taken


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


def test_interview_never_touches_trundlr(tmp_path, monkeypatch):
    """The board belongs to haarpi: the interview writes config and queues/deletes NOTHING. Any
    import of the trundlr module is a regression (that job moved to `haarpi next`)."""
    monkeypatch.setenv("RAZZLE_HOME", str(tmp_path / "razzle_home"))
    root = _project(tmp_path)

    import sys
    class _Boom:
        def __getattr__(self, _):  # any use of trundlr through the interview explodes
            raise AssertionError("razzle.interview must not touch trundlr")
    monkeypatch.setitem(sys.modules, "haarpi.trundlr", _Boom())

    _feed(monkeypatch, ["2", "ISMIR 2026", "2026-11-01", "", "y", "n", ""])
    out = interview.run(root)                              # completes without any trundlr call
    assert out["formats"] == ["shorttalk"] and "queued" not in out
    assert project.load_manifest(root).deck_formats == ["shorttalk"]

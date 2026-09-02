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


def _byline_project(tmp_path, monkeypatch):
    """Ada presents; Bo does not. Both are authors. Both have an email."""
    home = tmp_path / "razzle_home"
    (home / "logos").mkdir(parents=True)
    (home / "logos" / "ucb.png").write_bytes(b"x")
    (home / "affiliations.yaml").write_text("UC Berkeley:\n  logo: logos/ucb.png\n")
    monkeypatch.setenv("RAZZLE_HOME", str(home))

    m = project.Manifest(
        name="demo", short_title="demo", brief="x",
        authors=[{"name": "Ada", "affiliations": ["UC Berkeley"], "email": "ada@ucb.edu"},
                 {"name": "Bo", "affiliations": ["Cambridge"], "email": "bo@cam.ac.uk"}],
        decks={"shorttalk": {"venue": "ISMIR", "date": "2026-11-01",
                             "authors": ["Ada"], "affiliations": ["UC Berkeley"], "funders": []}})
    project.save_manifest(m, tmp_path)
    return tmp_path


def test_gather_scopes_logos_to_the_deck_but_never_the_byline(tmp_path, monkeypatch):
    """The deck config scopes the LOGOS. It must not scope the BYLINE: authorship is a fact about
    the work, so a co-author who is not travelling is still credited on the title slide."""
    root = _byline_project(tmp_path, monkeypatch)

    assert gather.byline(root) == "Ada, Bo"               # every author, in authorship order
    # logos are the deck's chosen affiliation (UC Berkeley has a registered logo; Cambridge excluded)
    assert [p.name for p in gather.logos(root, "shorttalk")] == ["ucb.png"]
    b = gather.bundle(root, "shorttalk")
    assert b["venue"] == "ISMIR" and b["byline"] == "Ada, Bo"


def test_the_contact_address_is_the_presenters_alone(tmp_path, monkeypatch):
    """Exactly ONE email reaches the title slide, and it belongs to whoever is at the podium — a
    contact address for this talk, not a credential printed for every co-author."""
    root = _byline_project(tmp_path, monkeypatch)

    assert gather.presenter(root, "shorttalk")["name"] == "Ada"
    assert gather.presenter_email(root, "shorttalk") == "ada@ucb.edu"
    assert gather.bundle(root, "shorttalk")["email"] == "ada@ucb.edu"
    assert "bo@cam.ac.uk" not in gather.bundle(root, "shorttalk")["byline"]


def test_presenter_falls_back_to_the_corresponding_author(tmp_path, monkeypatch):
    """No deck config for the format — the contact address is the corresponding author's, not
    simply the first author's."""
    monkeypatch.setenv("RAZZLE_HOME", str(tmp_path / "empty"))
    m = project.Manifest(
        name="demo", short_title="demo", brief="x",
        authors=[{"name": "Ada", "email": "ada@ucb.edu"},
                 {"name": "Bo", "email": "bo@cam.ac.uk", "corresponding": True}])
    project.save_manifest(m, tmp_path)

    assert gather.byline(tmp_path) == "Ada, Bo"
    assert gather.presenter_email(tmp_path, "shorttalk") == "bo@cam.ac.uk"


def test_apply_byline_stamps_the_title_slide():
    spec = [{"role": "title", "title": "T", "subtitle": "was"}, {"role": "content", "title": "C"}]
    gather.apply_byline(spec, "Ada, Bo")
    assert spec[0]["subtitle"] == "Ada, Bo"
    assert "subtitle" not in spec[1]                       # only the title slide is stamped


def test_apply_byline_puts_the_email_under_the_authors():
    """Layout 0 has a title and a subtitle and nothing else, so the contact address rides in the
    subtitle as a second paragraph — never appended to the author list itself."""
    spec = [{"role": "title", "title": "T", "subtitle": "was"}]
    gather.apply_byline(spec, "Ada, Bo", "ada@ucb.edu")
    assert spec[0]["subtitle"] == ["Ada, Bo", "ada@ucb.edu"]


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

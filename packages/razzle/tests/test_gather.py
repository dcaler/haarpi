"""razzle.gather + razzle.deck — pull real inputs from a project, then gather → compose → render.

Builds a fixture project (manifest, a one-pager on the chain, a findings.json, a figure pool) and
checks each puller, then the end-to-end orchestrator with a fake brain. The render half skips when the
neutral house master isn't present.
"""

from __future__ import annotations

import json

import pytest

from haarpi import figure, project
from razzle import assets, deck, gather

_HAS_MASTER = assets.master_pptx("default") is not None


def _fixture_project(root):
    project.save_manifest(project.Manifest(name="demo", short_title="demo", brief="x"), root)
    # a one-pager on the naming chain (a tool draft), in its canonical deliverable folder
    pout = root / "paper" / "onepager"
    pout.mkdir(parents=True)
    (pout / "260809_demo_onepager_ra.md").write_text("# The through-line\n\nFrictions and prices differ.")
    # the full manuscript (the SUBSTANCE) — venue-scoped, a tool draft on the chain
    mout = root / "paper" / "acorn2026" / "manuscript"
    mout.mkdir(parents=True)
    (mout / "260809_demo_acorn2026_ra.md").write_text(
        "# Full paper\n\nThe elasticity of matching is what the deck must present.")
    # rayleigh findings.json
    (root / "results").mkdir()
    (root / "results" / "findings.json").write_text(json.dumps({"experiments": [
        {"id": "E1", "question": "does friction bind?", "finding": "yes — 0.42 (95% CI 0.31–0.53)"}]}))
    # a figure in the pool
    figure.write_figure(root, "demo", figure.stage_dag(project.DEFAULT_STAGES), render_svg=False)


def test_gather_pulls_the_real_inputs(tmp_path):
    _fixture_project(tmp_path)
    b = gather.bundle(tmp_path)
    assert b["short_title"] == "demo"
    assert "Frictions and prices differ" in b["narrative"]        # the one-pager (spine)
    assert "elasticity of matching" in b["manuscript"]             # the full paper (substance)
    assert any(f["id"] == "stageLadder" for f in b["figures"])     # the pool
    assert "0.42 (95% CI 0.31–0.53)" in b["claims"]                # the real finding, verbatim
    assert isinstance(b["logos"], list)                            # no funders/affiliations → []
    assert b["title"] == "Full paper"                              # the PAPER's title, not invented


def test_the_title_slide_carries_the_papers_title(tmp_path):
    """A talk IS the paper, so its name is a fact to be read, not written. Left to the composer it
    invents one — and the invented title then propagates into the footer on every slide."""
    _fixture_project(tmp_path)
    assert (gather.paper_title("# A New Sense of Schelling Segregation\n\nbody")
            == "A New Sense of Schelling Segregation")
    # a deeper heading first means there is no title heading — do not walk on and grab a later one
    assert gather.paper_title("## a subsection first\n\n# not the title") == ""
    assert gather.paper_title("") == ""
    assert gather.talk_title(tmp_path) == "Full paper"

    spec = [{"role": "title", "title": "Something The Model Made Up"}, {"role": "content"}]
    gather.apply_title(spec, "Full paper")
    assert spec[0]["title"] == "Full paper"
    # and the running footer is built from it, so the invented one cannot leak downstream
    assert "Full paper" in gather.furniture(tmp_path, None, spec)["footer"]


def test_a_project_with_no_manuscript_falls_back_to_its_short_title(tmp_path):
    project.save_manifest(project.Manifest(name="demo", short_title="demo", brief="x"), tmp_path)
    assert gather.talk_title(tmp_path) == "demo"


def test_the_acknowledgements_slide_is_built_from_the_deck_config(tmp_path):
    """The closing thanks are facts — who funded this, whose marks go up — so razzle appends the
    slide itself and the composer never sees the role."""
    m = project.Manifest(name="d", short_title="demo", brief="x", deck_formats=["longtalk"],
                         decks={"longtalk": {"venue": "CSS2026", "funders": ["The Fund"]}})
    project.save_manifest(m, tmp_path)
    ack = gather.acknowledgements(tmp_path, "longtalk")
    assert ack["role"] == "acknowledgements" and ack["body"] == ["Supported by The Fund"]
    # no funders → the logo strip carries the slide on its own
    assert "body" not in gather.acknowledgements(tmp_path, "poster")


def test_appending_the_acknowledgements_slide_is_idempotent(tmp_path):
    """`spec.json` is re-rendered after hand edits, so a second render must refresh the closing
    slide rather than stack another one behind it."""
    ack = {"role": "acknowledgements", "title": "Acknowledgements"}
    spec = [{"role": "title", "title": "T"}, {"role": "content", "title": "C"}]
    once = gather.apply_acknowledgements(spec, ack)
    twice = gather.apply_acknowledgements(once, ack)
    assert [s["role"] for s in twice] == ["title", "content", "acknowledgements"]
    assert twice == once


class _Brain:
    def coordinator(self, prompt, system="", **kw):
        # a grounded reply that references the real figure id + a claim
        return json.dumps({"slides": [
            {"role": "title", "title": "Friction ≠ price", "subtitle": "authors"},
            # `caption` and `notes` are both dropped: the slot is citations-only and a deck has
            # no speaker notes — the fixture keeps emitting them so the drop stays pinned
            {"role": "figure", "title": "The pipeline", "figure": "stageLadder",
             "caption": "", "notes": "the ladder"},
            {"role": "content", "title": "Result", "bullets": ["yes — 0.42"]}]})


def test_build_deck_orchestrates_gather_compose_render(tmp_path):
    _fixture_project(tmp_path)
    got = deck.build_deck(tmp_path, "shorttalk", _Brain())
    # the spec is always written (the durable artifact)
    spec = json.loads((tmp_path / "slides" / "shorttalk" / "spec.json").read_text())
    assert spec[0]["role"] == "title" and spec[1]["figure"] == "stageLadder"
    assert spec[-1]["role"] == "acknowledgements"      # appended from the deck config, not authored
    if _HAS_MASTER:
        assert got["pptx"] is not None and got["pptx"].is_file()
        from pptx import Presentation
        assert len(Presentation(str(got["pptx"])).slides) == 4   # 3 authored + acknowledgements


def test_build_deck_rejects_an_unknown_format(tmp_path):
    _fixture_project(tmp_path)
    with pytest.raises(ValueError):
        deck.build_deck(tmp_path, "keynote", _Brain())


def test_the_deck_lives_under_its_venue_not_its_format(tmp_path):
    """`slides/CSS2026/`, the way a manuscript lives in `paper/css2026/`. The venue is what the
    deck is FOR and what anyone browses by; the format is a property of the talk."""
    from haarpi import project
    from razzle import gather
    m = project.Manifest(name="d", short_title="demo", brief="x",
                         deck_formats=["longtalk"],
                         decks={"longtalk": {"venue": "CSS2026", "date": "31 Oct"}})
    project.save_manifest(m, tmp_path)

    assert gather.deck_dir(tmp_path, "longtalk") == tmp_path / "slides" / "CSS2026"
    assert gather.format_for_venue(tmp_path, "CSS2026") == "longtalk"
    assert gather.format_for_venue(tmp_path, "  css2026 ") == "longtalk"   # case/whitespace
    assert gather.format_for_venue(tmp_path, "NeurIPS") == ""


def test_a_deck_with_no_venue_yet_still_lands_somewhere(tmp_path):
    """A deck can be authored before the interview has named a venue — it must not land in
    `slides/` itself, where it would collide with every other deck."""
    from haarpi import project
    from razzle import gather
    project.save_manifest(project.Manifest(name="d", short_title="demo", brief="x"), tmp_path)
    assert gather.deck_dir(tmp_path, "poster") == tmp_path / "slides" / "poster"

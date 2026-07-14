"""A venue is a facet of the DELIVERABLE, not of the project.

The one-pager is the narrative and belongs to the work. An outline and a manuscript are
written FOR somewhere — its length, its columns, what it will publish — so a project may
carry several, and the conference→journal flow means one may descend from another.

The venue rides in the naming chain as one more token, which is why none of the gate,
redline or release machinery had to learn about venues at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from raconteur import naming as n
from raconteur.config import ProjectConfig, VenueConfig, venue_slug


# ── slugs: the token that will sit in every filename ─────────────────────────

@pytest.mark.parametrize("name,slug", [
    ("ISMIR", "ismir"),
    ("International Society for Music Information Retrieval (ISMIR)", "ismir"),
    ("Journal of Artificial Societies and Social Simulation", "jasss"),
    ("Computer Music Journal", "cmj"),
    ("NIME 2027", "nime"),                      # its own acronym beats initials ("n2")
])
def test_venue_slug(name, slug):
    assert venue_slug(name) == slug


def test_a_slug_never_breaks_the_chain():
    """The chain splits on underscores and dots end the filename."""
    for name in ("Foo_Bar Journal", "A.C.M. Multimedia", "NIME 2027"):
        s = venue_slug(name)
        assert "_" not in s and "." not in s and s == s.lower()


# ── naming: the venue is part of which document this IS ──────────────────────

def test_the_onepager_has_no_venue():
    """The narrative belongs to the work, not to whoever might publish it."""
    assert n.major_onepager_name("Chords", "docx") == f"{n.today()}_Chords_onepager_ra.docx"


def test_each_venue_gets_its_own_outline_and_manuscript():
    assert n.major_outline_name("Chords", "docx", venue="ismir").endswith(
        "_Chords_ismir_outline_ra.docx")
    assert n.major_name("Chords", "docx", venue="jasss").endswith("_Chords_jasss_ra.docx")


def test_a_release_keeps_its_venue_and_loses_its_authors():
    rel = n.release_name("Chords", "docx", venue="ismir")
    chain = n.parse(Path(rel), "Chords")[1]
    assert chain == ["ismir"]
    assert n.is_release(chain), "nobody's turn"


@pytest.mark.parametrize("fname,venue", [
    ("260714_Chords_ismir_outline_ra_DCR.docx", "ismir"),
    ("260714_Chords_ismir_ra_DCR.docx", "ismir"),
    ("260714_Chords_jasss.docx", "jasss"),
    ("260714_Chords_onepager_ra.docx", ""),          # shared
    ("260714_Chords_ra_DCR.docx", ""),               # the venue-less manuscript
])
def test_venue_of_reads_it_back_out(fname, venue):
    assert n.venue_of(Path(fname), "Chords") == venue


def test_two_venues_do_not_collide(tmp_path):
    """The point of the whole exercise: ISMIR's outline and JASSS's sit side by side, and
    each one's redline finds its own."""
    for f in ("260714_Chords_ismir_outline_ra_DCR.docx",
              "260714_Chords_jasss_outline_ra.docx",
              "260714_Chords_ismir.docx"):
        (tmp_path / f).touch()

    ismir = n.find_user_revision(tmp_path, "Chords", chain_includes=["ismir", "outline"])
    assert ismir.name == "260714_Chords_ismir_outline_ra_DCR.docx"
    # JASSS's outline is the tool's turn — no markup awaiting an answer
    assert n.find_user_revision(tmp_path, "Chords", chain_includes=["jasss", "outline"]) is None


def test_a_release_is_never_mistaken_for_markup(tmp_path):
    """`260714_Chords_ismir.docx` ends in "ismir", which is not "ra" and is emphatically
    not a reviewer's initials."""
    (tmp_path / "260714_Chords_ismir.docx").touch()
    assert n.find_user_revision(tmp_path, "Chords", chain_includes="ismir") is None


# ── config: the venues map ───────────────────────────────────────────────────

def _write(tmp_path, data: dict) -> Path:
    (tmp_path / "paper").mkdir(exist_ok=True)
    (tmp_path / "paper" / "raconteur.yaml").write_text(yaml.safe_dump(data))
    return tmp_path


def test_the_old_single_venue_block_still_loads(tmp_path):
    """Every project alive today has one. A project that named a venue meant to write for
    it, so it loads as SELECTED."""
    _write(tmp_path, {"short_title": "Chords",
                      "venue": {"name": "ISMIR", "page_limit": 8}})
    cfg = ProjectConfig.load(tmp_path)
    assert list(cfg.venues) == ["ismir"]
    assert cfg.venue("ismir").page_limit == 8
    assert cfg.selected_venues() == ["ismir"]


def test_several_venues_and_the_default(tmp_path):
    _write(tmp_path, {"short_title": "Chords", "venues": {
        "ismir": {"name": "ISMIR", "status": "selected"},
        "jasss": {"name": "JASSS", "status": "candidate"},
    }})
    cfg = ProjectConfig.load(tmp_path)
    assert cfg.selected_venues() == ["ismir"]
    assert cfg.default_venue() == "ismir"


def test_two_selected_venues_have_no_default(tmp_path):
    """Writing the ISMIR paper when the author meant the JASSS one wastes a cycle and is
    not obvious from the output — so the caller must ask."""
    _write(tmp_path, {"short_title": "Chords", "venues": {
        "ismir": {"name": "ISMIR", "status": "selected"},
        "jasss": {"name": "JASSS", "status": "selected"},
    }})
    assert ProjectConfig.load(tmp_path).default_venue() is None


def test_a_venue_the_author_declared_says_so(tmp_path):
    _write(tmp_path, {"short_title": "Chords", "venues": {
        "nime": {"name": "NIME 2027", "status": "selected", "origin": "author",
                 "url": "https://nime.org/cfp"},
    }})
    v = ProjectConfig.load(tmp_path).venue("nime")
    assert v.by_author and v.url


def test_a_spec_never_passes_as_a_fact_when_it_is_a_guess():
    """"Page limit: 8" invented from the tool's own prose reads exactly like "Page limit: 8"
    off the CFP, and only one of them is true."""
    v = VenueConfig(name="X", page_limit=8, sources={"page_limit": "cfp"})
    assert "from the call for papers" in v.spec_line("page_limit")

    guessed = VenueConfig(name="X", page_limit=8, sources={"page_limit": "analysis"})
    assert "UNVERIFIED" in guessed.spec_line("page_limit")

    unknown = VenueConfig(name="X")
    assert "unknown" in unknown.spec_line("page_limit")
    assert "do not assume" in unknown.spec_line("page_limit")

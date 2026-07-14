"""The slate: how the author says where the paper is going.

raconteur's brainstorm will not always find the venue you have in mind. When it doesn't, you
add a row — and that row is a decision, not a suggestion. The tool may propose candidates
beside it; it may never touch, downgrade, or delete it.
"""

from __future__ import annotations

import types

from raconteur import slate
from raconteur.config import VenueConfig

ANALYSIS = """\
## Recommendation
**Primary target: ISMIR.**

## Venue slate

Set `status` to selected for each venue you intend to write for.

| slug | venue | kind | status | source |
|------|-------|------|--------|--------|
| ismir | ISMIR | conference | candidate | (raconteur) |
| jasss | JASSS | journal | selected | (raconteur) |
| nime | NIME 2027 | conference | selected | https://nime.org/2027/cfp |
"""


def test_the_slate_is_read_back_out_of_the_analysis():
    got = slate.parse(ANALYSIS)
    assert set(got) == {"ismir", "jasss", "nime"}
    assert got["jasss"]["status"] == "selected"
    assert got["ismir"]["status"] == "candidate"
    assert got["nime"]["url"] == "https://nime.org/2027/cfp"
    assert got["nime"]["kind"] == "conference"


def test_a_venue_the_tool_never_proposed_is_the_authors_own():
    """The case that has to work: raconteur's brainstorm missed NIME, so the author typed
    it in."""
    venues = {"ismir": VenueConfig(name="ISMIR", origin="raconteur")}
    merged = slate.merge(venues, slate.parse(ANALYSIS))
    assert merged["nime"].by_author
    assert merged["nime"].status == "selected"
    assert merged["nime"].url == "https://nime.org/2027/cfp"


def test_the_slate_moves_a_status_and_nothing_else():
    """It is a decision surface, not a config editor: it may not overwrite a spec."""
    venues = {"jasss": VenueConfig(name="JASSS", status="candidate", page_limit=20,
                                   sources={"page_limit": "cfp"})}
    merged = slate.merge(venues, slate.parse(ANALYSIS))
    assert merged["jasss"].status == "selected"
    assert merged["jasss"].page_limit == 20, "the spec is untouched"
    assert merged["jasss"].sources["page_limit"] == "cfp"


def test_a_row_the_author_struck_is_reported_not_obeyed_silently():
    venues = {"ismir": VenueConfig(name="ISMIR"), "cmj": VenueConfig(name="CMJ")}
    assert slate.dropped(venues, slate.parse(ANALYSIS)) == ["cmj"]


def test_no_slate_means_no_change():
    venues = {"ismir": VenueConfig(name="ISMIR", status="selected")}
    assert slate.parse("## Recommendation\nNo table here.") == {}
    assert slate.merge(venues, {})["ismir"].status == "selected"


def test_a_bolded_cell_from_word_still_parses():
    """The author edits this table in Word; pandoc brings bold and stray spaces back."""
    got = slate.parse("## Venue slate\n\n| slug | venue | status |\n|--|--|--|\n"
                      "| **nime** | *NIME 2027* |  **selected**  |\n")
    assert got["nime"]["status"] == "selected"


def test_the_slate_renders_what_the_author_will_edit():
    text = slate.render({
        "ismir": VenueConfig(name="ISMIR", kind="conference"),
        "nime": VenueConfig(name="NIME 2027", status="selected", origin="author",
                            url="https://nime.org/cfp"),
    })
    assert slate.SLATE_HEADING in text
    assert "| ismir | ISMIR | conference | candidate |" in text
    assert "https://nime.org/cfp" in text
    assert slate.parse(text)["nime"]["status"] == "selected", "round-trips"


# ── the specs come from the venue, or they are unknown ───────────────────────

def test_a_venue_with_no_url_keeps_an_unknown_format():
    v = VenueConfig(name="NIME 2027")
    out = slate.fetch_specs(v, brain=None)
    assert out.page_limit is None and not out.sources


def test_the_cfp_is_the_source_and_says_so(monkeypatch):
    import raconteur.web as web
    monkeypatch.setattr(web, "fetch_page_text",
                        lambda url, email, max_chars=6000: "Papers may be up to 8 pages.")

    brain = types.SimpleNamespace(
        coordinator=lambda *a, **k: '{"page_limit": 8, "columns": 2}')
    v = VenueConfig(name="ISMIR", url="https://ismir.net/cfp")
    out = slate.fetch_specs(v, brain)

    assert out.page_limit == 8 and out.columns == 2
    assert out.sources["page_limit"] == "cfp"
    assert "from the call for papers" in out.spec_line("page_limit")


def test_an_unreadable_cfp_leaves_the_format_unknown(monkeypatch):
    import raconteur.web as web
    monkeypatch.setattr(web, "fetch_page_text", lambda *a, **k: "")
    v = VenueConfig(name="X", url="https://example.invalid/cfp")
    out = slate.fetch_specs(v, brain=None)
    assert out.page_limit is None and not out.sources


def test_the_writer_is_warned_when_nothing_was_verified():
    block = slate.specs_block(VenueConfig(name="NIME 2027", status="selected"))
    assert "unknown" in block
    assert "none of this venue's format has been read" in block


def test_a_verified_spec_carries_no_warning():
    v = VenueConfig(name="ISMIR", page_limit=8, sources={"page_limit": "cfp"})
    block = slate.specs_block(v)
    assert "from the call for papers" in block
    assert "not been read" not in block


# ── resolving which venue a verb is for ──────────────────────────────────────

def _cfg(venues):
    from raconteur.config import ProjectConfig
    return ProjectConfig(short_title="Chords", venues=venues)


def test_one_selected_venue_needs_no_flag():
    cfg = _cfg({"ismir": VenueConfig(name="ISMIR", status="selected")})
    assert slate.resolve(cfg) == "ismir"


def test_several_selected_venues_must_be_disambiguated():
    cfg = _cfg({"ismir": VenueConfig(name="ISMIR", status="selected"),
                "jasss": VenueConfig(name="JASSS", status="selected")})
    with __import__("pytest").raises(SystemExit):
        slate.resolve(cfg)
    assert slate.resolve(cfg, "jasss") == "jasss"


def test_no_venue_chosen_is_not_an_error():
    """A project that has not reached the venue analysis still writes."""
    assert slate.resolve(_cfg({})) == ""


def test_an_unknown_venue_is_refused():
    cfg = _cfg({"ismir": VenueConfig(name="ISMIR", status="selected")})
    with __import__("pytest").raises(SystemExit):
        slate.resolve(cfg, "nosuchvenue")

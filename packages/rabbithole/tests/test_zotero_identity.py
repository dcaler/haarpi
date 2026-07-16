"""A paper the user already filed must never be listed as a candidate again.

Pins the 2026-07-14 consGateII gather failure: Guagnano 1995 had been in the
project's Zotero collection since Jun 30 — saved without a DOI and with its
full subtitle — while the Crossref candidate arrived with the DOI but a
subtitle-less title. Neither identity key could meet, so the tool asked the
human to go collect a paper they already had.

Two independent fixes, both pinned here:
  * sources._full_title  — Crossref splits "Title: Subtitle" across two fields;
    join them back so candidate titles match Zotero's convention exactly.
  * discover._ZoteroIndex — a subtitle-tolerant channel that compares one
    side's pre-colon stem against the other side's whole title (never stem vs
    stem), corroborated by first-author family + year.
"""

from rabbithole.discover import _ZoteroIndex, _title_stem
from rabbithole.models import Author, Candidate
from rabbithole.sources import _full_title


def _zotero_item(title: str, doi: str = "", family: str = "", date: str = "") -> dict:
    return {"data": {"title": title, "DOI": doi,
                     "creators": [{"lastName": family}] if family else [],
                     "date": date}}


GUAGNANO_ZOTERO = _zotero_item(
    "Influences on Attitude-Behavior Relationships: "
    "A Natural Experiment with Curbside Recycling",
    doi="", family="Guagnano", date="1995")

GUAGNANO_CROSSREF = Candidate(
    title="Influences on Attitude-Behavior Relationships",
    doi="10.1177/0013916595275005",
    authors=[Author(family="Guagnano", given="Gregory A.")],
    year=1995)


class TestTheGuagnanoMiss:
    def test_a_bare_doi_record_with_a_subtitle_is_still_recognised(self):
        idx = _ZoteroIndex()
        idx.add(GUAGNANO_ZOTERO)
        assert idx.find(GUAGNANO_CROSSREF) is GUAGNANO_ZOTERO

    def test_the_reverse_direction_matches_too(self):
        # Zotero record saved short; the source carries the subtitle.
        idx = _ZoteroIndex()
        short = _zotero_item("Influences on Attitude-Behavior Relationships",
                             family="Guagnano", date="1995")
        idx.add(short)
        long_cand = Candidate(
            title="Influences on Attitude-Behavior Relationships: "
                  "A Natural Experiment with Curbside Recycling",
            authors=[Author(family="Guagnano")], year=1995)
        assert idx.find(long_cand) is short

    def test_doi_still_wins_outright(self):
        idx = _ZoteroIndex()
        it = _zotero_item("Any Title At All", doi="10.1177/0013916595275005")
        idx.add(it)
        assert idx.find(GUAGNANO_CROSSREF) is it

    def test_exact_title_still_wins_without_author_or_year(self):
        idx = _ZoteroIndex()
        it = _zotero_item("Influences on Attitude-Behavior Relationships")
        idx.add(it)
        c = Candidate(title="Influences on Attitude-Behavior Relationships")
        assert idx.find(c) is it


class TestTheStemChannelStaysNarrow:
    def test_two_subtitled_papers_sharing_a_stem_never_collide(self):
        # "…: Part I" filed; "…: Part II" must still be listed as a candidate.
        idx = _ZoteroIndex()
        idx.add(_zotero_item("Recycling behavior in dense housing: Part I",
                             family="Smith", date="2020"))
        part2 = Candidate(title="Recycling behavior in dense housing: Part II",
                          authors=[Author(family="Smith")], year=2020)
        assert idx.find(part2) is None

    def test_a_stem_hit_requires_the_author_to_agree(self):
        idx = _ZoteroIndex()
        idx.add(GUAGNANO_ZOTERO)
        other = Candidate(title="Influences on Attitude-Behavior Relationships",
                          authors=[Author(family="Stern")], year=1995)
        assert idx.find(other) is None

    def test_a_stem_hit_requires_the_year_to_agree(self):
        idx = _ZoteroIndex()
        idx.add(GUAGNANO_ZOTERO)
        other = Candidate(title="Influences on Attitude-Behavior Relationships",
                          authors=[Author(family="Guagnano")], year=2005)
        assert idx.find(other) is None

    def test_short_stems_are_too_generic_to_spend(self):
        assert _title_stem("Nudging: a review") == ""
        assert _title_stem("Who recycles and when? A review") != ""

    def test_no_authors_no_stem_match(self):
        idx = _ZoteroIndex()
        idx.add(GUAGNANO_ZOTERO)
        anon = Candidate(title="Influences on Attitude-Behavior Relationships",
                         year=1995)
        assert idx.find(anon) is None


class TestCrossrefSubtitleJoin:
    def test_subtitle_rejoins_the_title(self):
        assert _full_title("Influences on Attitude-Behavior Relationships",
                           "A Natural Experiment with Curbside Recycling") == \
            ("Influences on Attitude-Behavior Relationships: "
             "A Natural Experiment with Curbside Recycling")

    def test_a_subtitle_already_present_is_not_doubled(self):
        t = "Please sort out your rubbish! An integrated structural model approach"
        assert _full_title(t, "An integrated structural model approach") == t

    def test_empty_subtitle_is_a_no_op(self):
        assert _full_title("A Title", "") == "A Title"
        assert _full_title("A Title", None) == "A Title"

    def test_trailing_colon_is_not_doubled(self):
        assert _full_title("A Title:", "The Subtitle") == "A Title: The Subtitle"

"""Citations in a minted review must resolve, and one paper must be one row.

Both defects come from elephantRoom's 2026-09-18 revise (task 1052), whose delivered .docx
cited eight keys its own refs.bib never defined — ~7% of everything it cited — and whose
corpus held three papers twice.

The citekeys were not random. A model asked to cite from a list writes them from memory, and
six of the eight were mangled versions of real keys: a dropped year, or extra title words.
`unresolved_keys` was already reporting them to the model every polish round; after the last
round whatever it had not fixed simply shipped. Asking is not a mechanism.
"""
import pytest

from rabbithole import ledger
from rabbithole.corpus import dedupe_corpus
from rabbithole.models import Author, Candidate

# verbatim from that run's refs.bib
KNOWN = {"amendolaEnergy2024", "clausingCarbon2023", "coroneseAgriLOVE2021",
         "hémousDirected2021", "hoekstraCreating2017", "rengsEvolutionary2020",
         "climate2022", "grimaudClimate2011", "grimaudTechnology2025", "dosiMicro2017"}


def test_a_dropped_year_is_repaired():
    fixes, _ = ledger.repair_map({"amendolaEnergy", "clausingCarbon"}, KNOWN)
    assert fixes == {"amendolaEnergy": "amendolaEnergy2024",
                     "clausingCarbon": "clausingCarbon2023"}


def test_extra_title_words_are_repaired():
    """`hoekstraCreating2017` is 'Creating Agent-Based Energy Transition Management Models';
    the narrative wrote the title out further than Better BibTeX did."""
    fixes, _ = ledger.repair_map(
        {"hoekstraCreatingAgentBasedEnergy2017",
         "rengsEvolutionaryMacroeconomicAssessment2020"}, KNOWN)
    assert fixes == {"hoekstraCreatingAgentBasedEnergy2017": "hoekstraCreating2017",
                     "rengsEvolutionaryMacroeconomicAssessment2020": "rengsEvolutionary2020"}


def test_a_key_written_from_the_title_is_repaired():
    """`climate2022` is 'Beyond climate economics orthodoxy…'."""
    fixes, _ = ledger.repair_map({"ClimateEconomicsOrthodoxy2022"}, KNOWN)
    assert fixes == {"ClimateEconomicsOrthodoxy2022": "climate2022"}


def test_a_fabricated_key_is_reported_not_invented_away():
    """`grimaudCESifo` matched nothing. Two grimaud keys exist and neither is it — a person
    has to decide what that sentence rested on."""
    fixes, unmatched = ledger.repair_map({"grimaudCESifo"}, KNOWN)
    assert fixes == {}
    assert unmatched == ["grimaudCESifo"]


def test_an_ambiguous_key_is_never_silently_rewritten():
    """The reason `near_miss_keys` reports rather than rewrites: two real papers by one author
    can share a stem, and pointing a citation at the wrong one is worse than a broken one."""
    known = {"smithGrowth2019", "smithGrowth2021"}
    fixes, unmatched = ledger.repair_map({"smithGrowth"}, known)
    assert fixes == {}, "two candidates is not a repair"
    assert unmatched == []


def test_a_year_mismatch_blocks_a_prefix_match():
    """Without the year check, `grimaudClimate2011` would capture a 2019 paper."""
    fixes, _ = ledger.repair_map({"grimaudClimateChange2019"}, KNOWN)
    assert fixes == {}


def test_repairs_rewrite_the_tag_and_nothing_else():
    text = "as shown [@amendolaEnergy] — note amendolaEnergy is not a tag here."
    out = ledger.apply_repairs(text, {"amendolaEnergy": "amendolaEnergy2024"})
    assert "[@amendolaEnergy2024]" in out
    assert "note amendolaEnergy is not a tag" in out, "bare prose is untouched"


def test_a_known_key_is_left_alone():
    fixes, unmatched = ledger.repair_map({"dosiMicro2017"}, KNOWN)
    assert fixes == {} and unmatched == []


# ── one paper, one row ─────────────────────────────────────────────────────
def _c(title, last, year, doi="", fulltext="", pdf=""):
    return Candidate(title=title, authors=[Author(family=last)], year=year, doi=doi,
                     fulltext=fulltext, pdf_path=pdf)


def test_the_same_paper_twice_becomes_one_row():
    """Branger 2014 was in elephantRoom's collection under two Zotero items, so both took
    [@brangerWould2014a] and which one the bibliography printed was arbitrary."""
    t = "Would border carbon adjustments prevent carbon leakage"
    out = dedupe_corpus([_c(t, "Branger", 2014, doi="10.1016/j.ecolecon.2013.12.010"),
                         _c(t, "Branger", 2014, doi="10.1016/j.ecolecon.2013.12.010")])
    assert len(out) == 1


def test_the_readable_copy_survives_not_the_metadata_rich_one():
    """`filters.dedupe`'s richness score knows nothing about full text, so it could keep the
    twin that cannot be read. That is why this is not that function."""
    t = "Unilateral climate policy and foreign direct investment"
    rich = _c(t, "Sanna-Randaccio", 2014)
    rich.abstract = "a" * 500
    readable = _c(t, "Sanna-Randaccio", 2014, fulltext="the whole paper", pdf="/p/x.pdf")
    for order in ([rich, readable], [readable, rich]):
        (kept,) = dedupe_corpus(list(order))
        assert kept.fulltext == "the whole paper"
        assert kept.abstract == "a" * 500, "and it keeps what the discarded twin had"


def test_different_papers_are_not_merged():
    out = dedupe_corpus([_c("Carbon leakage", "Branger", 2014, doi="10.1/a"),
                         _c("Climate clubs", "Nordhaus", 2015, doi="10.1/b")])
    assert len(out) == 2


def test_a_record_with_no_identity_is_kept():
    """No DOI and no title is not evidence of duplication."""
    out = dedupe_corpus([_c("", "", None), _c("", "", None)])
    assert len(out) == 2


def test_order_is_preserved():
    out = dedupe_corpus([_c("A", "X", 2000, doi="10.1/a"), _c("B", "Y", 2001, doi="10.1/b"),
                         _c("A", "X", 2000, doi="10.1/a")])
    assert [c.title for c in out] == ["A", "B"]

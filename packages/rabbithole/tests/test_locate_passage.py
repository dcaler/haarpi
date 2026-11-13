"""locate_passage: tolerant in finding a quote, never in what it hands on (2026-11-09)."""
from __future__ import annotations

from rabbithole import facts

PAPER = (
    "[p.1]\nIntroduction. Firms learn by search. We study how search distance changes.\n"
    "[p.2]\nResults. The optimal search distance typically de-\ncreases as ﬁtness increases, "
    "which we show in Figure 7. A second finding is unrelated to the first one here.\n\n"
    "[p.3]\nConclusion. Long jumps pay only early on, when fitness is still low across the landscape."
)


def test_an_exact_quote_comes_back_as_the_whole_sentence_in_the_papers_words():
    r = facts.locate_passage("optimal search distance typically decreases as fitness increases", PAPER)
    assert r["found"]
    [p] = r["passages"]
    assert p["page"] == 2
    assert p["text"].startswith("The optimal search distance typically decreases as fitness")
    assert p["text"].endswith("in Figure 7.")


def test_a_tidied_quote_is_found_but_the_papers_words_are_what_is_handed_on():
    # One word changed: found at 0.9 tolerance, and the passage says "decreases", not the model's word.
    r = facts.locate_passage("The optimal search distance typically increases as fitness increases, "
                             "which we show in Figure 7", PAPER)
    assert r["found"]
    assert "decreases as fitness increases" in r["passages"][0]["text"]
    assert facts.locate_passage("The optimal search distance typically increases as fitness increases, "
                                "which we show in Figure 7", PAPER, min_share=1.0)["found"] is False


def test_an_elided_quote_locates_each_piece_and_may_span_pages():
    r = facts.locate_passage("Firms learn by search. We study how search distance changes ... "
                             "Long jumps pay only early on, when fitness is still low", PAPER)
    assert r["found"]
    assert [p["page"] for p in r["passages"]] == [1, 3]


def test_an_invented_quote_is_not_found_and_its_nearest_match_is_kept_for_repair():
    r = facts.locate_passage("Firms that search widely always outperform those that search locally", PAPER)
    assert r["found"] is False
    assert r["passages"] == []
    assert len(r["nearest"]) == 1 and r["nearest"][0]["share"] < 0.9


def test_a_scrap_too_short_to_prove_anything_is_not_found():
    assert facts.locate_passage("search distance", PAPER)["found"] is False
    assert facts.locate_passage("", PAPER)["found"] is False


def test_words_run_together_by_a_scan_still_locate():
    scanned = "[p.4]\nEarly radical and bushyradiation should be followed by eventual stasis. Next."
    r = facts.locate_passage("Early radical and bushy radiation should be followed by eventual stasis", scanned,
                             min_share=1.0)
    assert r["found"]
    assert r["passages"][0]["text"] == "Early radical and bushyradiation should be followed by eventual stasis."


IPCC = ("[p.7]\nThere is very high confidence that these losses are \nmainly from the northern Antarctic "
        "Peninsula and the Amundsen Sea sector of West Antarctica. {4.4}\n•\t\nThe annual mean Arctic sea "
        "ice extent decreased over the period 1979 to 2012 with a rate that was very likely in the \n"
        "range 3.5 to 4.1% per decade (range of 0.45 to 0.51 million km² per decade). {4.2}\n•\t\n"
        "The extent of Arctic sea ice has decreased in every season.")


def test_a_bullet_ends_the_previous_item_so_the_passage_does_not_reach_back_into_it():
    r = facts.locate_passage("The annual mean Arctic sea ice extent decreased over the period 1979 to 2012", IPCC)
    assert r["found"]
    [p] = r["passages"]
    assert p["text"].startswith("The annual mean Arctic sea ice extent")
    assert "Antarctica" not in p["text"] and "every season" not in p["text"]


def test_a_braced_section_reference_stays_with_its_sentence():
    paper = "[p.1]\nFirms learn by search. {2.1} The optimal distance falls as fitness rises over time here."
    [p] = facts.locate_passage("The optimal distance falls as fitness rises over time", paper)["passages"]
    assert p["text"].startswith("The optimal distance falls")


def test_a_units_exponent_survives_into_the_passage():
    [p] = facts.locate_passage("range of 0.45 to 0.51 million km2 per decade", IPCC)["passages"]
    assert "million km² per decade" in p["text"]


RASWANT = ("[p.16]\nThrough such partnerships MNEs can play a critical role in mitigating contention over "
           "Space resources and supporting\n17b. Open and equal access and benefits via OST\n18. Sustainable "
           "Space (NEW)\n18b. Driver and contributor to all SDGs\n\n\n[p.17]\nthe development of an "
           "institutional framework that balances \nsocial responsibility, global cooperation, environmental "
           "sustainability, and international security. With Space law still evolving, MNEs can engage in "
           "public-private partnerships.")


def test_table_rows_before_a_page_break_are_not_part_of_the_passage():
    # The tolerant matcher can start a passage in the table that interrupts a sentence.
    r = facts.locate_passage("Driver and contributor to all SDGs the development of an institutional "
                             "framework that balances social responsibility", RASWANT)
    [p] = r["passages"]
    assert "SDGs" not in p["text"] and "18b" not in p["text"]
    assert p["text"].startswith("…the development of an institutional framework")
    assert p["page"] == 17 and p["pages"] == [17, 17]


def test_prose_before_a_page_break_is_kept_and_the_pages_are_a_range():
    paper = ("[p.4]\nIn the first period of the study the market for storage grew quickly and\n\n"
             "[p.5]\nfirms entered it from adjacent sectors, which is the pattern we report here.")
    [p] = facts.locate_passage("the market for storage grew quickly and firms entered it from adjacent sectors",
                               paper)["passages"]
    assert p["text"].startswith("In the first period") and p["pages"] == [4, 5]

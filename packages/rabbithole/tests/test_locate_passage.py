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

"""Raised citation numbers stay in their sentence (2026-11-09: de Visser & Krug lost all 108)."""
from __future__ import annotations

import fitz
import pytest

from rabbithole import facts, pdfs


def _page(lines: list[tuple[float, str, float, float]]):
    """A one-page PDF: (x, text, fontsize, baseline y) per piece."""
    doc = fitz.open()
    page = doc.new_page(width=600, height=400)
    for x, text, size, y in lines:
        page.insert_text((x, y), text, fontsize=size, fontname="helv")
    return doc


def _x_after(text: str, x: float, size: float) -> float:
    return x + fitz.get_text_length(text, fontname="helv", fontsize=size)


def test_a_raised_reference_number_is_kept_where_it_sits():
    body = "stronger ruggedness than mutations with a collective benefit"
    doc = _page([(40, body, 12, 100), (_x_after(body, 40, 12), "44", 8, 95),
                 (40, "and a second line follows here.", 12, 118)])
    text = pdfs._page_text(doc[0])
    assert "collective benefit[44]" in text
    assert "\n44\n" not in text


def test_a_run_of_numbers_is_one_citation():
    body = "consistent with predictions from various models"
    x = _x_after(body, 40, 12)
    doc = _page([(40, body, 12, 100), (x, "46,47,48", 8, 95), (40, "next line of text.", 12, 118)])
    assert "models[46,47,48]" in pdfs._page_text(doc[0])


def test_an_exponent_after_a_digit_is_left_alone():
    body = "a population of 10"
    doc = _page([(40, body, 12, 100), (_x_after(body, 40, 12), "6", 8, 95), (40, "cells grew.", 12, 118)])
    assert "[6]" not in pdfs._page_text(doc[0])


def test_in_an_author_year_paper_raised_numbers_are_notes():
    body = "the problem of transitions was framed this way"
    cites = [(40, "; ".join(f"(Smith {1990 + 4 * r + i})" for i in range(4)), 12, 200 + 18 * r)
             for r in range(4)]
    doc = _page([(40, body, 12, 100), (_x_after(body, 40, 12), "3", 8, 95)] + cites)
    assert "framed this way[note:3]" in "".join(pdfs._doc_pages(doc))


def test_the_matchers_ignore_the_markers():
    marked = "[p.2]\nstronger ruggedness than mutations with a collective benefit[44]. This contrast holds."
    assert facts.find_quote("ruggedness than mutations with a collective benefit. This contrast",
                            facts.page_texts(marked)) == 2
    r = facts.locate_passage("ruggedness than mutations with a collective benefit", marked, min_share=1.0)
    assert r["found"] and "benefit[44]" in r["passages"][0]["text"]


def test_a_raised_2_after_a_unit_is_its_exponent_not_a_citation():
    body = "range of 0.45 to 0.51 million km"
    doc = _page([(40, body, 12, 100), (_x_after(body, 40, 12), "2", 8, 95),
                 (_x_after(body + "2", 40, 12), " per decade", 12, 100), (40, "next line.", 12, 118)])
    text = pdfs._page_text(doc[0])
    assert "million km²" in text and "[2]" not in text


def test_a_raised_number_after_an_ordinary_word_is_still_a_citation():
    body = "as shown for them"
    doc = _page([(40, body, 12, 100), (_x_after(body, 40, 12), "2", 8, 95), (40, "next line.", 12, 118)])
    assert "them[2]" in pdfs._page_text(doc[0])


def test_running_headers_footers_and_page_numbers_are_dropped():
    words = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot"]
    body = lambda w: "\n".join(f"{w} sentence {k} of the body, set as prose." for k in range(8))
    pages = [f"{580 + i}\nJournal of International Business Studies (2025) 56:567–588\n{body(w)}\nFooter Ltd. {i}"
             for i, w in enumerate(words)]
    out = pdfs._drop_furniture(pages)
    assert all("Journal of International" not in pg and not pg.startswith("58") for pg in out)
    assert all(f"{w} sentence 0 of the body" in pg and f"{w} sentence 7 of the body" in pg
               for w, pg in zip(words, out))


def test_a_short_paper_keeps_its_lines():
    pages = ["Title line\nBody one.", "Title line\nBody two."]
    assert pdfs._drop_furniture(pages) == pages

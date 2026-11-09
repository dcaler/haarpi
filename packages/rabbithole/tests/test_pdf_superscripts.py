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

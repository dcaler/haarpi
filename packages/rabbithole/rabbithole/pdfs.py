"""PDF helpers: extract text and verify it's real full text."""

from __future__ import annotations

import re
from pathlib import Path


def extract_text(path: Path) -> tuple[str, int]:
    """Return (text, n_pages). Empty text on failure."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return "", 0
    try:
        doc = fitz.open(path)
    except Exception:  # noqa: BLE001
        return "", 0
    pages = [p.get_text() for p in doc]
    n = doc.page_count
    doc.close()
    return "\n".join(pages), n


def page_marked_text(path: Path) -> str:
    """Full text with [p.N] markers so the LLM can give page-level pointers."""
    try:
        import fitz
        doc = fitz.open(path)
    except Exception:  # noqa: BLE001
        return ""
    out = []
    for i, page in enumerate(doc, 1):
        out.append(f"[p.{i}]\n{page.get_text()}")
    doc.close()
    return "\n\n".join(out)


# What this gate is FOR: rejecting an abstract-only or preview PDF, where we got a
# teaser instead of the paper. It is a capability check, not a source policy — policy
# lives at `gather` (filters.item_type_allowed), and anything a person filed in the
# collection is theirs to decide. So this must not become a way of excluding short
# work: a genuine 3-page paper is a paper.
#
# The REFERENCES SECTION is the discriminator doing the real work. A preview stops
# before the bibliography; a complete short paper carries one. The word count beside it
# is only a floor against a bare abstract that happens to say "references" in passing,
# so it wants to sit above abstract length (150–300 words) and nowhere near paper
# length. It was 600, which rejected a fully-extracted 3-page JASSS corrigendum at 564
# words — a 36-word miss that had nothing to do with whether we had the whole document.
SHORT_PAPER_MIN_WORDS = 400


def looks_like_fulltext(text: str, n_pages: int) -> bool:
    """Heuristic: reject abstract-only / preview PDFs."""
    words = len(text.split())
    if words >= 1500 or n_pages >= 4:
        return True
    # short but has a references section -> probably a short full paper
    if words >= SHORT_PAPER_MIN_WORDS and re.search(r"\b(references|bibliography)\b",
                                                    text, re.I):
        return True
    return False

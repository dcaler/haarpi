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


def looks_like_fulltext(text: str, n_pages: int) -> bool:
    """Heuristic: reject abstract-only / preview PDFs."""
    words = len(text.split())
    if words >= 1500 or n_pages >= 4:
        return True
    # short but has a references section -> probably a short full paper
    if words >= 600 and re.search(r"\b(references|bibliography)\b", text, re.I):
        return True
    return False

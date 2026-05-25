"""Quality gates: MDPI exclusion (hard rule), publisher exclusion, date window,
and de-duplication across sources.
"""

from __future__ import annotations

from .models import Candidate, norm_doi

# MDPI's Crossref DOI prefix — the most reliable signal.
MDPI_DOI_PREFIX = "10.3390"

# Known MDPI journal names (lower-cased), from the lit-review skill. Not
# exhaustive, but a useful backstop when publisher metadata is missing.
MDPI_JOURNALS = {
    "sustainability", "sensors", "applied sciences", "energies", "materials",
    "molecules", "remote sensing", "water", "forests", "land", "ijerph",
    "agriculture", "foods", "plants", "atmosphere", "nutrients", "cancers",
    "ijms", "international journal of molecular sciences", "electronics",
    "mathematics", "symmetry", "polymers", "processes", "healthcare",
}


def is_mdpi(c: Candidate) -> bool:
    if norm_doi(c.doi).startswith(MDPI_DOI_PREFIX):
        return True
    if "mdpi" in (c.publisher or "").lower():
        return True
    if "mdpi.com" in (c.url or "").lower() or "mdpi.com" in (c.oa_pdf_url or "").lower():
        return True
    if (c.venue or "").strip().lower() in MDPI_JOURNALS:
        return True
    return False


def is_excluded(c: Candidate, extra_publishers: list[str]) -> bool:
    if is_mdpi(c):
        return True
    pub = (c.publisher or "").lower()
    return any(x.lower() in pub for x in extra_publishers if x)


def within_dates(c: Candidate, year_from: int | None, year_to: int | None) -> bool:
    if c.year is None:
        return True  # don't drop unknown-year items here; let ranking decide
    if year_from and c.year < year_from:
        return False
    if year_to and c.year > year_to:
        return False
    return True


def _richness(c: Candidate) -> int:
    """Score how complete a record is, to pick the best of duplicates."""
    s = 0
    s += 2 if c.abstract else 0
    s += 1 if c.oa_pdf_url else 0
    s += 1 if c.doi else 0
    s += 1 if c.venue else 0
    s += 1 if c.publisher else 0
    s += 1 if c.authors else 0
    return s


def dedupe(candidates: list[Candidate]) -> list[Candidate]:
    """Merge duplicates by DOI (preferred) or normalised title.

    Keeps the richest record but back-fills missing fields (esp. OA PDF URL,
    abstract, cited-by) from the discarded duplicates.
    """
    best: dict[str, Candidate] = {}
    for c in candidates:
        key = c.dedup_key
        if not key:
            continue
        if key not in best:
            best[key] = c
            continue
        keep, drop = (best[key], c) if _richness(best[key]) >= _richness(c) else (c, best[key])
        # back-fill
        keep.abstract = keep.abstract or drop.abstract
        keep.oa_pdf_url = keep.oa_pdf_url or drop.oa_pdf_url
        keep.doi = keep.doi or drop.doi
        keep.venue = keep.venue or drop.venue
        keep.publisher = keep.publisher or drop.publisher
        keep.url = keep.url or drop.url
        keep.cited_by_count = max(keep.cited_by_count, drop.cited_by_count)
        if not keep.authors:
            keep.authors = drop.authors
        best[key] = keep
    return list(best.values())

"""Quality gates: MDPI exclusion (hard rule), publisher exclusion, date window,
and de-duplication across sources.
"""

from __future__ import annotations

import re

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


def is_arxiv(c: Candidate) -> bool:
    """True for arXiv preprints and other grey/preprint sources."""
    doi = (c.doi or "").lower()
    url = (c.url or "").lower()
    pdf = (c.oa_pdf_url or "").lower()
    return (
        "10.48550" in doi          # arXiv's Crossref DOI prefix
        or "arxiv.org" in url
        or "arxiv.org" in pdf
        or c.source == "arxiv"
        or c.item_type == "preprint"
    )


# ── predatory / very-low-quality venues (conservative, evidence-based) ──────
# DOI prefixes seen producing predatory output in real runs (Medcrave, and two
# regional journals flagged in difference analyses). Kept small to avoid false
# positives; users can add more publisher names via cfg.exclude_publishers.
PREDATORY_DOI_PREFIXES = ("10.15406", "10.56225", "10.26911")
PREDATORY_PUBLISHERS = {
    "medcrave", "scirp", "scientific research publishing",
    "science publishing group", "sciencedomain", "david publishing",
    "academic journals", "omics", "bentham",
}


def is_predatory(c: Candidate) -> bool:
    if norm_doi(c.doi).startswith(PREDATORY_DOI_PREFIXES):
        return True
    pub = (c.publisher or "").lower()
    return any(p in pub for p in PREDATORY_PUBLISHERS)


def is_excluded(c: Candidate, extra_publishers: list[str]) -> bool:
    if is_mdpi(c) or is_predatory(c):
        return True
    pub = (c.publisher or "").lower()
    return any(x.lower() in pub for x in extra_publishers if x)


# ── item-type policy ────────────────────────────────────────────────────────
# Never useful for a literature review, regardless of the user's source-type choice.
JUNK_ITEM_TYPES = {
    "editorial", "erratum", "correction", "retraction", "abstract",
    "proceedings-abstract", "encyclopedia", "reference-entry", "dataset",
    "grant", "peer-review", "component", "report-component", "other",
}
# Whole books are always excluded (not offered in the wizard); book *chapters* stay.
BOOK_ITEM_TYPES = {"book", "monograph", "edited-book", "reference-book"}
PREPRINT_ITEM_TYPES = {"preprint", "posted-content"}
NEWS_ITEM_TYPES = {"news", "magazine-article", "newspaper-article", "blog"}
REVIEW_ITEM_TYPES = {"review", "review-article"}

# Review detection. A review synthesises a field; a *systematic* review or
# meta-analysis is a curated, screened bibliography of it — the single highest-value
# entry point and snowball seed, especially early in a project. We treat these as
# first-class (boosted in ranking, prioritised as snowball seeds), so the patterns
# stay conservative: catch the genuine article, not papers that merely mention review.
_SYSTEMATIC_RE = re.compile(
    r"\b(systematic review|systematic literature review|meta-?analysis|"
    r"scoping review|umbrella review|prisma)\b", re.IGNORECASE)
_REVIEW_TITLE_RE = re.compile(
    r"\b(literature review|narrative review|state of the art|"
    r"a review of|review of the literature|: a review)\b", re.IGNORECASE)


# ── book reviews ───────────────────────────────────────────────────────────
# A book review is not a contribution to the literature, but nothing in the metadata
# says so: Crossref, OpenAlex and S2 all type it `journal-article`, and its title is
# frequently just the reviewed BOOK's title with the imprint appended. So it passes
# every gate above, and then compounds — `is_review` sees "review", ranking floats it
# up the cut, and the snowball may take it as a DEEP seed and spend 25 reference pulls
# on one book's bibliography.
#
# The discriminator is not the word "review". "Review of carbon leakage under regionally
# differentiated climate policies" (elephantRoom, kept) is a real paper. It is that a
# book review's title carries the reviewed book's IMPRINT — attribution, publisher,
# place, pagination, binding, price — copied off a title page. Real papers have no
# reason to carry any of that, let alone two of them at once.
_BOOK_REVIEW_LABEL_RE = re.compile(
    r"^\s*[\[(]?\s*(?:book|film|media)\s+reviews?\b"
    r"|^\s*reviewed\s+works?\b"
    r"|^\s*review\s+essay\b"
    r"|[\[(]\s*book\s+review\s*[\])]\s*$", re.IGNORECASE)

# Each is a fragment a title only acquires by quoting a book's title page.
_IMPRINT_RES = (
    re.compile(r"\b(?:[ivxlc]{1,7}\s*\+\s*)?\d{1,4}\s*pp\b\.?", re.IGNORECASE),  # xvi + 208 pp.
    re.compile(r"\bpp\.\s*\d", re.IGNORECASE),                                     # pp. 208
    re.compile(r"[$\u00a3\u20ac]\s?\d"),                                            # a price
    re.compile(r"\bisbn\b", re.IGNORECASE),
    re.compile(r"\b(?:pbk|hbk|hardback|paperback|hardcover|cloth)\b\.?", re.IGNORECASE),
    # "Cambridge, Mass.: MIT Press, 1996" / "New York, NY, The Guilford Press, 2021"
    re.compile(r"\b(?:press|publishers?|verlag|routledge|springer|wiley|blackwell|sage|"
               r"palgrave|guilford|brookings|pergamon|elsevier|academic)\b[^,]{0,24},\s*"
               r"(?:19|20)\d{2}\b", re.IGNORECASE),
    # "by J\u00f6rg Henseler" / "edited by Andrew Abbott" — a personal name, not "by doing"
    re.compile(r"\b(?:edited\s+by|by)\s+[A-Z][\w'\u2019-]+\s+(?:[A-Z]\.\s*)*[A-Z][\w'\u2019-]+"),
)


def is_book_review(c: Candidate) -> bool:
    """A review OF A BOOK — excluded outright, unlike a review article.

    Two routes, because half of them are not labelled: an explicit "Book Review"/
    "Reviewed Work" label, or a title carrying TWO OR MORE imprint markers. Two is the
    threshold that separates the real cases from the coincidences — a genuine title may
    carry "$1 trillion" or "learning by Doing Well", never those *and* a pagination.
    """
    t = (c.title or "").replace("&amp;", "&")
    if not t:
        return False
    if (c.item_type or "").lower() in ("book-review", "bookreview", "review-of-book"):
        return True
    if _BOOK_REVIEW_LABEL_RE.search(t):
        return True
    return sum(1 for r in _IMPRINT_RES if r.search(t)) >= 2


def is_systematic_review(c: Candidate) -> bool:
    """A systematic review / meta-analysis (the most valuable kind of review)."""
    return bool(_SYSTEMATIC_RE.search(f"{c.title} {(c.abstract or '')[:400]}"))


def is_review(c: Candidate) -> bool:
    """Any review ARTICLE — by item type or by title/abstract signal.

    A review of a book is not one, and saying otherwise is expensive: `discover` seeds
    the snowball from reviews at 25 references each, so one mislabelled book review
    spends a deep seed on a single monograph's bibliography.
    """
    if is_book_review(c):
        return False
    if (c.item_type or "").lower() in REVIEW_ITEM_TYPES:
        return True
    return bool(is_systematic_review(c) or _REVIEW_TITLE_RE.search(c.title or ""))


def item_type_allowed(c: Candidate, include_preprints: bool, include_news: bool) -> bool:
    """Gate on item type per the project's source-type policy.

    Junk types, whole books and reviews OF books are always dropped. Preprints/news are
    admitted only when the project opted in (the wizard's 4-way question). Everything
    else (journal-article, book-chapter, report/working-paper) is kept.

    Book reviews are decided here rather than at each caller so the rule cannot drift
    between `discover`'s search loop, its snowball merge, and the two Zotero ingests.
    The gate has never been purely about the declared type anyway — `is_arxiv` reads
    the DOI and URL.
    """
    t = (c.item_type or "").lower()
    if t in JUNK_ITEM_TYPES or t in BOOK_ITEM_TYPES:
        return False
    if is_book_review(c):
        return False
    if is_arxiv(c) or t in PREPRINT_ITEM_TYPES:
        return include_preprints
    if t in NEWS_ITEM_TYPES:
        return include_news
    return True


def is_english(c: Candidate) -> bool:
    """True if language is unknown or explicitly English (ISO 639-1 'en')."""
    return not c.language or c.language == "en"


def has_min_metadata(c: Candidate) -> bool:
    """Drop records too thin to cite: no authors, or no venue/publisher.
    Catches the '(unknown authors)' metadata failures seen in real runs."""
    if not c.authors:
        return False
    if not (c.venue or c.publisher):
        return False
    return True


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
        # Provenance is a UNION, not a pick: a paper reached by both a general query and a
        # reviewer's topic query counts toward that topic, which is the whole point of tracking it.
        seen_q = set(keep.found_by)
        keep.found_by = list(keep.found_by) + [q for q in drop.found_by if q not in seen_q]
        if not keep.authors:
            keep.authors = drop.authors
        best[key] = keep
    return list(best.values())

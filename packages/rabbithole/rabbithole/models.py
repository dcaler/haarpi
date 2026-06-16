"""Shared data model: a candidate source (and, later, a corpus item)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict


def _norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def norm_doi(doi: str | None) -> str:
    if not doi:
        return ""
    d = doi.strip().lower()
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d)
    return d


@dataclass
class Author:
    family: str = ""
    given: str = ""

    @property
    def display(self) -> str:
        return f"{self.given} {self.family}".strip() or self.family


@dataclass
class Candidate:
    title: str = ""
    authors: list = field(default_factory=list)   # list[Author]
    year: int | None = None
    venue: str = ""
    doi: str = ""
    url: str = ""
    abstract: str = ""
    keywords: list = field(default_factory=list)   # concepts/subjects/fields for ranking
    publisher: str = ""
    oa_status: str = ""          # gold/green/hybrid/bronze/closed
    oa_pdf_url: str = ""
    cited_by_count: int = 0
    item_type: str = "journal-article"
    language: str = ""           # ISO 639-1 code if known (e.g. "en", "fr"); "" = unknown
    source: str = ""             # which API surfaced it
    relevance: float = 0.0       # filled by ranking
    # Set during report ingest:
    pdf_path: str = ""
    fulltext: str = ""

    # ── identity / dedup keys ────────────────────────────────────────────
    @property
    def doi_key(self) -> str:
        return norm_doi(self.doi)

    @property
    def title_key(self) -> str:
        return _norm_title(self.title)

    @property
    def dedup_key(self) -> str:
        return self.doi_key or self.title_key

    # ── display helpers ──────────────────────────────────────────────────
    @property
    def first_author_last(self) -> str:
        if self.authors:
            return self.authors[0].family or self.authors[0].display
        return "Anon"

    def author_year(self) -> str:
        """In-text author-year tag, e.g. 'Smith & Jones, 2021'."""
        yr = self.year or "n.d."
        names = [a.family or a.display for a in self.authors]
        if not names:
            return f"Anon, {yr}"
        if len(names) == 1:
            return f"{names[0]}, {yr}"
        if len(names) == 2:
            return f"{names[0]} & {names[1]}, {yr}"
        return f"{names[0]} et al., {yr}"

    def full_citation(self) -> str:
        """APA-ish author-year reference line (Markdown)."""
        auth = self._format_authors_full()
        yr = self.year or "n.d."
        venue = f"*{self.venue}*" if self.venue else ""
        doi = f" https://doi.org/{self.doi_key}" if self.doi_key else (f" {self.url}" if self.url else "")
        parts = [p for p in [f"{auth} ({yr}).", f"{self.title}.", venue] if p]
        return " ".join(parts).rstrip(".") + "." + doi

    def _format_authors_full(self) -> str:
        out = []
        for a in self.authors:
            fam = a.family or a.display
            init = "".join(f"{p[0]}." for p in a.given.split() if p) if a.given else ""
            out.append(f"{fam}, {init}".strip().rstrip(","))
        if not out:
            return "Anon"
        if len(out) == 1:
            return out[0]
        return ", ".join(out[:-1]) + ", & " + out[-1]

    # ── (de)serialisation ────────────────────────────────────────────────
    def to_dict(self) -> dict:
        d = asdict(self)
        d["authors"] = [asdict(a) for a in self.authors]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Candidate":
        import dataclasses
        known = {f.name for f in dataclasses.fields(cls)}
        d = {k: v for k, v in d.items() if k in known}
        d["authors"] = [Author(**a) for a in d.get("authors", [])]
        return cls(**d)

"""report (ingest) — assemble the working corpus (papers + full text).

Source of papers, in priority order:
  1. The Zotero collection created by gather (if configured), or
  2. The local ./pdfs/ folder (fallback / no-Zotero mode).

Metadata is enriched from gather's candidates.json where possible.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import config, filters
from .models import Author, Candidate, norm_doi
from .pdfs import extract_text, looks_like_fulltext


def _load_candidate_index(paths) -> dict[str, Candidate]:
    """Map dedup_key + pdf filename -> Candidate, from gather output."""
    idx: dict[str, Candidate] = {}
    if not paths.candidates_json.exists():
        return idx
    for d in json.loads(paths.candidates_json.read_text(encoding="utf-8")):
        c = Candidate.from_dict(d)
        if c.dedup_key:
            idx[c.dedup_key] = c
        if c.pdf_path:
            idx[Path(c.pdf_path).name] = c
    return idx


def _zotero_item_to_candidate(data: dict) -> Candidate:
    authors = []
    for cr in data.get("creators", []):
        if cr.get("creatorType") not in (None, "author"):
            continue
        if cr.get("name"):
            from .sources import _split_name
            authors.append(_split_name(cr["name"]))
        else:
            authors.append(Author(family=cr.get("lastName", ""),
                                  given=cr.get("firstName", "")))
    year = None
    m = re.search(r"(\d{4})", data.get("date", "") or "")
    if m:
        year = int(m.group(1))
    return Candidate(
        title=data.get("title", "") or "",
        authors=authors,
        year=year,
        venue=data.get("publicationTitle", "") or data.get("bookTitle", "") or "",
        doi=data.get("DOI", "") or "",
        url=data.get("url", "") or "",
        abstract=data.get("abstractNote", "") or "",
        publisher=data.get("publisher", "") or "",
        item_type=data.get("itemType", "journal-article") or "journal-article",
        source="zotero",
    )


def _enrich(c: Candidate, idx: dict[str, Candidate]) -> Candidate:
    match = idx.get(c.dedup_key)
    if match:
        c.abstract = c.abstract or match.abstract
        c.venue = c.venue or match.venue
        c.publisher = c.publisher or match.publisher
        c.cited_by_count = c.cited_by_count or match.cited_by_count
        c.doi = c.doi or match.doi
        if not c.authors:
            c.authors = match.authors
    return c


def _corpus_item_from_zotero(zc, it: dict, idx: dict[str, Candidate], paths,
                             quiet: bool = False) -> Candidate | None:
    """Turn one Zotero collection item into a full-text Candidate, or None if it is
    an attachment/note, an excluded type, or has no usable full text."""
    data = it.get("data", {})
    if data.get("itemType") in ("attachment", "note"):
        return None
    c = _enrich(_zotero_item_to_candidate(data), idx)
    if not filters.item_type_allowed(c, include_preprints=True, include_news=False):
        if not quiet:
            print(f"    [skip] excluded item type ({c.item_type}): {c.title[:60]}")
        return None
    att = zc.pdf_attachment_key(it["key"])
    text, n_pages = "", 0
    if att:
        dest = paths.pdfs / f"{it['key']}.pdf"
        if zc.download_attachment(att, dest):
            c.pdf_path = str(dest)
            text, n_pages = extract_text(dest)
        if not text:
            text = zc.fulltext(att)
    if not text or not looks_like_fulltext(text, n_pages):
        if not quiet:
            print(f"    [skip] no usable full text: {c.title[:60]}")
        return None
    c.fulltext = text
    return c


def ingest_from_zotero(cfg, gc, paths) -> list[Candidate]:
    from . import zotero
    zc = zotero.ZoteroClient(gc)
    coll = cfg.zotero.get("collection_key") or zc.find_collection(cfg.project_name)
    if not coll:
        raise RuntimeError(
            f"No Zotero collection for '{cfg.project_name}'. "
            "Run gather with Zotero configured, or use --from-folder.")

    idx = _load_candidate_index(paths)
    items = zc.collection_items(coll)
    print(f"  Zotero collection has {len(items)} top-level items.")
    corpus: list[Candidate] = []
    for it in items:
        c = _corpus_item_from_zotero(zc, it, idx, paths)
        if c is not None:
            corpus.append(c)
    return corpus


def persist(paths, corpus: list[Candidate]) -> None:
    """Write slim corpus metadata (no full text) to work/corpus.json."""
    slim = []
    for c in corpus:
        d = c.to_dict()
        d["fulltext"] = ""
        d["fulltext_chars"] = len(c.fulltext)
        slim.append(d)
    paths.corpus_json.write_text(json.dumps(slim, indent=2, ensure_ascii=False),
                                 encoding="utf-8")


def refresh_append(cfg, gc, paths, existing: list[Candidate]) -> list[Candidate]:
    """Append Zotero-collection items not already in `existing` (matched by dedup_key),
    preserving the order and indices of existing entries so per-paper notes stay aligned.

    Returns ONLY the newly appended candidates (with full text in memory). Does not
    persist — the caller decides when to write, usually after annotating the new items."""
    from . import zotero
    zc = zotero.ZoteroClient(gc)
    coll = cfg.zotero.get("collection_key") or zc.find_collection(cfg.project_name)
    if not coll:
        return []
    have = {c.dedup_key for c in existing if c.dedup_key}
    idx = _load_candidate_index(paths)
    added: list[Candidate] = []
    for it in zc.collection_items(coll):
        data = it.get("data", {})
        if data.get("itemType") in ("attachment", "note"):
            continue
        probe = _zotero_item_to_candidate(data)
        if probe.dedup_key and probe.dedup_key in have:
            continue
        c = _corpus_item_from_zotero(zc, it, idx, paths, quiet=True)
        if c is not None and c.dedup_key not in have:
            added.append(c)
            if c.dedup_key:
                have.add(c.dedup_key)
    return added


def ingest_from_folder(paths) -> list[Candidate]:
    idx = _load_candidate_index(paths)
    pdfs = sorted(paths.pdfs.glob("*.pdf"))
    print(f"  ./pdfs/ has {len(pdfs)} files.")
    corpus: list[Candidate] = []
    for fp in pdfs:
        text, n_pages = extract_text(fp)
        if not text or not looks_like_fulltext(text, n_pages):
            print(f"    [skip] no usable full text: {fp.name}")
            continue
        c = idx.get(fp.name)
        if c is None:
            c = _candidate_from_pdf(fp, text)
        if not filters.item_type_allowed(c, include_preprints=True, include_news=False):
            print(f"    [skip] excluded item type ({c.item_type}): {fp.name}")
            continue
        c.pdf_path = str(fp)
        c.fulltext = text
        corpus.append(c)
    return corpus


def _candidate_from_pdf(fp: Path, text: str) -> Candidate:
    """Best-effort metadata when a manually-added PDF isn't in candidates.json."""
    title = ""
    try:
        import fitz
        doc = fitz.open(fp)
        title = (doc.metadata or {}).get("title", "") or ""
        doc.close()
    except Exception:  # noqa: BLE001
        pass
    if not title:
        for line in text.splitlines():
            if len(line.strip()) > 15:
                title = line.strip()
                break
    return Candidate(title=title or fp.stem, source="folder")


def build(cfg, gc, paths, from_folder: bool) -> list[Candidate]:
    use_zotero = (not from_folder) and gc.have_zotero and cfg.zotero.get("collection_key")
    if use_zotero:
        print("Ingesting from Zotero collection...")
        corpus = ingest_from_zotero(cfg, gc, paths)
    else:
        if not from_folder and not gc.have_zotero:
            print("[note] No Zotero configured — ingesting from ./pdfs/ instead.")
        print("Ingesting from ./pdfs/ folder...")
        corpus = ingest_from_folder(paths)
    persist(paths, corpus)  # slim metadata (no full text) for inspection / resume
    return corpus

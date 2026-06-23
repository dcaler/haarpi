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


# Better BibTeX writes the curated citation key into the item's Extra field as
# a "Citation Key: xxx" line; Zotero's BibTeX export uses the same key. Parsing it
# here lets the review cite with the user's own Zotero keys (not generated ones).
_CITEKEY_RE = re.compile(r"(?im)^[ \t]*Citation Key[ \t]*[:=][ \t]*(\S+)")


def _extract_citekey(data: dict) -> str:
    m = _CITEKEY_RE.search(data.get("extra", "") or "")
    return m.group(1) if m else ""


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
        citekey=_extract_citekey(data),
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


def _bibtex_key_maps(bib_text: str) -> tuple[dict[str, str], dict[str, str]]:
    """From a Better BibTeX export, map normalised DOI → key and normalised title → key.

    Uses the same block split and title/DOI normalisation as _patch_bibtex_keys, so the
    keys recovered here are exactly those the export (and a pinned Extra) would carry."""
    from .models import _norm_title
    starts = [m.start() for m in re.finditer(r"^@", bib_text, re.MULTILINE)]
    by_doi: dict[str, str] = {}
    by_title: dict[str, str] = {}
    for i, s in enumerate(starts):
        block = bib_text[s: starts[i + 1] if i + 1 < len(starts) else len(bib_text)]
        m = re.match(r"@\w+\{([^,\s]+)", block)
        if not m:
            continue
        key = m.group(1)
        doi_m = re.search(r"\bdoi\s*=\s*\{([^}]+)\}", block, re.IGNORECASE)
        if doi_m:
            by_doi.setdefault(norm_doi(doi_m.group(1).strip()), key)
        title_m = re.search(r"\btitle\s*=\s*\{((?:[^{}]|\{[^{}]*\})*)\}",
                            block, re.IGNORECASE)
        if title_m:
            by_title.setdefault(_norm_title(re.sub(r"[{}]", "", title_m.group(1))), key)
    return by_doi, by_title


def backfill_citekeys(cfg, gc, paths, corpus: list[Candidate]) -> int:
    """Fill empty `citekey` fields on an already-loaded corpus from Zotero's BibTeX.

    The ingest-time citekey is parsed from an item's Extra field, which only carries a
    key when Better BibTeX has *pinned* it. Libraries that leave keys unpinned (the
    common case) ingest with citekey="" even though BBT still has a key for every item —
    that key appears in the collection's BibTeX export. So we source from the export, not
    Extra: one HTTP call (no PDF downloads, no LLM), matched to the corpus by DOI then
    normalised title. Returns the number filled; the caller persists when > 0. No-op when
    every entry already has a key, Zotero is unavailable, or no collection is configured."""
    missing = [c for c in corpus if not c.citekey]
    if not missing or not gc.have_zotero:
        return 0
    coll = cfg.zotero.get("collection_key")
    if not coll:
        return 0
    from . import zotero
    try:
        zc = zotero.ZoteroClient(gc)
        bib_text = zc.collection_bibtex(coll)
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] citekey backfill skipped — Zotero BibTeX fetch failed ({e}).")
        return 0

    by_doi, by_title = _bibtex_key_maps(bib_text)
    filled = 0
    for c in missing:
        ck = (c.doi_key and by_doi.get(c.doi_key)) or by_title.get(c.title_key)
        if ck:
            c.citekey = ck
            filled += 1
    return filled


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

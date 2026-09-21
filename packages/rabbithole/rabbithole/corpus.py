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

from . import config, corpus_ledger
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
    an attachment/note or has no usable full text.

    NO TYPE POLICY HERE. Membership of the collection IS the decision. `gather` judges
    what to propose off the open web, where a whole book, an editorial or a review of a
    book is usually noise; but an item sitting in the collection with a PDF attached got
    there because a person put it there, and second-guessing that threw away Epstein &
    Axtell's *Growing Artificial Societies* and Epstein's *Generative Social Science* —
    two of DigiPros' foundations — on the grounds that they are books.

    This path already trusts the human on publisher, date and language: none of
    `is_excluded`, `within_dates` or `is_english` is applied here. The type gate was the
    last holdout, and dropping it makes the rule one thing instead of two.

    The full-text requirement stays, because it is not a policy: there is simply nothing
    to embed without text."""
    data = it.get("data", {})
    if data.get("itemType") in ("attachment", "note"):
        return None          # Zotero plumbing, not a source
    c = _enrich(_zotero_item_to_candidate(data), idx)
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

    # ONE COLLECTION, MANY ROLES. The collection is shared by every review in the project
    # and holds the union of their sources, so ingesting it wholesale would pull the
    # substantive corpus into the methods review and vice versa. The ledger says which
    # rows are ours; `quarantine` is in the collection (and so in refs.bib) but in no
    # review's corpus.
    #
    # AN ITEM WITH NO ROW IS NOT EXCLUDED. Requiring a matching row looks right and is a
    # trap: on a project that has never synced, `audit` writes rows for the papers it
    # quarantines and for nothing else, so the very next `build` would see a non-empty
    # ledger in which no item carries the literature role — and ingest ZERO papers. The
    # rule is therefore: an unrowed item belongs to the default review, which is the only
    # reading under which a half-populated ledger cannot silently empty a corpus. A
    # non-default review still takes only what is explicitly its own, or it would hoover
    # up the substantive corpus.
    project_root = config.work_root(paths.root).parent
    kind = config.kind_of(paths.root)
    rows = corpus_ledger.load(project_root)

    def _mine(it) -> bool:
        key = (it.get("data", {}) or {}).get("key") or it.get("key")
        row = rows.get(key)
        if row is None:
            return kind.name == config.DEFAULT_KIND
        return row.ingestible(kind.name)

    if rows:
        before = len(items)
        items = [it for it in items if _mine(it)]
        dropped = before - len(items)
        print(f"  Zotero collection has {before} top-level items; {len(items)} are in the "
              f"'{kind.name}' corpus ({dropped} belong to another review or are "
              f"quarantined).")
    else:
        # No ledger at all: no information, so behave exactly as before it existed.
        print(f"  Zotero collection has {len(items)} top-level items.")

    corpus: list[Candidate] = []
    for it in items:
        c = _corpus_item_from_zotero(zc, it, idx, paths)
        if c is not None:
            corpus.append(c)
    return dedupe_corpus(corpus)


def dedupe_corpus(corpus: list[Candidate]) -> list[Candidate]:
    """Collapse the same paper appearing twice. One row per work, keeping the usable one.

    `filters.dedupe` has always run inside `gather`, and nothing ran here — a Zotero collection
    holding one paper under two items produced two corpus entries, and since they are the same
    paper they take the same citekey. Which of the two the bibliography then prints is
    arbitrary: elephantRoom carried three such pairs (Branger 2014, Rosenbloom 2020 and
    Sanna-Randaccio 2014), each flagged by `duplicate_citekeys` as something to go and fix by
    hand every run.

    Not `filters.dedupe`: its richness score knows nothing about full text or a downloaded PDF,
    so it could keep the metadata-rich twin and discard the only one that can be read. Full
    text wins first here, and everything else is a tiebreak under it.
    """
    def _usable(c: Candidate) -> tuple:
        return (bool(c.fulltext), len(c.fulltext or ""), bool(c.pdf_path),
                bool(c.doi), bool(c.citekey), bool(c.abstract))

    best: dict[str, Candidate] = {}
    merged: list[tuple[str, str]] = []
    order: list[str] = []
    for c in corpus:
        key = c.dedup_key
        if not key:
            order.append(f"\x00{len(order)}")      # no identity to merge on: keep as-is
            best[order[-1]] = c
            continue
        if key not in best:
            best[key] = c
            order.append(key)
            continue
        keep, drop = ((best[key], c) if _usable(best[key]) >= _usable(c) else (c, best[key]))
        # Back-fill so the surviving row is not poorer than the pair it replaces.
        keep.doi = keep.doi or drop.doi
        keep.abstract = keep.abstract or drop.abstract
        keep.citekey = keep.citekey or drop.citekey
        keep.pdf_path = keep.pdf_path or drop.pdf_path
        keep.fulltext = keep.fulltext or drop.fulltext
        keep.venue = keep.venue or drop.venue
        keep.publisher = keep.publisher or drop.publisher
        best[key] = keep
        merged.append((keep.first_author_last, keep.title))
    if merged:
        print(f"  Merged {len(merged)} duplicate record(s) — the same paper held twice in the "
              f"collection, which would otherwise take one citekey between them:")
        for au, title in merged[:6]:
            print(f"    - {au}: {title[:66]}")
        if len(merged) > 6:
            print(f"    … and {len(merged) - 6} more")
    return [best[k] for k in order]


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


def count_uningested(cfg, gc, existing: list[Candidate]) -> int:
    """How many Zotero-collection items are not yet in `existing` — metadata probe only.

    A cheap staleness check for `revise`, which no longer embeds new sources itself (that is
    `build`'s job): list the collection ONCE and compare dedup_keys built from item metadata,
    fetching no PDF and embedding nothing. Returns 0 when Zotero is unavailable."""
    from . import zotero
    try:
        zc = zotero.ZoteroClient(gc)
    except Exception:  # noqa: BLE001 — a staleness hint must never break a revise
        return 0
    coll = cfg.zotero.get("collection_key") or zc.find_collection(cfg.project_name)
    if not coll:
        return 0
    have = {c.dedup_key for c in existing if c.dedup_key}
    n = 0
    for it in zc.collection_items(coll):
        data = it.get("data", {})
        if data.get("itemType") in ("attachment", "note"):
            continue
        probe = _zotero_item_to_candidate(data)
        if probe.dedup_key and probe.dedup_key not in have:
            n += 1
    return n


def unambiguous(pairs) -> dict:
    """A fingerprint -> citekey map that DROPS any fingerprint two different keys claim.

    The rule this encodes: a wrong match is worse than no match. An unmatched record falls
    through to a generated {last}{year} key, which is correct and unique; a wrongly-matched
    one is cited under another work's key, so the bibliography prints the wrong source and
    the real one becomes uncitable.

    `setdefault` — first block wins, rest discarded — is what this replaces. DigiPros holds
    two different works titled exactly "Prosopography" (Stone, Daedalus 1971; Dogan & Lebaron,
    2023), neither with a DOI, and title-only matching gave the 2023 chapter Stone's citekey.
    """
    out: dict = {}
    poisoned: set = set()
    for fp, key in pairs:
        if not fp or fp in poisoned:
            continue
        if fp in out and out[fp] != key:
            del out[fp]
            poisoned.add(fp)
            continue
        out[fp] = key
    return out


def _bibtex_year(block: str) -> str:
    """The 4-digit year of a BibTeX block. `\bdate` does not match inside `urldate`."""
    m = re.search(r"\b(?:year|date)\s*=\s*\{([^}]*)\}", block, re.IGNORECASE)
    if not m:
        return ""
    y = re.search(r"\b(1[5-9]\d{2}|20\d{2})\b", m.group(1))
    return y.group(1) if y else ""


def _bibtex_key_maps(bib_text: str) -> tuple[dict, dict, dict]:
    """From a Better BibTeX export: DOI -> key, (title, year) -> key, and title -> key.

    Three maps rather than two because title alone is not an identity. Uses the same block
    split and title/DOI normalisation as _patch_bibtex_keys, so the keys recovered here are
    exactly those the export (and a pinned Extra) would carry.
    """
    from .models import _norm_title
    starts = [m.start() for m in re.finditer(r"^@", bib_text, re.MULTILINE)]
    dois, title_years, titles = [], [], []
    for i, s in enumerate(starts):
        block = bib_text[s: starts[i + 1] if i + 1 < len(starts) else len(bib_text)]
        m = re.match(r"@\w+\{([^,\s]+)", block)
        if not m:
            continue
        key = m.group(1)
        doi_m = re.search(r"\bdoi\s*=\s*\{([^}]+)\}", block, re.IGNORECASE)
        if doi_m:
            dois.append((norm_doi(doi_m.group(1).strip()), key))
        title_m = re.search(r"\btitle\s*=\s*\{((?:[^{}]|\{[^{}]*\})*)\}",
                            block, re.IGNORECASE)
        if title_m:
            t = _norm_title(re.sub(r"[{}]", "", title_m.group(1)))
            titles.append((t, key))
            year = _bibtex_year(block)
            if year:
                title_years.append(((t, year), key))
    return unambiguous(dois), unambiguous(title_years), unambiguous(titles)


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

    by_doi, by_title_year, by_title = _bibtex_key_maps(bib_text)
    filled = 0
    for c in missing:
        # DOI, then title+year, then title — and the last only when exactly one work in the
        # export carries that title. Title alone identified two different "Prosopography"
        # chapters as one source; the year separates them, and where it cannot, no key is
        # better than another work's.
        ck = ((c.doi_key and by_doi.get(c.doi_key))
              or (c.year and by_title_year.get((c.title_key, str(c.year))))
              or by_title.get(c.title_key))
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
        # Same rule as the Zotero path: a PDF you dropped in this folder by hand is a
        # decision already made. Only the full-text check above applies.
        c.pdf_path = str(fp)
        c.fulltext = text
        corpus.append(c)
    return dedupe_corpus(corpus)


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

"""gather — discover sources and emit a curated candidate list.

Machine half of the pipeline. gather searches the free APIs, ranks by topical
relevance (embeddings + an expert LLM re-rank), reconciles against your Zotero
library (auto-filing what you already have), and writes a short, high-quality
list of the literature still missing from the project's collection. After this
you (the human) download those PDFs and add them to the collection; then
`rabbitHole report` reads the collection and writes the review.

gather never downloads PDFs — it only produces the list (with DOIs and links).
Both gather and report are repeatable.
"""

from __future__ import annotations

import json
import time

from . import config, filters, ranking, sources
from .brain import Brain
from .models import Candidate, norm_doi


def run(directory: str = ".", use_zotero: bool = True) -> int:
    cfg = config.load_project(directory)
    gc = config.load_global()
    paths = config.project_paths(directory).ensure()
    brain = Brain(cfg.brain, gc)

    t0 = time.time()
    print(f"rabbitHole gather — {cfg.project_name}")
    print(f"  topic: {cfg.topic}")
    if cfg.focus:
        print(f"  focus: {cfg.focus}")
    if not gc.contact_email:
        print("  [note] no contact_email set — APIs may rate-limit. "
              "Set RABBITHOLE_CONTACT_EMAIL or run `rabbitHole init`.")
    print()

    per_source = max(cfg.target_max, 30)
    raw: list[Candidate] = []
    source_counts: dict[str, int] = {}

    print("Searching sources...")
    if cfg.sources.get("openalex"):
        r = sources.search_openalex(cfg.topic, per_source, gc.contact_email,
                                    cfg.date_from, cfg.date_to)
        print(f"  OpenAlex: {len(r)}")
        source_counts["OpenAlex"] = len(r)
        raw += r
    if cfg.sources.get("crossref"):
        r = sources.search_crossref(cfg.topic, per_source, gc.contact_email,
                                    cfg.date_from, cfg.date_to)
        print(f"  Crossref: {len(r)}")
        source_counts["Crossref"] = len(r)
        raw += r
    if cfg.sources.get("semantic_scholar"):
        r = sources.search_semantic_scholar(cfg.topic, per_source, gc.contact_email,
                                            gc.s2_api_key)
        print(f"  Semantic Scholar: {len(r)}")
        source_counts["Semantic Scholar"] = len(r)
        raw += r
    if cfg.sources.get("arxiv"):
        r = sources.search_arxiv(cfg.topic, per_source, gc.contact_email)
        print(f"  arXiv: {len(r)}")
        source_counts["arXiv"] = len(r)
        raw += r

    print(f"\nRaw results: {len(raw)}")
    deduped = filters.dedupe(raw)
    print(f"After de-dup: {len(deduped)}")

    kept, dropped_mdpi, dropped_date = [], 0, 0
    for c in deduped:
        if filters.is_excluded(c, cfg.exclude_publishers):
            dropped_mdpi += 1
            continue
        if not filters.within_dates(c, cfg.date_from, cfg.date_to):
            dropped_date += 1
            continue
        kept.append(c)
    print(f"Dropped: {dropped_mdpi} MDPI/excluded, {dropped_date} out-of-date-range")
    print(f"Candidates: {len(kept)}")

    # Snowball: widen the net via OpenAlex citation trails from the strongest seeds.
    snowballed = 0
    seed_dois = [c.doi_key for c in kept if c.doi_key][:8]
    if seed_dois:
        extra = _merge_new(sources.openalex_snowball(seed_dois, gc.contact_email), kept, cfg)
        if extra:
            snowballed = len(extra)
            print(f"Snowball: +{snowballed} via OpenAlex citation trails")
            kept += extra

    print("Ranking by relevance...")
    ranked = ranking.rank(kept, cfg.topic, cfg.focus, brain,
                          method=cfg.ranking.get("method", "embedding"),
                          rerank_top_n=cfg.ranking.get("rerank_top_n", 0),
                          target=cfg.target_max)

    # Reconcile against Zotero: drop what's already in the collection, auto-file
    # anything you already have elsewhere in your library, and keep only the
    # genuinely-missing literature on the human search list.
    collection_key, already, auto_added = "", 0, 0
    if use_zotero and gc.have_zotero:
        collection_key, ranked, already, auto_added = _zotero_filter(cfg, gc, ranked)
        if collection_key:
            print(f"In collection: {already} already | "
                  f"{auto_added} auto-added from your library | "
                  f"{len(ranked)} still missing")
            cfg.zotero["collection_key"] = collection_key
            config.save_project(cfg, directory)
    elif use_zotero:
        print("\n[note] No Zotero credentials — listing all candidates "
              "(can't tell which you already have). "
              "Add a Zotero key to ~/.config/rabbithole/config.toml to enable.")

    # Floor scale depends on the ranking method: LLM scores are 0-10 (default
    # floor 6), embedding cosine is 0-1 (no floor by default -> just take top N).
    rank_method = cfg.ranking.get("method", "embedding")
    default_floor = 6.0 if rank_method == "llm" else 0.0
    floor = float(cfg.ranking.get("min_score", default_floor))
    qualified = [c for c in ranked if c.relevance >= floor]
    shortlist = qualified[:cfg.target_max]
    print(f"Curated: {len(shortlist)} of {len(ranked)} "
          f"(method={rank_method}, floor={floor:g}) — target {cfg.target_max}")

    # Resolve a direct OA PDF link for the final list only — a convenience, NOT a
    # selection criterion. Paywalled papers stay on the list; the DOI is the fetch
    # path (the user has institutional access).
    if gc.contact_email:
        for c in shortlist:
            if not c.oa_pdf_url and c.doi:
                c.oa_pdf_url = sources.unpaywall_pdf(c.doi_key, gc.contact_email)

    _write_candidates(cfg, paths, shortlist, missing_mode=bool(collection_key))

    stats = {
        "sources": source_counts,
        "raw": len(raw),
        "deduped": len(deduped),
        "dropped_mdpi": dropped_mdpi,
        "dropped_date": dropped_date,
        "candidates": len(kept),
        "snowballed": snowballed,
        "already": already,
        "auto_added": auto_added,
        "oa_links": sum(1 for c in shortlist if c.oa_pdf_url),
    }
    _print_next_steps(cfg, paths, shortlist, collection_key)
    _notify_done(cfg, gc, paths, shortlist, collection_key, stats,
                 elapsed=time.time() - t0)
    return 0


def _item_keys(data: dict) -> list[str]:
    """Identity keys (normalised DOI + normalised title) for a Zotero item."""
    keys = []
    d = norm_doi(data.get("DOI", ""))
    if d:
        keys.append(d)
    t = Candidate(title=data.get("title", "")).title_key
    if t:
        keys.append(t)
    return keys


def _hit(c: Candidate, keys: set[str]) -> bool:
    return bool((c.doi_key and c.doi_key in keys) or
                (c.title_key and c.title_key in keys))


def _library_hit(c: Candidate, library: dict) -> dict | None:
    if c.doi_key and c.doi_key in library:
        return library[c.doi_key]
    if c.title_key and c.title_key in library:
        return library[c.title_key]
    return None


def _zotero_filter(cfg, gc, ranked: list[Candidate]):
    """Find-or-create the collection, then split `ranked`:
      - already in the collection         -> dropped (counted)
      - in the library but not collection -> added to the collection, dropped
      - absent from the library           -> kept (the human search list)
    Returns (collection_key, kept, already_in_collection, auto_added)."""
    from . import zotero
    try:
        zc = zotero.ZoteroClient(gc)
        coll = zc.create_collection(cfg.project_name)   # idempotent find-or-create
    except Exception as e:  # noqa: BLE001
        print(f"\n[warn] Zotero unavailable ({e}); listing all candidates.")
        return "", ranked, 0, 0

    present: set[str] = set()
    for it in zc.collection_items(coll):
        present.update(_item_keys(it.get("data", {})))

    library: dict[str, dict] = {}
    for it in zc.library_items():
        for k in _item_keys(it.get("data", {})):
            library.setdefault(k, it)

    kept, already, added = [], 0, 0
    for c in ranked:
        if _hit(c, present):
            already += 1
            continue
        item = _library_hit(c, library)
        if item is not None and zc.add_item_to_collection(item, coll):
            added += 1
            continue
        kept.append(c)
    return coll, kept, already, added


def _merge_new(extra: list[Candidate], existing: list[Candidate], cfg) -> list[Candidate]:
    """Dedup snowball results against the existing set + each other, drop MDPI/out-of-date."""
    seen = {c.dedup_key for c in existing if c.dedup_key}
    out: list[Candidate] = []
    for c in extra:
        k = c.dedup_key
        if not k or k in seen:
            continue
        if filters.is_excluded(c, cfg.exclude_publishers):
            continue
        if not filters.within_dates(c, cfg.date_from, cfg.date_to):
            continue
        seen.add(k)
        out.append(c)
    return out


def _authors_str(c: Candidate) -> str:
    names = [a.family or a.display for a in c.authors]
    if not names:
        return "(unknown authors)"
    if len(names) > 6:
        names = names[:6] + ["et al."]
    return ", ".join(names)


def _write_candidates(cfg, paths, shortlist: list[Candidate],
                      missing_mode: bool = False) -> None:
    # machine-readable
    paths.candidates_json.write_text(
        json.dumps([c.to_dict() for c in shortlist], indent=2, ensure_ascii=False),
        encoding="utf-8")

    headline = (f"{len(shortlist)} articles missing from your Zotero collection "
                "(curated by relevance)" if missing_mode else
                f"{len(shortlist)} candidates (curated by relevance)")
    lines = [
        f"# Candidate sources — {cfg.project_name}",
        "",
        f"**Topic:** {cfg.topic}  ",
    ]
    if cfg.focus:
        lines.append(f"**Focus:** {cfg.focus}  ")
    lines += [headline, ""]

    for i, c in enumerate(shortlist, 1):
        doi = f"[{c.doi_key}](https://doi.org/{c.doi_key})" if c.doi_key else "—"
        if c.oa_pdf_url:
            link = f"[Open-access PDF]({c.oa_pdf_url})"
        elif c.url:
            link = f"[Source]({c.url})"
        else:
            link = "—"
        yr = f" ({c.year})" if c.year else ""
        lines += [
            f"## {i}. {c.title}",
            f"{_authors_str(c)}{yr}  ",
            f"DOI: {doi}  ",
            f"Link: {link}",
            "",
        ]

    lines += [
        "---",
        "",
        f"Download these PDFs and add them to your Zotero collection "
        f"(`{cfg.project_name}`), then run `rabbitHole report`.",
        "",
    ]
    paths.candidates_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _print_next_steps(cfg, paths, shortlist, collection_key: str) -> None:
    print("\n" + "=" * 60)
    print(" gather complete")
    print("=" * 60)
    print(f"  Candidate list: {paths.candidates_md}")
    label = "Articles missing from Zotero" if collection_key else "Candidates listed"
    print(f"  {label}: {len(shortlist)}")
    if collection_key:
        print(f"  Zotero collection: {cfg.project_name} (key {collection_key})")
    print("\n Next (your manual step):")
    print("  • Download the listed PDFs and add them to the Zotero collection")
    print("  • Then: rabbitHole report")


def _notify_done(cfg, gc, paths, shortlist, collection_key: str, stats: dict,
                 elapsed: float = 0.0) -> None:
    from . import notify
    label = "missing from Zotero" if collection_key else "candidates"
    src = ", ".join(f"{k} {v}" for k, v in stats["sources"].items()) or "(none)"
    h, r = divmod(int(elapsed), 3600)
    m, s = divmod(r, 60)
    rt = f"{h}h {m}m {s}s" if h else (f"{m}m {s}s" if m else f"{s}s")
    lines = [
        f"Runtime: {rt}",
        "",
        f"gather complete for '{cfg.project_name}'.",
        "",
        f"Topic: {cfg.topic}",
        f"Focus: {cfg.focus or '(none)'}",
        "",
        "Activity",
        f"  Searched: {src}",
        f"  Raw results: {stats['raw']}  ->  after de-dup: {stats['deduped']}",
        f"  Dropped: {stats['dropped_mdpi']} MDPI/excluded, "
        f"{stats['dropped_date']} out-of-date-range",
        f"  Candidates after filtering: {stats['candidates']}",
    ]
    if stats.get("snowballed"):
        lines.append(f"  Snowballed (citation trails): +{stats['snowballed']}")
    if collection_key:
        lines.append(f"  Already in Zotero collection: {stats['already']}")
        lines.append(f"  Auto-added from your library: {stats['auto_added']}")
    lines += [
        f"  Listed ({label}): {len(shortlist)}  "
        f"({stats['oa_links']} with an OA PDF link)",
        "",
        f"Candidate list: {paths.candidates_md}",
    ]
    if collection_key:
        lines.append(f"Zotero collection: {cfg.project_name}")
    lines += [
        "",
        "Next: download the listed PDFs, add them to the Zotero collection, "
        "then run `rabbitHole report`.",
    ]
    notify.send_email(f"rabbitHole: gather complete for '{cfg.project_name}'",
                      "\n".join(lines), gc)

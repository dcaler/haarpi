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
import re
import sys
import time

from . import config, filters, ranking, render, sources
from .brain import Brain
from .models import Candidate, norm_doi


_EXTRACT_SYS = """\
You turn a researcher's free-form description into structured fields for
FINDING EXISTING ACADEMIC LITERATURE. These fields drive database searches —
they must describe papers that already exist, not the researcher's novel contribution.

Respond with ONLY a JSON object, no other text:

{"topic": "...", "focus": "...", "domain_anchor": "...", "exclude_topics": "..."}

- topic: a search-friendly phrase covering the existing literature this work builds
  on (one line). If the researcher proposes a novel framing or contribution, describe
  the underlying research areas to survey — not the novel claim itself.
- focus: key subtopics, disciplines, methods, or component fields to emphasise
  (one line; "" if none). For novel interdisciplinary work, list the component
  literatures (e.g. "cognitive load theory, syllabus analysis, LLM benchmarking").
- domain_anchor: one line naming what an existing paper MUST be about to serve as
  useful background. Anchor on established fields and phenomena, not on the
  researcher's novel framing — if the framing is new, ask: what literatures does
  it draw from?
- exclude_topics: one line naming adjacent disciplines or approaches to keep OUT
  ("" if none come to mind).
Base it strictly on what the user wrote; do not invent scope."""


def _extract_topic(brain: Brain, prompt: str) -> tuple[str, str, str, str]:
    """Derive topic/focus/domain_anchor/exclude_topics from the raw research prompt."""
    try:
        raw = brain.coordinator(prompt, _EXTRACT_SYS, num_ctx=4096)
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(m.group(0)) if m else {}
        topic = (data.get("topic") or "").strip()
        focus = (data.get("focus") or "").strip()
        anchor = (data.get("domain_anchor") or "").strip()
        exclude = (data.get("exclude_topics") or "").strip()
        if topic:
            return topic, focus, anchor, exclude
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] topic extraction failed ({e}); using raw prompt as topic.",
              file=sys.stderr)
    return prompt.strip(), "", "", ""


def _make_gather_brain(cfg, gc) -> Brain:
    """Build the gather Brain, degrading to local Ollama if the requested backend
    is unavailable (e.g. 'claude' with no API key) rather than crashing the run."""
    try:
        return Brain(cfg.brain, gc)
    except RuntimeError as e:  # claude requested but not configured
        print(f"  [warn] {e}\n  Falling back to local Ollama for gather.", file=sys.stderr)
        return Brain(cfg.brain, gc, backend_override="ollama")


_QUERYGEN_SYS = """\
You generate search queries for academic databases (OpenAlex, Crossref, Semantic
Scholar). Given a research topic and focus, produce 8-10 SHORT keyword queries
(3-8 words each) that together cover the topic from several angles:
- the core concepts, stated plainly;
- key methods or study designs central to the topic;
- important synonyms / alternate terminology a different field might use;
- named seminal works, foundational authors, or specific programs/policies/datasets
  an expert would search for by name to reach high-value papers whose titles do not
  contain the obvious keywords;
- and ALWAYS 1-2 queries that specifically target underrepresented scholarly voices:
  name a relevant journal, venue, or publication context where Global South researchers,
  feminist/gender scholars, indigenous or decolonial thinkers, or other structurally
  marginalized voices in THIS specific field tend to publish. Use your knowledge of the
  discipline — pick venues that are genuinely associated with underrepresented scholarship
  in this domain, not generic diversity terms.
Stay strictly within the stated domain; do not drift into adjacent fields.
Respond with ONLY a JSON array of query strings, no other text."""


def _generate_queries(brain: Brain, cfg) -> list[str]:
    """Decompose topic+focus into several targeted search queries (plus the raw
    topic). Falls back to the single topic string if generation fails."""
    base = cfg.topic.strip()
    prompt = (f"Topic: {cfg.topic}\nFocus: {cfg.focus or '(none)'}\n"
              f"Must be about: {cfg.domain_anchor or '(the topic above)'}\n"
              f"Keep out: {cfg.exclude_topics or '(nothing specific)'}\n\n"
              f"Search queries (JSON array):")
    try:
        raw = brain.coordinator(prompt, _QUERYGEN_SYS, num_ctx=4096)
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        queries = json.loads(m.group(0)) if m else []
        queries = [str(q).strip() for q in queries if str(q).strip()]
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] query generation failed ({e}); using the topic as the only query.",
              file=sys.stderr)
        queries = []
    # Always include the raw topic; dedupe case-insensitively, keep order.
    seen, out = set(), []
    for q in [base, *queries]:
        k = q.lower()
        if q and k not in seen:
            seen.add(k)
            out.append(q)
    return out[:10]


def run(directory: str = ".", use_zotero: bool = True) -> int:
    cfg = config.load_project(directory)
    gc = config.load_global()
    paths = config.project_paths(directory).ensure()
    brain = _make_gather_brain(cfg, gc)

    t0 = time.time()

    def log(msg: str) -> None:
        """Progress line stamped with elapsed mm:ss, so a long run is legible."""
        el = int(time.time() - t0)
        print(f"  [{el // 60}:{el % 60:02d}] {msg}", flush=True)

    if not cfg.topic and cfg.research_prompt:
        log("Extracting topic and focus from your research prompt...")
        cfg.topic, cfg.focus, cfg.domain_anchor, cfg.exclude_topics = (
            _extract_topic(brain, cfg.research_prompt))
        log(f"Topic: {cfg.topic}")
        if cfg.focus:
            log(f"Focus: {cfg.focus}")
        config.save_project(cfg, directory)

    print(f"rabbitHole gather — {cfg.project_name}")
    print(f"  topic: {cfg.topic}")
    if cfg.focus:
        print(f"  focus: {cfg.focus}")
    backend = "Claude" if brain.backend == "claude" else f"Ollama ({cfg.brain.coordinator_model})"
    print(f"  brain: {backend}")
    if not gc.contact_email:
        print("  [note] no contact_email set — APIs may rate-limit. "
              "Set RABBITHOLE_CONTACT_EMAIL or run `rabbitHole init`.")
    print()

    per_source = max(cfg.target_max, 30)
    raw: list[Candidate] = []
    source_counts: dict[str, int] = {}

    log("Generating search queries with the coordinator model...")
    queries = _generate_queries(brain, cfg)
    log(f"{len(queries)} query angles:")
    for q in queries:
        print(f"        • {q}")
    # Spread the budget across query angles so total volume stays comparable to
    # the old single-query run; dedupe collapses the heavy overlap afterwards.
    per_query = max(12, per_source // 2)
    for qi, q in enumerate(queries, 1):
        per_q: list[str] = []
        if cfg.sources.get("openalex"):
            r = sources.search_openalex(q, per_query, gc.contact_email,
                                        cfg.date_from, cfg.date_to)
            source_counts["OpenAlex"] = source_counts.get("OpenAlex", 0) + len(r)
            per_q.append(f"OpenAlex {len(r)}")
            raw += r
        if cfg.sources.get("crossref"):
            r = sources.search_crossref(q, per_query, gc.contact_email,
                                        cfg.date_from, cfg.date_to)
            source_counts["Crossref"] = source_counts.get("Crossref", 0) + len(r)
            per_q.append(f"Crossref {len(r)}")
            raw += r
        if cfg.sources.get("semantic_scholar"):
            r = sources.search_semantic_scholar(q, per_query, gc.contact_email,
                                                gc.s2_api_key)
            source_counts["Semantic Scholar"] = source_counts.get("Semantic Scholar", 0) + len(r)
            per_q.append(f"S2 {len(r)}")
            raw += r
        if cfg.sources.get("arxiv") and cfg.include_preprints:
            r = sources.search_arxiv(q, per_query, gc.contact_email)
            source_counts["arXiv"] = source_counts.get("arXiv", 0) + len(r)
            per_q.append(f"arXiv {len(r)}")
            raw += r
        log(f"[{qi}/{len(queries)}] {q[:55]!r} → {', '.join(per_q) or '(no sources enabled)'}")

    # High-value recall: a dedicated "most-cited in this area" pass pulls the
    # canonical, well-cited backbone of the field even when keyword search misses it.
    if cfg.sources.get("openalex"):
        log("Most-cited pass (OpenAlex, sorted by citation count)...")
        mc = sources.search_openalex(cfg.topic, per_source, gc.contact_email,
                                     cfg.date_from, cfg.date_to,
                                     sort="cited_by_count:desc")
        source_counts["OpenAlex (most-cited)"] = len(mc)
        log(f"  +{len(mc)} most-cited")
        raw += mc

    log(f"Raw results: {len(raw)}")
    deduped = filters.dedupe(raw)
    log(f"After de-dup: {len(deduped)}")

    kept, dropped_excluded, dropped_date, dropped_type, dropped_meta = [], 0, 0, 0, 0
    for c in deduped:
        if filters.is_excluded(c, cfg.exclude_publishers):
            dropped_excluded += 1
            continue
        if not filters.within_dates(c, cfg.date_from, cfg.date_to):
            dropped_date += 1
            continue
        if not filters.item_type_allowed(c, cfg.include_preprints, cfg.include_news):
            dropped_type += 1
            continue
        if not filters.has_min_metadata(c):
            dropped_meta += 1
            continue
        kept.append(c)
    log(f"Dropped: {dropped_excluded} MDPI/predatory/excluded, "
        f"{dropped_date} out-of-date-range, {dropped_type} disallowed type, "
        f"{dropped_meta} thin metadata")
    log(f"Candidates: {len(kept)}")

    # Snowball: widen via OpenAlex citation trails from the MOST-CITED seeds — the
    # field's established anchors, whose references and citers are the canonical work.
    snowballed = 0
    by_cites = sorted(kept, key=lambda c: c.cited_by_count, reverse=True)
    seed_dois = [c.doi_key for c in by_cites if c.doi_key][:8]
    if seed_dois:
        log(f"Snowball: expanding from {len(seed_dois)} most-cited seeds via citation trails...")
        extra = _merge_new(sources.openalex_snowball(seed_dois, gc.contact_email), kept, cfg)
        if extra:
            snowballed = len(extra)
            log(f"Snowball: +{snowballed} via OpenAlex citation trails")
            kept += extra

    log("Ranking by relevance...")
    ranked = ranking.rank(kept, cfg.topic, cfg.focus, brain,
                          method=cfg.ranking.get("method", "embedding"),
                          rerank_top_n=cfg.ranking.get("rerank_top_n", 0),
                          target=cfg.target_max,
                          domain_anchor=cfg.domain_anchor,
                          exclude_topics=cfg.exclude_topics)

    # Reconcile against Zotero: drop what's already in the collection, auto-file
    # anything you already have elsewhere in your library, and keep only the
    # genuinely-missing literature on the human search list.
    collection_key, already, auto_added = "", 0, 0
    if use_zotero and gc.have_zotero:
        log("Reconciling against your Zotero collection...")
        collection_key, ranked, already, auto_added = _zotero_filter(cfg, gc, ranked)
        if collection_key:
            log(f"In collection: {already} already | "
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
    max_arxiv_frac = float(cfg.ranking.get("max_arxiv_fraction", 0.25))
    shortlist = _cap_arxiv(qualified, cfg.target_max, max_arxiv_frac)
    arxiv_n = sum(1 for c in shortlist if filters.is_arxiv(c))
    log(f"Curated: {len(shortlist)} of {len(ranked)} "
        f"(method={rank_method}, floor={floor:g}, arxiv={arxiv_n}/{len(shortlist)}) "
        f"— target {cfg.target_max}")

    # Resolve a direct OA PDF link for the final list only — a convenience, NOT a
    # selection criterion. Paywalled papers stay on the list; the DOI is the fetch
    # path (the user has institutional access).
    if gc.contact_email:
        log("Resolving open-access PDF links (Unpaywall)...")
        for c in shortlist:
            if not c.oa_pdf_url and c.doi:
                c.oa_pdf_url = sources.unpaywall_pdf(c.doi_key, gc.contact_email)

    _write_candidates(cfg, paths, shortlist, missing_mode=bool(collection_key))

    stats = {
        "sources": source_counts,
        "raw": len(raw),
        "deduped": len(deduped),
        "dropped_excluded": dropped_excluded,
        "dropped_date": dropped_date,
        "dropped_type": dropped_type,
        "dropped_meta": dropped_meta,
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


def _cap_arxiv(ranked: list[Candidate], target: int, max_fraction: float) -> list[Candidate]:
    """Select top `target` candidates, capping arXiv/preprints at max_fraction."""
    if max_fraction >= 1.0:
        return ranked[:target]
    max_arxiv = max(1, round(target * max_fraction))
    result, arxiv_seen, backlog = [], 0, []
    for c in ranked:
        if len(result) >= target:
            break
        if filters.is_arxiv(c):
            if arxiv_seen < max_arxiv:
                result.append(c)
                arxiv_seen += 1
            else:
                backlog.append(c)
        else:
            result.append(c)
    # If too few non-arXiv candidates exist, backfill from deferred arXiv entries.
    if len(result) < target:
        result += backlog[:target - len(result)]
    return result


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
        if not filters.item_type_allowed(c, cfg.include_preprints, cfg.include_news):
            continue
        if not filters.has_min_metadata(c):
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
    out_docx = paths.candidates_md.with_suffix(".docx")
    if render.pandoc_convert(paths.candidates_md, out_docx):
        paths.candidates_md.unlink()


def _print_next_steps(cfg, paths, shortlist, collection_key: str) -> None:
    print("\n" + "=" * 60)
    print(" gather complete")
    print("=" * 60)
    cand_path = paths.candidates_md.with_suffix(".docx") if \
        paths.candidates_md.with_suffix(".docx").exists() else paths.candidates_md
    print(f"  Candidate list: {cand_path}")
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
        f"  Dropped: {stats['dropped_excluded']} MDPI/predatory/excluded, "
        f"{stats['dropped_date']} out-of-date-range, "
        f"{stats['dropped_type']} disallowed type, "
        f"{stats['dropped_meta']} thin metadata",
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
        f"Candidate list: {paths.candidates_md.with_suffix('.docx') if paths.candidates_md.with_suffix('.docx').exists() else paths.candidates_md}",
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

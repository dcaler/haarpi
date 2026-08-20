"""rabbitHole `audit` — a word-sense corpus filter that quarantines lexical false-friends.

A paper can share a *word* with the research question while using it in a completely different
*sense* — AutoDock Vina's "docking" (ligand binding) has nothing to do with the "docking" of
adaptive agents. The embedding pre-sort that curates the corpus rewards shared vocabulary, so
such homographs slip in. This verb re-judges the corpus for CONCEPTUAL TRANSFER (not shared
words, and NOT domain membership — cross-disciplinary transfer is the point) and quarantines the
confident false-friends.

Quarantine is a MOVE between Zotero collections, never a delete: a flagged item leaves the
project collection and joins a shared ``quarantine`` collection, so it drops out of both the
corpus (``collection_items``) and refs.bib (``collection_bibtex``) with no filtering — while
staying in the library, fully reversible with ``--release``. The bias is always toward keep: a
wrong keep costs a line in a review, a wrong drop costs a cross-disciplinary paper.

Everything but the one brain call is deterministic and tested (DESIGN_corpus_audit.md).
"""
from __future__ import annotations

import json
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .brain import Brain
from . import config, runlog

QUARANTINE_COLLECTION = "quarantine"
_JSON = re.compile(r"\{.*\}", re.S)
_YEAR = re.compile(r"\b(\d{4})\b")


@dataclass
class Verdict:
    """One word-sense judgment. ``kind`` is ``"transfer"`` (keep) or ``"false_friend"`` (a
    shared word in a different sense). The sense fields explain a quarantine to a human."""
    key: str
    label: str
    kind: str
    term: str = ""
    its_sense: str = ""
    review_sense: str = ""
    confidence: float = 0.0


_SYS = (
    "You judge whether a paper belongs in a literature review, guarding against the HOMOGRAPH "
    "TRAP: a paper can share a TERM with the research question while using it in a completely "
    "different SENSE, which is not relevance. Decide whether the paper's CONTRIBUTION (a "
    "finding, method, or concept) TRANSFERS to the research question — genuine cross-"
    "disciplinary transfer counts, judge the ideas not the field — or whether it is a "
    "FALSE-FRIEND that only shares vocabulary used in another sense. Bias toward TRANSFER: call "
    "FALSE-FRIEND only when you are confident the apparent relevance is a shared word in a "
    "different sense. Respond with ONLY a JSON object: "
    '{"verdict": "TRANSFER" | "FALSE-FRIEND", "term": "the shared word (if a false friend)", '
    '"its_sense": "the sense THIS paper uses it in", "review_sense": "the sense the research '
    'question uses it in", "confidence": 0-10}.')


def _prompt(topic: str, focus: str, title: str, abstract: str, keywords) -> str:
    kw = "; ".join(keywords) if keywords else ""
    return (f"Research question topic: {topic}\nFocus: {focus}\n\n"
            f"Paper title: {title}\nKeywords: {kw}\nAbstract: {abstract[:1500]}\n\n"
            f"Verdict JSON:")


def judge_item(brain: Brain, topic: str, focus: str, *, key: str, label: str,
               title: str, abstract: str = "", keywords=()) -> Verdict:
    """One word-sense judgment for one paper. Fails SAFE: any error, or an unparseable reply,
    yields a TRANSFER (keep) at confidence 0 — the tool never quarantines on a bad signal."""
    try:
        raw = brain.coordinator(_prompt(topic, focus, title, abstract, keywords),
                                system=_SYS, num_ctx=2048).strip()
        m = _JSON.search(raw)
        data = json.loads(m.group(0)) if m else {}
    except Exception:  # noqa: BLE001 — a failed judgment is a keep, not a crash
        data = {}
    verdict = str(data.get("verdict", "")).upper().replace("_", "-").replace(" ", "-")
    kind = "false_friend" if "FALSE" in verdict else "transfer"
    try:
        conf = float(data.get("confidence", 0) or 0)
    except (TypeError, ValueError):
        conf = 0.0
    return Verdict(key=key, label=label, kind=kind, confidence=conf,
                   term=str(data.get("term", "")), its_sense=str(data.get("its_sense", "")),
                   review_sense=str(data.get("review_sense", "")))


def _to_cache(v: Verdict) -> dict:
    return {"kind": v.kind, "confidence": v.confidence, "term": v.term,
            "its_sense": v.its_sense, "review_sense": v.review_sense}


def _from_cache(d: dict, key: str, label: str) -> Verdict:
    return Verdict(key=key, label=label, kind=d.get("kind", "transfer"),
                   confidence=float(d.get("confidence", 0) or 0), term=d.get("term", ""),
                   its_sense=d.get("its_sense", ""), review_sense=d.get("review_sense", ""))


def format_progress(i: int, total: int, v: Verdict, cached: bool,
                    min_confidence: float = 7.0) -> str:
    """One human-readable progress line for a judged item: counter, label, and the verdict — with
    the shared term and sense-clash spelled out for a would-be quarantine. Each judgment is a whole
    model pass (minutes on old hardware), so the run must narrate itself as it goes rather than sit
    silent from the opening 'Judging N items…' to the final summary."""
    if v.kind == "false_friend" and v.confidence >= min_confidence:
        detail = (f"⚑ QUARANTINE ({v.confidence:.0f}/10)  \"{v.term}\": "
                  f"{v.its_sense or '?'} ↮ {v.review_sense or '?'}")
    elif v.kind == "false_friend":
        detail = f"keep · weak false-friend ({v.confidence:.0f}/10) \"{v.term}\""
    else:
        detail = "keep · transfers"
    tag = " · cached" if cached else ""
    return f"[{i:>3}/{total}] {v.label:<26.26} {detail}{tag}"


def audit_corpus(brain: Brain, topic: str, focus: str, items: list[dict],
                 cache: dict | None = None, min_confidence: float = 7.0,
                 *, progress=None, checkpoint=None, checkpoint_every: int = 5
                 ) -> tuple[list[Verdict], list[Verdict]]:
    """Judge every corpus item (each a dict of key/label/title/abstract/keywords) for word-sense
    transfer. Returns (flagged, all_verdicts); flagged = the CONFIDENT false-friends only. A
    ``cache`` keyed by item key is consulted first and updated, so a re-run judges only new
    items and never re-litigates a decision.

    ``progress(i, total, verdict, cached, seconds)`` is called after each item (for live logging);
    ``checkpoint(cache)`` is called every ``checkpoint_every`` FRESHLY-judged items so a long run's
    work survives an interruption (the corpus can be hundreds of items at minutes each). Both are
    optional and pure-core-preserving — omitted, the behaviour is exactly as before."""
    cache = cache if cache is not None else {}
    verdicts: list[Verdict] = []
    total = len(items)
    fresh = 0
    for i, it in enumerate(items, 1):
        key, label = it["key"], it.get("label", it["key"])
        cached = key in cache
        t0 = time.monotonic()
        if cached:
            v = _from_cache(cache[key], key, label)
        else:
            v = judge_item(brain, topic, focus, key=key, label=label,
                           title=it.get("title", ""), abstract=it.get("abstract", ""),
                           keywords=it.get("keywords", ()))
            cache[key] = _to_cache(v)
            fresh += 1
        verdicts.append(v)
        if progress is not None:
            progress(i, total, v, cached, time.monotonic() - t0)
        if checkpoint is not None and not cached and fresh % checkpoint_every == 0:
            checkpoint(cache)
    flagged = [v for v in verdicts
               if v.kind == "false_friend" and v.confidence >= min_confidence]
    return flagged, verdicts


def write_quarantine_log(outdir, flagged: list[Verdict], *, released=()) -> Path:
    """Record the reasons Zotero cannot: for each quarantined item, the shared term and the
    sense clash. This is the human-readable audit trail beside the Zotero move."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    lines = ["# Quarantine audit — suspected lexical false-friends", "",
             "Moved to the shared Zotero `quarantine` collection (nothing deleted; the papers "
             "stay in your library). Release any with `rabbitHole audit --release @key`.", ""]
    for v in flagged:
        lines += [f"## {v.label}  (`{v.key}`)",
                  f"- shared term: **{v.term}**",
                  f"- its sense: {v.its_sense}",
                  f"- review's sense: {v.review_sense}",
                  f"- confidence: {v.confidence:.0f}/10", ""]
    if released:
        lines += ["## Released back to the corpus this run", ""] + [f"- {r}" for r in released] + [""]
    p = outdir / "audit_quarantine.md"
    p.write_text("\n".join(lines))
    return p


def prune_corpus_json(corpus_json, dedup_keys: set[str]) -> int:
    """Remove quarantined papers from the cached corpus (work/corpus.json) by dedup_key, so a
    `revise` that loads the cache WITHOUT a rebuild cannot resurrect them. In the chain a `build`
    follows and rebuilds anyway, so this is the safety net for a STANDALONE audit. Notes are keyed
    by citekey (not position), so no reindex is needed — an orphaned note file is simply never
    loaded. Returns the count removed; a no-op when the file is absent or nothing matches."""
    from .models import Candidate
    corpus_json = Path(corpus_json)
    if not corpus_json.exists() or not dedup_keys:
        return 0
    try:
        data = json.loads(corpus_json.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return 0
    kept = [d for d in data if Candidate.from_dict(d).dedup_key not in dedup_keys]
    removed = len(data) - len(kept)
    if removed:
        corpus_json.write_text(json.dumps(kept, indent=2, ensure_ascii=False), encoding="utf-8")
    return removed


def _zotero_label(data: dict, fallback: str) -> str:
    creators = data.get("creators", [])
    fam = ""
    for c in creators:
        if c.get("creatorType", "author") == "author":
            fam = c.get("lastName") or c.get("name") or ""
            if fam:
                break
    if not fam and creators:
        fam = creators[0].get("lastName") or creators[0].get("name") or ""
    ym = _YEAR.search(data.get("date", "") or "")
    year = ym.group(1) if ym else ""
    return " ".join(p for p in (fam, year) if p) or fallback


def _judge_fields(raw: dict, labels: dict | None = None) -> dict:
    """Extract the fields the word-sense check reads from a raw Zotero item."""
    data = raw.get("data", {})
    key = data.get("key") or raw.get("key")
    label = (labels or {}).get(key) or _zotero_label(data, key)
    return {"key": key, "label": label, "title": data.get("title", ""),
            "abstract": data.get("abstractNote", ""),
            "keywords": [t.get("tag", "") for t in data.get("tags", [])]}


def perform_audit(zc, brain: Brain, topic: str, focus: str, *, project_key: str,
                  quarantine_key: str, items: list[dict], outdir, dry_run: bool = False,
                  cache: dict | None = None, min_confidence: float = 7.0,
                  labels: dict | None = None, progress=None, checkpoint=None,
                  checkpoint_every: int = 5) -> dict:
    """Judge the raw Zotero ``items``, move each confident false-friend from the project
    collection to quarantine (unless ``dry_run``), and write the reasons log. Pure but for the
    injected ``zc``/``brain``, so the whole flow is testable without the network. ``progress`` and
    ``checkpoint`` are passed straight through to :func:`audit_corpus` (see there)."""
    raw_by_key = {}
    judge_items = []
    for raw in items:
        f = _judge_fields(raw, labels)
        judge_items.append(f)
        raw_by_key[f["key"]] = raw
    flagged, verdicts = audit_corpus(brain, topic, focus, judge_items, cache=cache,
                                     min_confidence=min_confidence, progress=progress,
                                     checkpoint=checkpoint, checkpoint_every=checkpoint_every)
    moved: list[str] = []
    if not dry_run:
        for v in flagged:
            if zc.move_item_between_collections(raw_by_key[v.key], project_key, quarantine_key):
                moved.append(v.key)
    log = write_quarantine_log(outdir, flagged)
    return {"flagged": [v.key for v in flagged], "moved": moved,
            "verdicts": len(verdicts), "log": log}


def release_item(zc, *, quarantine_key: str, project_key: str, item: dict) -> bool:
    """Move one item back from quarantine to this project's collection — the reverse of a
    quarantine. Because the CLI is project-scoped, ``project_key`` is unambiguous."""
    return zc.move_item_between_collections(item, quarantine_key, project_key)


# ── the verb wiring (not unit-tested; the tested core is above) ────────────────

def _cache_path(paths):
    return paths.output / "audit_cache.json"


def _load_cache(paths, sig: str) -> dict:
    p = _cache_path(paths)
    if not p.exists():
        return {}
    try:
        blob = json.loads(p.read_text())
    except Exception:  # noqa: BLE001
        return {}
    if blob.get("sig") != sig:          # topic/focus changed -> re-judge from scratch
        return {}
    return blob.get("verdicts", {})


def _save_cache(paths, sig: str, cache: dict) -> None:
    try:
        _cache_path(paths).write_text(json.dumps({"sig": sig, "verdicts": cache}, indent=1))
    except Exception:  # noqa: BLE001
        pass


def _sig(topic: str, focus: str) -> str:
    """A STABLE signature of the research question, so a re-run reloads the cache instead of
    re-judging. Must not use builtin ``hash()``: string hashing is salted per process, so that
    signature changes every invocation and ``_load_cache`` would discard the cache every time —
    making the cache (and the incremental checkpoints) useless for resuming a long run."""
    import hashlib
    return hashlib.sha1(f"{topic}\x00{focus}".encode("utf-8")).hexdigest()


def run(directory: str = ".", *, dry_run: bool = False, release: str | None = None,
        brain_override: str | None = None) -> int:
    runlog.start()
    cfg = config.load_project(directory)
    gc = config.load_global()
    paths = config.project_paths(directory)

    if not (gc.have_zotero and cfg.zotero.get("collection_key")):
        print("[error] audit needs a Zotero collection (run gather first).", file=sys.stderr)
        return 1
    from . import zotero
    zc = zotero.ZoteroClient(gc)
    project_key = cfg.zotero.get("collection_key")
    quarantine_key = zc.create_collection(QUARANTINE_COLLECTION)   # find-or-create

    print(f"rabbitHole audit — {cfg.project_name}")

    if release:
        ident = release.lstrip("@")
        target = None
        for raw in zc.collection_items(quarantine_key):
            data = raw.get("data", {})
            if ident in ((data.get("key") or ""), _zotero_label(data, "")):
                target = raw
                break
        if target is None:
            print(f"  [warn] '{ident}' not found in the quarantine collection.", file=sys.stderr)
            return 1
        ok = release_item(zc, quarantine_key=quarantine_key, project_key=project_key, item=target)
        print(f"  {'Released' if ok else 'FAILED to release'} {ident} back to {cfg.project_name}.")
        return 0 if ok else 1

    brain = Brain(cfg.brain, gc, backend_override=brain_override)
    items = [it for it in zc.collection_items(project_key)
             if it.get("data", {}).get("itemType") not in ("attachment", "note")]
    total = len(items)
    print(f"  {runlog.stamp()}Judging {total} corpus item(s) for word-sense transfer"
          f"{' (dry run)' if dry_run else ''} — one model pass each; on this hardware allow "
          f"a few minutes per item. Progress below (resumable — verdicts are cached).", flush=True)

    sig = _sig(cfg.topic, cfg.focus or "")
    cache = _load_cache(paths, sig)
    min_conf = 7.0
    # Live reporter: a line per item, plus a rolling ETA from the freshly-judged rate (cached items
    # are instant, so they don't skew it) — the run narrates itself instead of going dark for hours.
    counts: Counter = Counter()
    seen = {"fresh_n": 0, "fresh_s": 0.0}

    def _report(i, n, v, cached, dt):
        counts[v.kind] += 1
        print(f"  {runlog.stamp()}{format_progress(i, n, v, cached, min_conf)}", flush=True)
        if not cached:
            seen["fresh_n"] += 1
            seen["fresh_s"] += dt
        if seen["fresh_n"] and (i % 10 == 0 or i == n) and i < n:
            avg = seen["fresh_s"] / seen["fresh_n"]
            print(f"  {runlog.stamp()}… {i}/{n} judged · ~{runlog.fmt_dt(avg)}/item · "
                  f"~{runlog.fmt_dt((n - i) * avg)} left", flush=True)

    summary = perform_audit(zc, brain, cfg.topic, cfg.focus or "",
                            project_key=project_key, quarantine_key=quarantine_key,
                            items=items, outdir=paths.output, dry_run=dry_run, cache=cache,
                            min_confidence=min_conf, progress=_report,
                            checkpoint=lambda c: _save_cache(paths, sig, c))
    _save_cache(paths, sig, cache)
    tr, ff = counts.get("transfer", 0), counts.get("false_friend", 0)
    print(f"  {runlog.stamp()}Judged {total}: {tr} transfer, {ff} false-friend "
          f"({len(summary['flagged'])} confident ≥ {min_conf:.0f}/10).")

    # Safety net for a STANDALONE audit (no `build` after): drop the quarantined papers from the
    # cached corpus too, so a following `revise` that loads work/corpus.json can't resurrect them.
    if summary["moved"]:
        from . import corpus as corpus_mod
        moved = set(summary["moved"])
        dedup = set()
        for raw in items:
            data = raw.get("data", {})
            if (data.get("key") or raw.get("key")) in moved:
                dk = corpus_mod._zotero_item_to_candidate(data).dedup_key
                if dk:
                    dedup.add(dk)
        pruned = prune_corpus_json(paths.corpus_json, dedup)
        if pruned:
            print(f"  {runlog.stamp()}Pruned {pruned} quarantined paper(s) from the cached corpus.")

    n = len(summary["flagged"])
    if not n:
        print(f"  {runlog.stamp()}No lexical false-friends found — corpus is clean.")
    elif dry_run:
        print(f"  {runlog.stamp()}{n} suspected false-friend(s) — see {summary['log'].name} "
              f"(dry run: nothing moved).")
    else:
        print(f"  {runlog.stamp()}Quarantined {len(summary['moved'])}/{n} false-friend(s) to "
              f"'{QUARANTINE_COLLECTION}'. Reasons in {summary['log'].name}; "
              f"release any with `rabbitHole audit --release @key`.")
    return 0

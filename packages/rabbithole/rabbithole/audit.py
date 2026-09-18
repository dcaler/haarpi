"""rabbitHole `audit` — a word-sense corpus filter that quarantines lexical false-friends.

A paper can share a *word* with the research question while using it in a completely different
*sense* — AutoDock Vina's "docking" (ligand binding) has nothing to do with the "docking" of
adaptive agents. The embedding pre-sort that curates the corpus rewards shared vocabulary, so
such homographs slip in. This verb re-judges the corpus for CONCEPTUAL TRANSFER (not shared
words, and NOT domain membership — cross-disciplinary transfer is the point) and quarantines the
confident false-friends.

Quarantine is a ROLE IN THE CORPUS LEDGER, never a move and never a delete: a flagged item
keeps its place in the project collection and is simply not ingested. It therefore stays in
refs.bib, which is built from that collection — dropping it out was how this verb could hand
a minted review a dangling citation, invisibly, from a command nobody thought of as touching
bibliographies. ``--release`` puts the role back and LOCKS it, so the next audit cannot undo
a human's decision. The bias is always toward keep: a wrong keep costs a line in a review, a
wrong drop costs a cross-disciplinary paper.

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
from . import config, corpus_ledger, runlog

QUARANTINE_COLLECTION = "quarantine"

# What a verdict MEANS. The signature hashes the question's words; this versions the test
# those words are put to. Bump it whenever the framing changes, because verdicts decided
# under different semantics are not comparable and the widening rule — which reasons about
# question CONTENT — cannot tell the difference. v2: the test became "is this a usable source
# for at least one stated need" rather than "does this paper's contribution match the work's".
# v3 narrowed that to the one ask that fetched each paper. Both were the same mistake: the
# needs list turns a word-sense check into a RELEVANCE judgement, which `ranking` already made
# before this verb runs. v2 quarantined 54% of DigiPros at 9/10, almost none of them
# homographs. v4 asks only what this verb was built for — does a shared word name a different
# KIND OF THING — with the needs list gone entirely.
_FRAMING = 4
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
    # Which review(s) this paper is a source FOR, when the project has more than one. Empty
    # when it has one review, or when the model offered nothing usable. ADDITIVE ONLY: it can
    # only ever add a purpose to what `gather` recorded, never take one away — gather searched
    # and found, which is certain; this is inference.
    serves: tuple = ()


_SYS = (
    "A literature corpus was assembled by KEYWORD SEARCH, so a few papers matched on a word "
    "that means something else entirely in their own literature. Your only job is to find "
    "those. "
    "THE TEST: does the shared term denote the SAME KIND OF THING in this paper as in the "
    "review? `agent` in agent-based modelling versus `agent` meaning a chemical reagent. "
    "`docking` of adaptive agents versus ligand-receptor docking. `cell` in a lattice model "
    "versus `cell` in biology. These are not delicate distinctions — a false friend is "
    "obvious, and if you find yourself weighing it finely, it is not one. "
    "A DIFFERENT FIELD IS NOT A FALSE FRIEND. A statistical-physics paper analysing the "
    "Schelling segregation model uses that term for exactly the thing the review means. "
    "Cross-disciplinary work is what a literature review exists to find, and dropping it is "
    "the most expensive mistake available here. Judge the WORD, never the discipline. "
    "THIS IS NOT A RELEVANCE TEST. Whether a paper is useful, central, or worth the space was "
    "decided by ranking before you saw it. A paper squarely in the review's subject that is "
    "merely less useful is a KEEP. So is a paper the review would cite in passing, or "
    "disagree with, or use only as an example. "
    "Call FALSE-FRIEND only when the shared term names a different kind of thing. Everything "
    "else transfers. "
    "Respond with ONLY a JSON object: "
    '{"verdict": "TRANSFER" | "FALSE-FRIEND", "term": "the shared word (if a false friend)", '
    '"its_sense": "what the word denotes in THIS paper", "review_sense": "what it denotes in '
    'the review", "confidence": 0-10}.')

# The SECOND question, asked only when a project has two reviews. It is a different kind of
# question from the homograph test and must stay one: choosing between two KNOWN descriptions
# is classification — what kind of contribution is this — and an abstract carries that. "Does
# this serve the review's needs" is the open-ended question that quarantined 54% of a corpus,
# and it is not being asked again.
#
# The answer can only ADD. `gather` searched an anchor and found the paper, which is certain;
# this is inference, and inference must not overrule a record. A paper the model thinks serves
# neither keeps exactly what gather gave it — so "neither" is not a quarantine signal, and the
# relevance judgement cannot return through that door.
_PURPOSE_SYS = (
    "A project keeps two literature reviews, and one Zotero collection holding both their "
    "sources. Say which review this paper is a source FOR. "
    "This is a question about the KIND of contribution the paper makes — methodological or "
    "substantive — not about how useful or central it is. Usefulness was settled before you "
    "saw it. "
    "A paper can serve BOTH, and often does: a paper applying a method to the review's own "
    "subject matter belongs to each of them. Say both when both are true. "
    "Answer with ONLY a JSON object: "
    '{"serves": ["literature"] | ["methods"] | ["literature", "methods"]}.')


def _prompt(topic: str, background: str, asks, title: str, abstract: str, keywords,
            fetched_for: str = "") -> str:
    """The judgement prompt: what the review is ABOUT, and one candidate.

    Deliberately short, and deliberately not a needs list. Two earlier versions handed the
    model the review's stated needs — first fused with the author's statement, then one ask
    at a time — and both turned a word-sense check into a relevance judgement. Relevance was
    already decided by `ranking`; asking for it again quarantined 43 of DigiPros's first 49
    papers at 9/10, and 54% of the corpus overall, almost none of them homographs.

    What the model needs to spot `agent`-the-reagent among `agent`-the-ABM is what the
    review is about. `asks` and `fetched_for` are accepted and ignored, so callers and
    cached signatures keep working.
    """
    kw = "; ".join(keywords) if keywords else ""
    return "\n".join([
        f"The review is about: {topic}",
        "",
        "In more detail, so you can tell which sense of a shared word is meant:",
        background or "(not stated)",
        "",
        "Candidate source:",
        f"Title: {title}",
        f"Keywords: {kw}",
        f"Abstract: {abstract[:1500]}",
        "",
        "Does any term this paper shares with the review name a DIFFERENT KIND OF THING "
        "here than it does there? Verdict JSON:",
    ])


def _purpose_prompt(reviews: dict, title: str, abstract: str, keywords) -> str:
    """Both reviews described, the candidate, one question."""
    kw = "; ".join(keywords) if keywords else ""
    out = ["This project's two reviews:"]
    for kind, desc in reviews.items():
        out.append(f"  [{kind}] {desc}")
    out += ["", "Candidate source:", f"Title: {title}", f"Keywords: {kw}",
            f"Abstract: {abstract[:1200]}", "",
            "Which review is this a source for? Answer with the JSON object:"]
    return "\n".join(out)


def judge_purpose(brain: Brain, reviews: dict, *, title: str, abstract: str = "",
                  keywords=()) -> tuple:
    """Which review(s) this paper serves. Fails SAFE: anything unparseable yields (), which
    adds nothing and leaves gather's record standing."""
    if len(reviews) < 2:
        return ()
    try:
        raw = brain.coordinator(_purpose_prompt(reviews, title, abstract, keywords),
                                system=_PURPOSE_SYS, num_ctx=4096, think=False).strip()
        m = _JSON.search(raw)
        data = json.loads(m.group(0)) if m else {}
    except Exception:  # noqa: BLE001
        return ()
    got = data.get("serves")
    if isinstance(got, str):
        got = [got]
    if not isinstance(got, (list, tuple)):
        return ()
    return tuple(str(v).strip().lower() for v in got
                 if str(v).strip().lower() in reviews)


def judge_item(brain: Brain, topic: str, focus: str, *, key: str, label: str,
               title: str, abstract: str = "", keywords=(), asks=(),
               fetched_for: str = "", reviews: dict | None = None) -> Verdict:
    """One word-sense judgment for one paper. Fails SAFE: any error, or an unparseable reply,
    yields a TRANSFER (keep) at confidence 0 — the tool never quarantines on a bad signal."""
    try:
        # 4096 is a floor, not a ceiling: Brain grows the window when a prompt needs more.
        # It was pinned at 2048 when `focus` was a one-line search string, and when the
        # question became the author's research prompt plus the cycle's asks (5,005 chars)
        # every prompt overflowed — Ollama discarded the head, which is where the question
        # sits. Small matters here: this runs once per paper, and the KV cache is linear in
        # the window, so the coordinator's 16384 default would cost 8x the VRAM for nothing.
        # think=False, measured not assumed. The coordinator reasons by default, which is
        # right where a scratchpad changes the answer. Here it does not: over 8 DigiPros
        # papers judged both ways (2026-09-17) the keep/quarantine verdict agreed 8/8 and
        # confidence moved by at most a point, while the cost went 1,273s -> 112s per item.
        # That is 54 hours against 5 for a 154-paper corpus. The chain was not weighing the
        # evidence, it was restating the question before answering it — and the numbered
        # needs in _prompt already do that work, visibly: replies cite "Point [9]" directly.
        raw = brain.coordinator(
            _prompt(topic, focus, asks, title, abstract, keywords, fetched_for),
            system=_SYS, num_ctx=4096, think=False).strip()
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
    serves = judge_purpose(brain, reviews or {}, title=title, abstract=abstract,
                           keywords=keywords)
    return Verdict(key=key, label=label, kind=kind, confidence=conf,
                   term=str(data.get("term", "")), its_sense=str(data.get("its_sense", "")),
                   review_sense=str(data.get("review_sense", "")), serves=serves)


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
                 *, asks=(), fetched_for: dict | None = None, reviews: dict | None = None,
                 progress=None, checkpoint=None, checkpoint_every: int = 5
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
                           keywords=it.get("keywords", ()), asks=asks,
                           fetched_for=(fetched_for or {}).get(key, ""), reviews=reviews)
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
                  items: list[dict], outdir, project_root=None, dry_run: bool = False,
                  cache: dict | None = None, min_confidence: float = 7.0,
                  labels: dict | None = None, asks=(), fetched_for: dict | None = None,
                  reviews: dict | None = None, progress=None, checkpoint=None,
                  checkpoint_every: int = 5, quarantine_key: str | None = None) -> dict:
    """Judge the raw Zotero ``items`` and mark each confident false-friend ``quarantine`` in
    the corpus ledger (unless ``dry_run``), then write the reasons log.

    QUARANTINE IS A LEDGER ROLE, NOT A MOVE. It used to move the item into a shared
    ``quarantine`` collection, which took it out of the project collection — and `refs.bib`
    is built FROM that collection, so a minted review citing the paper silently acquired a
    dangling key. `ledger.reconcile` could not catch it either: narrative and bibliography
    shrink together. Now the item never leaves; only its role changes, and only ingestion
    reads roles.

    A LOCKED row is left alone. `--release` locks what it releases, so a paper the human
    put back is not quarantined again on the next run — which is what happened for as long
    as release was a move that left the verdict cache untouched.

    Pure but for the injected ``zc``/``brain`` and the ledger write, so the flow stays
    testable without the network. ``quarantine_key`` is accepted and ignored, for callers
    that have not been updated.
    """
    raw_by_key = {}
    judge_items = []
    for raw in items:
        f = _judge_fields(raw, labels)
        judge_items.append(f)
        raw_by_key[f["key"]] = raw
    flagged, verdicts = audit_corpus(brain, topic, focus, judge_items, cache=cache,
                                     min_confidence=min_confidence, asks=asks,
                                     fetched_for=fetched_for, reviews=reviews, progress=progress,
                                     checkpoint=checkpoint, checkpoint_every=checkpoint_every)
    moved: list[str] = []
    held: list[str] = []
    widened: list[str] = []
    if not dry_run and project_root is not None:
        # PURPOSE FIRST, AND ONLY EVER ADDED. A paper both reviews can use is the reason one
        # collection is worth keeping, and gather cannot see it: a methods search found the
        # paper against a methods anchor and never had the other question in hand. This does.
        # It may only widen what gather recorded — subtracting would drop a paper out of the
        # review that deliberately went and found it, which is a wrong quarantine without the
        # reversibility.
        for v in verdicts:
            for kind in v.serves:
                row = corpus_ledger.load(project_root).get(v.key)
                if row is None or kind in row.purpose:
                    continue
                corpus_ledger.add_purpose(project_root, v.key, kind, added_by="audit")
                widened.append(f"{v.label}+{kind}")
        rows = corpus_ledger.load(project_root)
        for v in flagged:
            cur = rows.get(v.key)
            if cur is not None and cur.locked:
                held.append(v.key)          # the human already ruled on this one
                continue
            data = (raw_by_key.get(v.key) or {}).get("data", {}) or {}
            corpus_ledger.set_status(project_root, v.key, corpus_ledger.QUARANTINE,
                                     title=data.get("title", ""), added_by="audit")
            moved.append(v.key)
    log = write_quarantine_log(outdir, flagged)
    return {"flagged": [v.key for v in flagged], "moved": moved, "held": held,
            "widened": widened, "verdicts": len(verdicts), "log": log}


def release_item(zc, *, project_root, key: str, role: str | None = None,
                 quarantine_key: str | None = None, project_key: str | None = None,
                 item: dict | None = None) -> bool:
    """Put one item back, and make it STICK.

    Nothing moves in Zotero — the item never left. Only the quarantine flag clears, and the
    row is LOCKED so the next audit cannot overrule the person. The paper's ROLE is untouched,
    which is what returns a released methods paper to the methods corpus: an earlier version
    stored quarantine AS the role, so quarantining destroyed the provenance and releasing put
    everything back into `literature` regardless of where it came from.
    """
    row = corpus_ledger.set_status(project_root, key, corpus_ledger.CORPUS, locked=True,
                                   added_by="human")
    return row.status == corpus_ledger.CORPUS


# ── the verb wiring (not unit-tested; the tested core is above) ────────────────

def _cache_path(paths):
    return paths.output / "audit_cache.json"


def _load_cache(paths, sig: str, units=()) -> dict:
    """Verdicts still usable for THIS question.

    A verdict is a judgement about one paper against one question, so changing the question
    ought to discard it — but not every change is equal, and the whole-corpus re-judge is the
    expensive thing here (236 papers, ~17 minutes each on elephantRoom).

    When the question only GAINED units — this cycle added an ask — a paper that transferred
    to the narrower question still transfers to the wider one: nothing was taken away that its
    verdict rested on. Only the quarantined papers can change, and they are few. When anything
    was removed or reworded, every verdict was made against a question that no longer exists
    and all of them go.

    A change to ``_FRAMING`` discards everything regardless. That reasoning is about what the
    question SAYS; a framing bump changes what the verdict MEANS, and the two are not the same
    kind of change.
    """
    p = _cache_path(paths)
    if not p.exists():
        return {}
    try:
        blob = json.loads(p.read_text())
    except Exception:  # noqa: BLE001
        return {}
    if int(blob.get("framing") or 1) != _FRAMING:
        n = len(blob.get("verdicts") or ())
        if n:
            print(f"  {runlog.stamp()}the audit's test changed — discarding {n} verdict(s) "
                  f"decided under the previous one", flush=True)
        return {}
    verdicts = blob.get("verdicts", {})
    if blob.get("sig") == sig:
        return verdicts
    was, now = set(blob.get("units") or ()), set(units or ())
    if was and now > was:               # strictly wider: keep what transferred, re-judge the rest
        kept = {k: v for k, v in verdicts.items() if v.get("kind") == "transfer"}
        dropped = len(verdicts) - len(kept)
        print(f"  {runlog.stamp()}question widened by {len(now - was)} clause(s) — keeping "
              f"{len(kept)} transfer verdict(s), re-judging {dropped} quarantined", flush=True)
        return kept
    return {}


def _save_cache(paths, sig: str, cache: dict, units=()) -> None:
    try:
        _cache_path(paths).write_text(json.dumps(
            {"sig": sig, "framing": _FRAMING, "units": sorted(units or ()),
             "verdicts": cache}, indent=1))
    except Exception:  # noqa: BLE001
        pass


def _clauses(text: str) -> list[str]:
    """The distinct claims a topic/focus line makes, in a canonical order.

    The focus is a semicolon/comma-separated list that `_write_gap_config` rewrites on most
    gathering cycles, so it accumulates, gets de-duplicated, and gets reordered. Those are
    edits to the WORDING, not to the research question, and the audit's verdicts do not
    depend on them.
    """
    import re
    seen, out = set(), []
    for c in re.split(r"[;,]", (text or "").lower()):
        c = " ".join(re.sub(r"[^\w\s]", " ", c).split())
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return sorted(out)


def _norm(text: str) -> str:
    """One clause, canonicalised: lowercased, punctuation flattened, whitespace collapsed."""
    import re
    return " ".join(re.sub(r"[^\w\s]", " ", (text or "").lower()).split())


def _sig(topic: str, focus: str, asks=()) -> str:
    """A STABLE signature of the research QUESTION, so a re-run reloads the cache instead of
    re-judging.

    Hashed over the normalised clause SET, not the literal string. Every verdict is a judgement
    about whether one paper's contribution transfers to this question, so the cache must survive
    an edit that leaves the question unchanged — and most edits do. elephantRoom's focus was
    cleaned up from 837 characters to 339 by removing clauses four cycles had duplicated; the
    question did not move, but the literal signature did, and the next audit re-judged all 236
    papers at ~17 minutes each. That cleanup cost about 51 GPU-hours. Adding a genuinely new
    clause still invalidates, which is the distinction worth drawing.

    Must not use builtin ``hash()``: string hashing is salted per process, so that signature
    changes every invocation and ``_load_cache`` would discard the cache every time — making
    the cache (and the incremental checkpoints) useless for resuming a long run.
    """
    import hashlib
    return hashlib.sha1("\x00".join(_units(topic, focus, asks)).encode("utf-8")).hexdigest()


def _units(topic: str, focus: str, asks=()) -> list[str]:
    """The question as a canonical set of units — what the signature hashes, and what a later
    run compares against to tell a widening from a rewrite.

    The stated question IS prose or a delimited list, so it splits. An ask is one sentence and
    stays whole: splitting it on its commas would make the signature turn on where a
    subordinate clause happens to fall, which is not a change to the question.
    """
    return (sorted({f"t:{c}" for c in _clauses(topic)})
            + sorted({f"q:{c}" for c in _clauses(focus)})
            + sorted({f"a:{n}" for n in (_norm(a) for a in (asks or ())) if n}))


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
    # The ledger lives beside the PROJECT, not the review — one collection, shared by
    # however many reviews the project has. No quarantine collection is created or used:
    # quarantining is a role now, and the item never leaves the project collection.
    project_root = config.work_root(directory).parent

    print(f"rabbitHole audit — {cfg.project_name}")

    if release:
        ident = release.lstrip("@")
        rows = corpus_ledger.load(project_root)
        target = None
        for raw in zc.collection_items(project_key):
            data = raw.get("data", {})
            key = data.get("key") or raw.get("key")
            if ident in ((key or ""), _zotero_label(data, "")):
                target = key
                break
        if target is None or rows.get(target) is None:
            print(f"  [warn] '{ident}' is not in this project's ledger.", file=sys.stderr)
            return 1
        if rows[target].status != corpus_ledger.QUARANTINE:
            print(f"  '{ident}' is not quarantined "
                  f"(purpose: {'+'.join(rows[target].purpose)}).")
            return 0
        ok = release_item(zc, project_root=project_root, key=target)
        print(f"  {'Released' if ok else 'FAILED to release'} {ident} back to "
              f"{cfg.project_name} — and LOCKED, so the next audit will not undo it.")
        return 0 if ok else 1

    brain = Brain(cfg.brain, gc, backend_override=brain_override)
    items = [it for it in zc.collection_items(project_key)
             if it.get("data", {}).get("itemType") not in ("attachment", "note")]

    # Level the ledger BEFORE judging, so quarantining writes into a complete picture
    # rather than creating the only rows there are. Without this, the first audit on a
    # project leaves a ledger holding nothing but its own quarantines.
    corpus_ledger.sync(paths, cfg, gc)
    total = len(items)
    print(f"  {runlog.stamp()}Judging {total} corpus item(s) for word-sense transfer"
          f"{' (dry run)' if dry_run else ''} — one model pass each; on this hardware allow "
          f"a few minutes per item. Progress below (resumable — verdicts are cached).", flush=True)

    # THE QUESTION IS WHAT THE AUTHOR SAID THE WORK IS, PLUS THIS CYCLE'S ASKS.
    #
    # Not `focus`. The focus exists to aim searches — breadth, synonyms, adjacent terms,
    # deliberately a wide net — and this verb needs the opposite: a tight boundary against
    # which a shared word can be judged a false friend. Reusing one string for both jobs made
    # the guard toothless. elephantRoom's audit 8 judged 236 papers against an 837-character
    # search string and quarantined none of them, in 65 hours.
    #
    # `research_prompt` is the author's own statement of the work, and it is stable: across
    # eleven elephantRoom configs the focus went 230 -> 526 -> 822 -> 837 -> 339 while the
    # prompt changed exactly once. Stability is not incidental — a question that churns every
    # cycle discards every cached verdict with it.
    #
    # The asks stay in: they are what the reviewer asked for that the prompt does not yet
    # name, and judging without them inverts the guard — a paper fetched FOR an ask, against a
    # question that never mentions the ask, is indistinguishable from a source that shares
    # vocabulary without transferring.
    asks = [str(t) for t in (getattr(cfg, "gather_topics", None) or []) if str(t).strip()]
    stated = (cfg.research_prompt or cfg.focus or "").strip()
    sig = _sig(cfg.topic, stated, asks)
    units = _units(cfg.topic, stated, asks)
    cache = _load_cache(paths, sig, units)
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

    # BOTH REVIEWS' DESCRIPTIONS, when the project has two. Assessing purpose needs the
    # question each review is asking, so this verb is project-scoped in a way the others are
    # not — it runs from one review's directory and has to look sideways at the other.
    reviews: dict[str, str] = {}
    for kind in config.REVIEW_KINDS.values():
        d = project_root / kind.dir
        if not d.is_dir() or not config.latest_project_file(d):
            continue
        try:
            other = config.load_project(d)
        except Exception:  # noqa: BLE001
            continue
        desc = (other.research_prompt or other.topic or other.focus or "").strip()
        if desc:
            reviews[kind.name] = desc[:900]
    if len(reviews) > 1:
        print(f"  {runlog.stamp()}Two reviews in this project — each paper is also asked which "
              f"it is a source for ({', '.join(reviews)}).", flush=True)
    else:
        reviews = {}

    summary = perform_audit(zc, brain, cfg.topic, stated, reviews=reviews,
                            project_key=project_key, project_root=project_root,
                            items=items, outdir=paths.output, dry_run=dry_run, cache=cache,
                            min_confidence=min_conf, asks=asks, progress=_report,
                            checkpoint=lambda c: _save_cache(paths, sig, c, units))
    _save_cache(paths, sig, cache, units)
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
              f"(dry run: the ledger is untouched).")
    else:
        print(f"  {runlog.stamp()}Marked {len(summary['moved'])}/{n} false-friend(s) "
              f"'{corpus_ledger.QUARANTINE}' in the corpus ledger — they stay in Zotero and "
              f"in refs.bib, and drop out of the corpus only. Reasons in "
              f"{summary['log'].name}; release any with `rabbitHole audit --release @key`.")
        if summary.get("widened"):
            print(f"  {runlog.stamp()}Widened {len(summary['widened'])} paper(s) to serve both "
                  f"reviews: {', '.join(summary['widened'][:6])}"
                  + (" …" if len(summary["widened"]) > 6 else ""))
        if summary.get("held"):
            print(f"  {runlog.stamp()}Left {len(summary['held'])} released paper(s) alone "
                  f"(locked by a human): {', '.join(summary['held'][:6])}")
    return 0

"""rabbitHole revise — apply reviewer annotations from a _ra.docx to re-draft the narrative.

Default mode is an in-place REDLINE: edit a copy of the annotated docx, answering each
comment with a rabbitHole-authored tracked change on the paragraph it anchors to, and
leave every reviewer comment in place so the next output shows them. The reviewer reads a
true tracked-changes redline beside their own notes. See `redline.py`.

Pass --resynth for the alternative clean rewrite (no tracked changes, comments dropped):
  1. Find *_ra.docx in output/ (or accept --file path)
  2. Extract tracked changes + comments from the docx
  3. Load existing notes from work/annotations/ and slim corpus from work/corpus.json
  4. Re-synthesise the narrative using the revision brief
  5. Re-locate the cited claims against the current full text (embedding retrieval,
     keyed by citekey) so the annotated bibliography stays verifiable
  6. Write output/*_ra.md and .docx
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

from . import config, corpus as corpus_mod, docxio, render, runlog
from .brain import Brain
from .models import Candidate
from .summarize import (
    _make_citekeys, _digest, bibliography, citation_check, read_notes,
    locate_claims, SYNTH_SYS, _enforce_paragraph_citations, _is_ok,
    _HAVE_CHROMA,
)


# ── output paths ───────────────────────────────────────────────────────────────

def _revision_paths(paths, annotated_docx: Path) -> tuple[Path, Path]:
    """Output paths: append _ra to the annotated file's stem."""
    stem = f"{annotated_docx.stem}_ra"
    return paths.output / f"{stem}.md", paths.output / f"{stem}.docx"


# ── load existing intermediates ───────────────────────────────────────────────

def _load_corpus(paths) -> list[Candidate]:
    if not paths.corpus_json.exists():
        return []
    data = json.loads(paths.corpus_json.read_text(encoding="utf-8"))
    return [Candidate.from_dict(d) for d in data]


def _load_notes(paths, n: int) -> list[dict]:
    notes = []
    for i in range(n):
        fp = paths.annotations_dir / f"{i:03d}.json"
        notes.append(json.loads(fp.read_text(encoding="utf-8")) if fp.exists() else {})
    return notes


# ── revision synthesis ────────────────────────────────────────────────────────

_REVISE_PROMPT = """\
Review topic: {topic}
Focus: {focus}

CURRENT NARRATIVE (the draft you are revising):
{narrative}

EVIDENCE DIGEST (ground truth — stay grounded in these sources):
{digest}

REVIEWER ANNOTATIONS (the ONLY changes to make):
{revision_context}

Produce the revised narrative by making the SMALLEST set of edits that fully \
addresses every annotation. This is an iterative revision, not a rewrite: the \
reviewer must be able to read your output against the current draft and see their \
comments — and nothing else — addressed.

Rules:
- Change ONLY what an annotation requires. Leave every other sentence, paragraph, \
section, heading, ordering, and citation exactly as it stands in the current \
narrative — word for word.
- Do NOT restructure, reorder, merge, split, or re-theme sections the reviewer did \
not flag. Do NOT swap in new sources, drop existing ones, or rewrite prose for style \
where no annotation calls for it.
- Where an annotation does require a change, make it precisely and locally, keeping \
the surrounding text intact.
- Maintain [@citekey] citation format throughout. Do not add a bibliography — that is \
generated separately.

Output only the revised narrative."""


# ── revision audit (replaces the fresh-synthesis peer-review critique) ─────────
# A revise must respond ONLY to the reviewer's annotations and leave everything else
# verbatim, so the reviewer can iterate against a stable draft. The fresh-synthesis
# critique loop is wrong here: its peer-review pass judges the draft against generic
# quality ideals and re-themes/merges/drops unflagged content. Instead we run an audit
# that holds the revision to the annotations themselves — every comment addressed, and
# nothing changed that no comment asked for — with the previous draft as the baseline.

_AUDIT_SYS = """\
You are a revision auditor. A literature-review draft has been revised in response to a
set of reviewer annotations. Your ONLY job is to check that the revision (a) fully and
genuinely addresses every annotation, and (b) changed nothing the annotations did not
call for. Judge against the annotations, not your own taste. Respond with ONLY a
numbered list of specific, actionable problems, one per line, quoting the text you mean.
If every annotation is adequately addressed and nothing else was altered, respond "OK"."""

_AUDIT_PROMPT = """\
Review topic: {topic}
Focus: {focus}

REVIEWER ANNOTATIONS (what this revision was supposed to do):
{revision_context}

PREVIOUS DRAFT (the baseline — everything not flagged should survive unchanged):
{previous}

REVISED DRAFT (under audit):
{narrative}

Check, against the annotations only:
1. Unaddressed comments — flag any annotation the revised draft does not yet adequately
   satisfy. Quote the annotation and say what is still missing or wrong.
2. Superficial fixes — flag any annotation answered in name only (e.g. a single word
   changed where the comment asked for a reworked claim or added evidence). Quote both
   the annotation and the weak fix.
3. Overreach — flag any substantive change from the previous draft that NO annotation
   called for: a section reordered, merged, split, re-themed or dropped; a source added
   or removed; a passage rewritten for style. Quote the changed text. The reviewer must
   be able to iterate against a stable draft, so unrequested changes are defects here.

Output: numbered list with quoted text; skip checks with no issues. If the revision
fully and only addresses the annotations, respond "OK"."""

_REVISE_FROM_AUDIT_PROMPT = """\
You revised a literature-review draft to address reviewer annotations, and an auditor
found the problems below. Fix every one: address any annotation still outstanding or
only superficially handled, and REVERT any change the auditor flags as unrequested back
to the previous draft's wording. Change nothing else. Maintain [@citekey] citation
format throughout. Do not add a bibliography — that is generated separately.

REVIEWER ANNOTATIONS:
{revision_context}

PREVIOUS DRAFT (the baseline to preserve where no annotation applies):
{previous}

CURRENT REVISED DRAFT:
{narrative}

Auditor's problems to fix:
{critique}

Output only the corrected narrative."""


def _audit_revise_loop(brain: Brain, cfg, previous: str, narrative: str,
                       revision_context: str, digest: str,
                       rounds: int | None = None) -> str:
    """Iterate audit→fix until the revision addresses every comment and only those.

    Each round audits the current draft against the reviewer annotations (with the
    previous draft as baseline) and, if anything is outstanding or overreaching, applies
    a focused fix. Stops early when the audit returns "OK", or after `rounds` rounds
    (default: brain.cfg.critique_rounds). Ends on the citation-coverage backstop."""
    if rounds is None:
        rounds = max(1, int(getattr(brain.cfg, "critique_rounds", 2)))

    for r in range(1, rounds + 1):
        tag = f" (round {r}/{rounds})" if rounds > 1 else ""
        print(f"  {runlog.stamp()}Auditing revision — comments addressed?{tag}...",
              flush=True)
        try:
            audit = brain.coordinator(
                _AUDIT_PROMPT.format(
                    topic=cfg.topic, focus=cfg.focus or "",
                    revision_context=revision_context,
                    previous=previous.strip(), narrative=narrative),
                _AUDIT_SYS, num_ctx=16384)
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] revision audit failed ({e}); skipping.", file=sys.stderr)
            break

        if _is_ok(audit):
            print(f"  {runlog.stamp()}Audit clean — every comment addressed, no overreach.",
                  flush=True)
            break

        print(f"  {runlog.stamp()}Revising to address audit{tag}...", flush=True)
        try:
            narrative = brain.coordinator(
                _REVISE_FROM_AUDIT_PROMPT.format(
                    revision_context=revision_context, previous=previous.strip(),
                    narrative=narrative, critique=audit.strip()),
                SYNTH_SYS, num_ctx=16384)
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] revision fix failed ({e}); keeping current.", file=sys.stderr)
            break

    # Hard backstop: no body paragraph may be citation-free (locate/bibliography
    # depend on it). No-op when every paragraph already cites a source.
    return _enforce_paragraph_citations(brain, narrative, digest)


# ── in-place redline revision (comment-preserving, tracked changes) ────────────
# An alternative to re-synthesising the whole narrative: edit a COPY of the annotated
# docx in place, answering each comment by rewriting only the paragraph it anchors to,
# recording every rewrite as a `rabbitHole`-authored tracked change with the comment
# left in place. The reviewer reads a true redline beside their comments. The deterministic
# docx surgery lives in `redline`; this is just the per-paragraph brain call.

_PARA_REVISE_SYS = """\
You are revising ONE paragraph of a scholarly literature review to satisfy a reviewer's
comment(s) on it. Rewrite the paragraph so it fully and genuinely addresses every
comment, grounded in the supplied evidence, while keeping its role in the surrounding
argument. House style: organise around ideas not sources; state a claim, then attach its
citation immediately after; never make a citation the grammatical subject; tight, active
prose. Keep the citation style EXACTLY as it already appears in the paragraph (do not
convert between styles). Only cite a source that appears in the EVIDENCE list. Output
ONLY the revised paragraph text — no heading, no list, no commentary, no bibliography."""

_PARA_REVISE_PROMPT = """\
Review topic: {topic}
Focus: {focus}

PARAGRAPH (revise only this):
{paragraph}

REVIEWER COMMENT(S) on this paragraph (address every one):
{comments}

EVIDENCE you may cite (author/year and what each offers):
{digest}

Output only the revised paragraph."""


def _redline_revise(brain: Brain, cfg, paths, docx: Path,
                    corpus: list[Candidate], notes: list[dict],
                    citekeys: dict[int, str]) -> tuple[Path, dict]:
    """Answer each anchored comment with an in-place, tracked-change paragraph rewrite.

    Returns (output_docx_path, summary). The brain is called once per commented
    paragraph; everything else (comment preservation, redline XML) is deterministic.
    """
    from . import redline
    anchors = redline.comment_anchors(docx)
    cmap = redline.comments_by_id(docx)
    digest = _digest(corpus, notes, citekeys)

    edits: list[dict] = []
    skipped: list[str] = []
    for a in anchors:
        comments = [cmap[i]["text"] for i in a["ids"] if i in cmap and cmap[i]["text"]]
        if not comments or not a["text"].strip():
            skipped.append(f"para {a['para']} (no text or no comment body)")
            continue
        print(f"  {runlog.stamp()}Revising para {a['para']} for "
              f"{len(comments)} comment(s)...", flush=True)
        prompt = _PARA_REVISE_PROMPT.format(
            topic=cfg.topic, focus=cfg.focus or "",
            paragraph=a["text"].strip(),
            comments="\n".join(f"- {c}" for c in comments),
            digest=digest)
        try:
            new_text = brain.coordinator(prompt, _PARA_REVISE_SYS, num_ctx=16384).strip()
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] para {a['para']} revise failed ({e}); leaving as-is.",
                  file=sys.stderr)
            continue
        if new_text:
            edits.append({"para": a["para"], "op": "replace", "text": new_text})

    _, out_docx = _revision_paths(paths, docx)
    summary = redline.apply_edits(docx, out_docx, edits, author="rabbitHole")
    summary["skipped_paras"] = skipped
    return out_docx, summary


def _synthesize_revision(brain: Brain, cfg, corpus: list[Candidate],
                         notes: list[dict], citekeys: dict[int, str],
                         current_narrative: str, revision_context: str,
                         style_profile: str = "") -> str:
    digest = _digest(corpus, notes, citekeys)
    prompt = _REVISE_PROMPT.format(
        topic=cfg.topic, focus=cfg.focus,
        narrative=current_narrative.strip(),
        digest=digest,
        revision_context=revision_context,
    )
    sys_prompt = SYNTH_SYS
    if style_profile:
        sys_prompt = (sys_prompt.rstrip()
                      + f"\n\nWRITING STYLE\nMatch the following author's voice and "
                        f"prose style throughout:\n{style_profile}")
    print(f"  {runlog.stamp()}Re-synthesising narrative (coordinator)...", flush=True)
    narrative = brain.coordinator(prompt, sys_prompt, num_ctx=16384)
    return _audit_revise_loop(brain, cfg, current_narrative, narrative,
                              revision_context, digest)


# ── orchestration ─────────────────────────────────────────────────────────────

def run(directory: str = ".", brain_override: str | None = None,
        docx_path: str | None = None, redline: bool = True) -> int:
    docxio.require_docx()
    t0 = runlog.start()

    cfg = config.load_project(directory)
    gc = config.load_global()
    paths = config.project_paths(directory)
    brain = Brain(cfg.brain, gc, backend_override=brain_override)

    print(f"rabbitHole revise — {cfg.project_name}")

    # 1. Find the docx to revise
    if docx_path:
        docx = Path(docx_path)
    else:
        docx = docxio.find_annotated_docx(paths)
    if not docx or not docx.exists():
        print("[error] No *_ra.docx found in output/. "
              "Specify one with --file or run 'rabbitHole report' first.", file=sys.stderr)
        return 1
    print(f"  Annotated file: {docx.name}")

    # 2. Extract annotations
    revision_context = docxio.build_revision_context(docx)
    if not revision_context:
        print("[warn] No tracked changes or comments found in the docx. Nothing to revise.")
        return 0
    n_comments = len(docxio.read_comments(docx))
    tc = docxio.read_track_changes(docx)
    print(f"  Found: {n_comments} comment(s), "
          f"{len(tc['deletions'])} deletion(s), {len(tc['insertions'])} insertion(s).")

    # 3. Read current narrative from the matching .md (same stem, without user initials)
    #    e.g. digipros_litreview_ra_DCR.docx → look for digipros_litreview_ra.md
    ra_stem = re.sub(r"_[^_]+$", "_ra", docx.stem)  # replace last suffix with _ra
    md_path = paths.output / f"{ra_stem}.md"
    if not md_path.exists():
        # Fall back to body text extracted from the docx itself
        current_narrative = docxio.read_body_text(docx)
    else:
        current_narrative = md_path.read_text(encoding="utf-8")

    # 4. Load corpus + notes. Pull in anything added to the Zotero collection since the
    #    last build (e.g. via `ingest` and your `collect` step). Append-only, so existing
    #    per-paper notes stay index-aligned; only the new tail gets annotated.
    corpus = _load_corpus(paths)
    if not corpus:
        print("[error] work/corpus.json not found — run 'rabbitHole report' first.",
              file=sys.stderr)
        return 1
    persist_needed = False
    if gc.have_zotero and cfg.zotero.get("collection_key"):
        new = corpus_mod.refresh_append(cfg, gc, paths, corpus)
        if new:
            corpus = corpus + new
            corpus_mod.persist(paths, corpus)
            print(f"  {runlog.stamp()}Corpus refresh: +{len(new)} new source(s) "
                  f"from Zotero; annotating…", flush=True)
            read_notes(brain, corpus, cfg, paths)
        # Heal a corpus first built before citekeys were captured: fill any empty
        # citekey from Zotero's Extra so the review cites the user's curated keys
        # instead of generated ones. Cheap metadata call; no re-ingest.
        filled = corpus_mod.backfill_citekeys(cfg, gc, paths, corpus)
        if filled:
            persist_needed = True
            print(f"  {runlog.stamp()}Backfilled {filled} Zotero citation key(s) "
                  f"into the cached corpus.", flush=True)
    if persist_needed:
        corpus_mod.persist(paths, corpus)
    notes = _load_notes(paths, len(corpus))
    citekeys = _make_citekeys(corpus)

    # 4b. Redline mode: edit the annotated docx in place with tracked changes, leaving
    #     comments anchored and un-flagged paragraphs untouched. Skips the full
    #     re-synthesis (steps 5-8) entirely.
    if redline:
        print()
        out_docx, summary = _redline_revise(brain, cfg, paths, docx,
                                            corpus, notes, citekeys)
        print()
        print("=" * 60)
        print(f" revise (redline) complete  [{runlog.fmt_dt(time.time() - t0)}]")
        print("=" * 60)
        print(f"  {summary['replace']} paragraph(s) revised as tracked changes, "
              f"{summary['comments_preserved']} comment(s) preserved.")
        if summary.get("skipped_paras"):
            print(f"  Skipped: {len(summary['skipped_paras'])} paragraph(s).")
        if out_docx.exists():
            print(f"  Review (docx): {out_docx}")
        return 0

    # 5. Style profile
    style_profile = ""
    if cfg.use_style:
        from .style import load_style_profile
        style_profile = load_style_profile()

    # 6. Re-synthesise
    print()
    narrative = _synthesize_revision(brain, cfg, corpus, notes, citekeys,
                                     current_narrative, revision_context, style_profile)

    # 7. Bibliography — re-locate each cited claim against the CURRENT full text so
    #    the annotated bibliography stays verifiable: a reviewer must be able to
    #    confirm every citation maps to non-hallucinated source text. The revision
    #    may drop or add sources, so this keys off the new citations. locate_claims
    #    caches per-citekey and only computes what's missing (embedding retrieval).
    print()
    print(f"  {runlog.stamp()}Locating cited claims for the annotated bibliography...")
    collection = None
    if _HAVE_CHROMA:
        try:
            from . import chroma as _chroma
            collection = _chroma.get_collection(paths.work / "chroma")
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] ChromaDB unavailable ({e}) — locate will use "
                  f"head-truncation", file=sys.stderr)
    located = locate_claims(brain, narrative, corpus, notes, cfg, paths,
                            collection=collection, citekeys=citekeys)
    biblio = bibliography(corpus, located)
    unmatched = citation_check(narrative, citekeys)
    if unmatched:
        print(f"\n[citation check] {len(unmatched)} unmatched citekey(s): "
              f"{', '.join(f'[@{k}]' for k in unmatched[:8])}"
              + (" ..." if len(unmatched) > 8 else ""))

    # 8. Write output
    out_md, out_docx = _revision_paths(paths, docx)
    from .render import build_markdown, pandoc_convert
    md_text = build_markdown(cfg, brain.backend, narrative, biblio, corpus, unmatched)
    out_md.write_text(md_text, encoding="utf-8")
    pandoc_convert(out_md, out_docx)

    print()
    print("=" * 60)
    print(f" revise complete  [{runlog.fmt_dt(time.time() - t0)}]")
    print("=" * 60)
    print(f"  Review (md)  : {out_md}")
    if out_docx.exists():
        print(f"  Review (docx): {out_docx}")
    return 0

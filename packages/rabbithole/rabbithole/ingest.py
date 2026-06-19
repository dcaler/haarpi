"""rabbitHole ingest — pull reviewer-supplied references into the corpus.

When a reviewer pastes references into the annotated docx ("I've added work from a
related project… add these citations"), those references live only as tracked-change
text — they are NOT in the Zotero collection or the corpus, so the next revise cannot
cite them. This step closes that gap:

  1. Extract the pasted references from the tracked insertions (coordinator).
  2. CHECK ZOTERO FIRST: for each reference, search the library; a match is added to
     the project collection and pulled into the corpus (with its PDF's full text),
     then annotated, so revise can cite it immediately.
  3. References NOT in Zotero are resolved against the APIs just enough to print a
     clean COLLECT LIST — the human adds them (with a PDF) at the `collect` step, and
     a later corpus refresh picks them up. rabbitHole never writes to your Zotero.

Corpus growth is append-only (new items go on the end), so existing per-paper notes
stay index-aligned.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from . import config, corpus as corpus_mod, docxio, sources
from .brain import Brain
from .models import Candidate, norm_doi, _norm_title
from .revise import _load_corpus


# ── reference extraction ─────────────────────────────────────────────────────

_EXTRACT_SYS = (
    "You extract bibliographic references from reviewer-supplied text. You respond "
    "with ONLY a JSON array and nothing else."
)

_EXTRACT_PROMPT = """\
A reviewer added text and comments to a literature-review draft, intending to fold in
work from a related project. Extract every distinct bibliographic reference they supplied
— whether written as a formal reference, an in-text citation with enough detail to
identify the work, or a DOI/URL.

Reviewer's additions:
{context}

Respond with a JSON array; one object per reference:
[
  {{"title": "the work's title (best you can read)",
    "authors": "author surnames as written, or empty",
    "year": 2021,
    "doi": "the DOI if present, else empty"}}
]
Rules:
- Only include real references the reviewer supplied. Do NOT invent references, and do
  NOT include the review's own existing citations or generic mentions with no identifying
  detail.
- year is an integer or null. Omit nothing else.
- If there are no supplied references, respond with [].
Respond with only the JSON array."""


def _extract_references(brain: Brain, context: str) -> list[dict]:
    raw = brain.coordinator(_EXTRACT_PROMPT.format(context=context), _EXTRACT_SYS,
                            num_ctx=16384)
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        arr = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return []
    refs = []
    for r in arr if isinstance(arr, list) else []:
        if not isinstance(r, dict):
            continue
        title = (r.get("title") or "").strip()
        doi = norm_doi(r.get("doi") or "")
        if not title and not doi:
            continue
        year = r.get("year")
        refs.append({"title": title, "authors": (r.get("authors") or "").strip(),
                     "year": int(year) if isinstance(year, int) else None, "doi": doi})
    return refs


# ── Zotero matching ──────────────────────────────────────────────────────────

def _zotero_match(zc, ref: dict) -> dict | None:
    """A library item matching this reference, by DOI then fuzzy title. Or None."""
    doi = ref["doi"]
    query = ref["title"] or ref["authors"] or doi
    items = zc.search(query, limit=25) if query else []
    # exact DOI wins
    if doi:
        for it in items:
            if norm_doi(it.get("data", {}).get("DOI", "")) == doi:
                return it
    # else best title overlap
    best, best_score = None, 0.0
    for it in items:
        t = it.get("data", {}).get("title", "")
        s = sources.title_overlap(ref["title"], t) if ref["title"] else 0.0
        if s > best_score:
            best, best_score = it, s
    return best if best_score >= 0.6 else None


# ── orchestration ─────────────────────────────────────────────────────────────

def _ref_line(ref: dict) -> str:
    bits = [ref["authors"], f"({ref['year']})" if ref["year"] else "", ref["title"]]
    line = " ".join(b for b in bits if b).strip()
    return f"{line} doi:{ref['doi']}" if ref["doi"] else line


def _collect_path(paths, docx: Path) -> Path:
    return paths.output / f"{docx.stem}_to_collect.md"


def run(directory: str = ".", brain_override: str | None = None,
        docx_path: str | None = None) -> int:
    docxio.require_docx()
    cfg = config.load_project(directory)
    gc = config.load_global()
    paths = config.project_paths(directory)
    brain = Brain(cfg.brain, gc, backend_override=brain_override)

    print(f"rabbitHole ingest — {cfg.project_name}")

    docx = Path(docx_path) if docx_path else docxio.find_annotated_docx(paths)
    if not docx or not docx.exists():
        print("[error] No annotated .docx found in output/. Pass --file.", file=sys.stderr)
        return 1
    print(f"  Annotated file: {docx.name}")

    context = docxio.build_revision_context(docx)
    if not context:
        print("[warn] No tracked changes or comments found. Nothing to ingest.")
        return 0

    print("  Extracting supplied references (coordinator)…", flush=True)
    refs = _extract_references(brain, context)
    if not refs:
        print("  No reviewer-supplied references found.")
        return 0
    print(f"  Found {len(refs)} supplied reference(s).")

    corpus = _load_corpus(paths)
    have_doi = {c.doi_key for c in corpus if c.doi_key}
    have_title = {c.title_key for c in corpus if c.title_key}

    if not (gc.have_zotero and cfg.zotero.get("collection_key")):
        print("[warn] No Zotero collection configured — cannot match against your library.",
              file=sys.stderr)
        zc, coll = None, ""
    else:
        from . import zotero
        zc = zotero.ZoteroClient(gc)
        coll = cfg.zotero.get("collection_key")

    added: list[Candidate] = []
    to_collect: list[dict] = []
    already: list[dict] = []
    idx = corpus_mod._load_candidate_index(paths)

    for ref in refs:
        # already in the corpus?
        if (ref["doi"] and ref["doi"] in have_doi) or \
           (ref["title"] and _norm_title(ref["title"]) in have_title):
            already.append(ref)
            continue

        item = _zotero_match(zc, ref) if zc else None
        if item is not None:
            zc.add_item_to_collection(item, coll)
            cand = corpus_mod._corpus_item_from_zotero(zc, item, idx, paths, quiet=True)
            if cand is not None:
                added.append(cand)
                if cand.doi_key:
                    have_doi.add(cand.doi_key)
                have_title.add(cand.title_key)
                print(f"    [zotero] +corpus: {cand.first_author_last} {cand.year or ''}")
                continue
            # matched but no usable PDF/full text → treat like a collect item
            print(f"    [zotero] matched but no full text: {ref['title'][:60]}")

        # not in Zotero (or no full text): resolve metadata for the collect list
        resolved = (sources.resolve_by_doi(ref["doi"], gc.contact_email) if ref["doi"]
                    else sources.resolve_by_title(ref["title"], gc.contact_email, ref["year"]))
        to_collect.append({"ref": ref, "resolved": resolved})
        print(f"    [collect] {_ref_line(ref)}")

    # Append matched items to the corpus (append-only) and annotate them.
    if added:
        full = corpus + added
        corpus_mod.persist(paths, full)
        print(f"\n  Annotating {len(added)} new source(s)…", flush=True)
        from .summarize import read_notes
        read_notes(brain, full, cfg, paths)  # writes notes for the new tail indices only

    # Write the collect list for the human / the `collect` step.
    if to_collect:
        lines = ["# References to add to Zotero (collect step)", ""]
        for entry in to_collect:
            r, res = entry["ref"], entry["resolved"]
            if res is not None:
                lines.append(f"- {res.full_citation()}")
            else:
                lines.append(f"- {_ref_line(r)}  _(could not resolve automatically)_")
        cp = _collect_path(paths, docx)
        cp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\n  Collect list written: {cp.name}")

    print()
    print("=" * 60)
    print(" ingest complete")
    print("=" * 60)
    print(f"  Already in corpus : {len(already)}")
    print(f"  Added from Zotero : {len(added)}")
    print(f"  To collect (you)  : {len(to_collect)}")
    return 0

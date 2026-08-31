"""rabbitHole `refresh` — recompute the load-bearing block on a draft that already exists.

The block ranks the sources the review rests on most and sits above the narrative. It is the
fastest way to judge whether the corpus is right, which is what the document exists to be read
for — so a draft without one costs its reader 15,000 words to answer a question nine lines
answer.

Drafts made before the block shipped cannot get one from the verbs that write it: `report`
regenerates the review from the corpus and DISCARDS every comment thread, and `revise` needs the
comments that reading is supposed to produce. This is the third option — recompute the block, put
it in, touch nothing else. One coordinator call, no re-draft, threads intact.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from . import config, docxio, redline, runlog
from .brain import Brain


def latest_draft(paths) -> Path | None:
    """The newest litreview .docx in output/ — the draft a reader would open.

    Deliberately not `find_annotated_docx`, which looks for the REVIEWER's copy: a refresh
    belongs on the tool's most recent output, whether or not it has been annotated since.
    """
    docs = [p for p in paths.output.glob("*litreview*.docx") if not p.name.startswith("~$")]
    return max(docs, key=lambda p: p.stat().st_mtime) if docs else None


def run(directory: str = ".", brain_override: str | None = None,
        file: str | None = None) -> int:
    t0 = runlog.start()
    docxio.require_docx()
    cfg = config.load_project(directory)
    gc = config.load_global()
    paths = config.project_paths(directory)

    docx = Path(file) if file else latest_draft(paths)
    if docx is None or not docx.is_file():
        print("[refresh] no litreview .docx found in output/ — nothing to refresh.",
              file=sys.stderr)
        return 1

    from .revise import _load_corpus
    from .summarize import _make_citekeys, top_sources_block
    corpus = _load_corpus(paths)
    if not corpus:
        print("[refresh] no corpus — run `rabbitHole build` first.", file=sys.stderr)
        return 1
    citekeys = _make_citekeys(corpus)
    brain = Brain(cfg.brain, gc, backend_override=brain_override)

    print(f"rabbitHole refresh — {cfg.project_name}")
    print(f"  {runlog.stamp()}[refresh] {docx.name} ({len(corpus)} sources)", flush=True)
    # Read the narrative with tracked changes ACCEPTED: the ranking must describe the review as
    # it now stands, including anything a redline or a graft added.
    narrative = redline.accepted_body_text(docx)
    print(f"  {runlog.stamp()}[refresh] ranking the load-bearing sources...", flush=True)
    block = top_sources_block(brain, cfg, narrative, corpus, citekeys)
    if not block.strip():
        print("[refresh] nothing is cited in this draft — no block to write.", file=sys.stderr)
        return 1

    summary = redline.replace_top_sources(docx, block)
    if summary.get("error"):
        print(f"[refresh] {summary['error']}", file=sys.stderr)
        return 1

    verb = "replaced" if summary.get("had_existing_block") else "inserted"
    print()
    print("=" * 60)
    print(f" refresh complete  [{runlog.fmt_dt(time.time() - t0)}]")
    print("=" * 60)
    print(f"  {verb} the load-bearing block: {summary['top_sources']} source(s)")
    print(f"  Review (docx): {docx}")
    print("  Nothing else in the document was touched; every comment thread is intact.")
    return 0

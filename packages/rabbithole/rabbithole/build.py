"""rabbitHole `build` — embed the audited Zotero collection into the working corpus.

The corpus-BUILD step, split out from `report`: read the collection (or ./pdfs/) into
candidates + full text, pull Better-BibTeX citekeys, index every paper in ChromaDB, and write
per-paper notes — persisting all of it under work/. `report` and `revise` then draft from what
this leaves behind, so embedding happens ONCE, from the already-audited collection, rather than
being redone inside each drafting run.

In the litreview chain it runs `collect → audit → build → revise`: the human finalises the
sources, audit quarantines lexical false-friends, then build embeds only the survivors.
"""
from __future__ import annotations

from . import config, runlog, summarize
from .brain import Brain


def run(directory: str = ".", *, from_folder: bool = False, refresh_notes: bool = True,
        brain_override: str | None = None) -> int:
    runlog.start()
    cfg = config.load_project(directory)
    gc = config.load_global()
    paths = config.project_paths(directory).ensure()
    brain = Brain(cfg.brain, gc, backend_override=brain_override)

    print(f"rabbitHole build — {cfg.project_name}")
    built = summarize.build_corpus(cfg, gc, paths, brain,
                                   from_folder=from_folder, refresh_notes=refresh_notes)
    if built is None:
        print("\nNo usable sources with full text. Add PDFs to Zotero / ./pdfs/ and re-run.")
        return 1
    corpus, _notes, _citekeys, _collection = built
    print(f"  {runlog.stamp()}Corpus built: {len(corpus)} sources embedded "
          f"(work/corpus.json, notes, ChromaDB). Ready for revise / report.")
    return 0

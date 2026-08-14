# rabbitHole `audit` — a word-sense corpus filter that quarantines lexical false-friends

## The problem (pydsk contamination)

pydsk's 102-entry corpus carries papers that share a *word* with the research question but have
**no conceptual bearing** on it: AutoDock Vina ("docking" = ligand–receptor binding), phyloseq /
QIIME2 ("reproducibility" = wet-lab microbiome protocols), ColabFold, a business-strategy paper.
"Explain docking" then answered with *molecular* docking, though the right paper ("Between
Replication and Docking: Adaptive Agents…") was in the corpus.

**Root cause.** `gather` ranks with `method="embedding"` by default — cosine similarity of
title+abstract+keywords against the topic. Cosine rewards shared **vocabulary**, so a homograph
from an unrelated field scores high and clears the floor. There is no word-sense discrimination
anywhere in the pipeline.

## The axis is word-sense, not domain

The author does heavy cross-disciplinary work, so a *domain-membership* filter is exactly wrong —
it would drop the physics-informing-social-model borrowings that are the point. AutoDock Vina is
droppable **not because it is chemistry** but because its "docking" is a homograph with **zero
transfer** to agent docking. The discriminator is: *does this paper's contribution (a finding, a
method, a concept) transfer to the research question, or does it merely share a term used in an
unrelated sense?* A chemistry paper whose method genuinely informs the model must **survive**.

## Two parts

### A. Reframe the relevance gate (`ranking._llm_rerank`)

The gate's own prompt rewards lexical coverage — *"a paper that substantially covers ANY ONE of the
component fields, methods, or concepts … should score ≥ 6."* That is the sentence that lets a
homograph in. Reframe the system prompt to score **conceptual transfer** to the research question,
name the homograph trap explicitly, and **bias toward keep** (demote only a confident false-friend;
genuine cross-disciplinary transfer scores high). This upgrades the `llm` ranking method for
`gather`/`report`. It does **not** help the `embedding` default — which is why the audit exists.

### B. `rabbitHole audit` — a word-sense pass over the existing corpus

Independent of how an item entered the corpus:

1. **Judge** each project-collection item with a conservative homograph check: `TRANSFER` vs
   `FALSE-FRIEND`, naming the shared term, *its* sense, the review's sense, and a confidence 0–10.
   Only a **confident** `FALSE-FRIEND` is flagged — bias to keep protects cross-disciplinary recall.
2. **Quarantine, don't delete.** Each flagged item is **moved** from the project collection to a
   single shared `quarantine` collection (find-or-create) via a new
   `Zotero.move_item_between_collections(item, from_key, to_key)` — one PATCH of the item's
   `collections` array, mirroring `add_item_to_collection`. The item stays in the **library**; since
   both `collection_items` (the corpus) and `collection_bibtex` (refs.bib) read the *project*
   collection, a moved item drops out of the review **and** the bibliography with no filter code.
3. **Log the reasons.** Write `output/audit_quarantine.md`: citekey, title, shared term, its sense,
   the review's sense, confidence — the "why" Zotero can't record. This is the audit trail.
4. **Cache verdicts** (per item key + a hash of topic/focus) so a re-run judges only new items and
   never re-litigates a decision.

`rabbitHole audit --release @key` moves an item back from `quarantine` to *this* project's
collection (the CLI is always project-scoped, so "this project" is unambiguous even with one shared
bin). `rabbitHole audit --dry-run` judges and writes the report but moves nothing.

## Grounding / safety laws

- **Never delete from the library.** The only Zotero mutation is a move between collections, always
  reversible. Delete-to-Trash is never issued.
- **Bias to keep.** Quarantine only on a *confident* `FALSE-FRIEND`. Anything ambiguous, any
  `TRANSFER`, any unparseable or failed judgment → **stays** in the corpus. A wrong keep costs a
  line in a review; a wrong drop costs a cross-disciplinary paper — so the asymmetry favors keep.
- **Every move is explained** in the reasons log; nothing leaves silently.

## Pipeline placement — audit reads text, so it runs before `build`

Audit judges from Zotero **metadata** (title/abstract/keywords), reasoning about word-*sense* —
it needs neither embeddings (cosine is what *caused* the contamination: a homograph is a near
neighbour) nor the full-text notes. So it runs the moment `collect` finishes, before anything is
embedded. To make that clean, corpus-building is split out of `report` into its own step/verb:

```
gather → collect → audit → build → revise         (a report re-draft embeds inline: no build step)
```

- **`collect`** (human) finalises the new sources in the Zotero collection.
- **`audit`** curates the *collection* — moves confident false-friends to `quarantine`. It never
  touches the embedded corpus.
- **`build`** embeds the *already-clean* collection once (candidates, citekeys, ChromaDB, notes),
  so the junk is never embedded in the first place — no wasted PDF-extraction, no prune needed.
- **`revise`** loads what `build` left behind. **`report`** still builds inline (so a report
  re-draft needs no separate `build` step, and standalone `report` is unchanged).

Because `build` is now its own step, `_estimate_hours` learns a clean median for *embedding*
separate from *drafting* — the blended `report` estimate de-blurs. (A count-scaled `build`
estimate, `per_paper × n_new`, is a further refinement gated on whether trundlr reflow re-estimates
a downstream task once its upstream completes.)

## Where each piece lives

- `rabbithole/ranking.py` — reframed `_llm_rerank` system prompt (part A).
- `rabbithole/zotero.py` — `move_item_between_collections(item, from_key, to_key)`.
- `rabbithole/audit.py` (new) — `run(directory, *, dry_run=False, release=None, brain_override=None)`:
  find-or-create quarantine, judge (cached), move, write the log, and `prune_corpus_json` as the
  standalone-safety net (drops quarantined papers from `work/corpus.json` by dedup_key, so a
  `revise` with no preceding `build` cannot resurrect them; notes are citekey-keyed, no reindex).
- `rabbithole/summarize.py` — `build_corpus()` extracted from `report`'s run (pure relocation).
- `rabbithole/build.py` (new) — the `build` verb over `build_corpus`.
- `rabbithole/plan.py` — `_STEP["build"]` / `_STEP["audit"]`; `_chain_for` inserts `audit` after
  `collect` and `build` before a `revise` re-draft.
- `rabbithole/cli.py` — `audit` and `build` subparsers.

## Frozen tests (GPU-free; brain + Zotero mocked)

Part A — `ranking`:
1. The reframed system prompt carries the transfer / homograph framing and **no longer** the lenient
   "≥ 6 for any component" line.
2. A FakeBrain scoring a homograph 1 and a transferable paper 8 → after `rank(method="llm")` the
   homograph falls below the floor and the transfer paper survives (plumbing + floor unchanged).

Part B — `audit` (a FakeZotero records every PATCH; a FakeBrain scripts verdicts):
3. One item judged `FALSE-FRIEND` (high confidence), the rest `TRANSFER` → exactly that item is
   moved (removed from the project key, added to the quarantine key); the others are untouched.
4. The reasons log names the flagged item and its sense-clash.
5. **Bias to keep** — a low-confidence `FALSE-FRIEND` is **not** moved.
6. **Fail-safe** — a brain exception on an item leaves it in the corpus (never quarantined).
7. `--dry-run` → judged and logged, **zero** moves.
8. `--release @key` → moved from `quarantine` back to the project collection.
9. **Cache** — a second run judges only the items without a cached verdict.
10. `move_item_between_collections` (zotero unit) — builds the right `collections` array (from
    removed, to added), issues the PATCH, and is idempotent if the item is already moved.

## Not in scope (yet)

- Making `report` LOAD a pre-`build` corpus instead of rebuilding inline (feasible — synthesis runs
  off notes, locate off ChromaDB — but a larger change to the critical drafting path; deferred).
- Count-scaled `build` duration (`per_paper × n_new`), pending trundlr reflow late-refresh support.
- Splitting `quarantine` per project — one shared bin is enough; provenance is recoverable from the
  add order, and release is project-scoped by the CLI's working directory.

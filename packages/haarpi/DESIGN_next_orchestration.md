# `haarpi next` — decompose, sequence, instruct

## The shift

Today `haarpi next` collapses the whole annotation set into **one tier**
(`cosmetic` / `gap_fill` / `redirection`) and runs that tier's **fixed template chain** from
`STAGE_TIERS` (e.g. `gap_fill → [gather, collect, revise, comment]`). One label, one canned
sequence, coarse instructions (an LLM re-invents `gather_topics` from the raw comments).

The general solution makes `next` do what the name implies — the three steps:

1. **Decompose** the comments into concrete tasks.
2. **Sequence** the agents + verbs those tasks require.
3. **Instruct** each verb with what it specifically must accomplish.

Open-loop, deliberately: **the human is the verification loop.** Each `next` cycle is a fresh
decomposition of the *current* annotation set; the author re-annotates and the next cycle
re-decomposes. No completion tracking, no escalation — that machinery is dropped.

## 1 · Decompose — the task list

One structured brain call over the unresolved comments returns a **per-comment task list**
instead of a single tier:

```
[ { "comment_ids": ["3"], "need": "sources", "query": "household distributional equity of climate ABM impacts" },
  { "comment_ids": ["4"], "need": "sources", "query": "consumption smoothing, savings drawdown, investment/innovation" },
  { "comment_ids": ["5"], "need": "section",  "query": "supply-chain reshoring and domestic production, unemployment" },
  { "comment_ids": ["10","11"], "need": "edit" } ]
```

`need ∈ {edit, sources, section, ingest, cite, redirect, correct}` (the litreview verb-need
vocabulary). Every comment lands in exactly one task; a `query` is required for
`sources`/`section`/`redirect`, and a `wrong`/`right` pair for `correct`.
The two source-provenance needs are distinct and easy to conflate:

- **`ingest`** — references the reviewer supplied that are **not yet in Zotero** (pasted text,
  "add @key", a reference list). They must be *fetched* before they can be cited.
- **`cite`** — papers the reviewer says are **already in the Zotero collection** ("I've added
  Doblinger 2019 and Howell 2017 to Zotero, cite them"). Nothing is fetched; they only need
  *embedding*. A deterministic floor (`_CITE_ASK`) promotes an explicit "already in Zotero / the
  collection / the library" assertion to `cite` no matter what the 8B model called it — the exact
  DRvehicle misroute, where "cite Doblinger and Howell" was filed as `ingest` and the fetch
  (which drops author-only mentions) reported "none found."

This is the "list all the tasks it needs to do."

## 2 · Sequence — derive the chain from the tasks

The chain is **built from the task set**, not looked up from a tier template. Verbs are unioned
in dependency order:

- any `sources`/`section`/`redirect` task → prepend **`gather → collect`**; an `ingest` task with
  no gather still gets a **`collect`** (the human finalises any reference the fetch missed);
- any chain with a **`collect`** (the corpus changed) → **`audit`** it right after (word-sense
  quarantine of lexical false-friends), then, for a `revise` re-draft, **`build`** it immediately
  before revise — because `revise` loads a *cached* corpus and no longer embeds; embedding lives
  solely in `build`;
- a **`cite`** task (papers already in Zotero) → a lone **`build`** before revise, with no
  gather/collect (nothing to fetch) and no audit (deference forbids quarantining sources the
  reviewer named);
- the redraft verb is **`graft`** if any `section` task exists, **`report`** only for a
  `redirect`, else **`revise`**. `report` re-plans the review's sections from the corpus *and
  embeds inline*, so a report chain never gets a separate
  `build`), otherwise **`revise`** (in-place per-comment edits);
- a `redirect` task also **rewrites the brief** before gather;
- a `correct` task adds **no step at all**. A reviewer correcting a name is saying the project
  has a fact wrong, and the fix is one deterministic substitution — applied by the planner, before
  the chain is queued, across `haarpi.yaml:brief`, the litrev config's
  `topic`/`focus`/`research_prompt` and the current `*_litreview_ra.md` (re-rendered to .docx).
  Queueing it would make a one-right-answer edit depend on a task running, which is the failure it
  exists to fix: a span-local reviser corrected one of six occurrences of a wrong model name and
  left the brief — the string every gather searches with — untouched, so the next cycle re-injected
  it. A correction that matches nothing is reported as `NOTHING MATCHED` rather than passing
  silently; the counts land on the plan record. When a chain's only need is `correct`, no redraft
  is queued at all.
- always end **`… → comment → next`**.

**The embedding contract, enforced structurally:** a `revise` re-draft never reads a corpus
`build` has not touched. Every path that changes the corpus (`collect` present) or cites
reviewer-added papers (`cite`) carries a `build` immediately before revise or graft; `report` paths are
exempt because they embed inline. This is the invariant `test_a_revise_redraft_never_reads_an_
unembedded_corpus` pins.

**Tier becomes derived, not primary:** `cosmetic` = every task is `edit`; `gap_fill` = ≥1
`sources`; `redirection` = ≥1 `redirect`. So everything downstream that keys on tier — the
`confirm_tiers=[redirection]` human-approval gate, notifications — keeps working, now as a
*summary* of the decomposition rather than its driver.

## 3 · Instruct — per-verb payloads from the tasks

- **`gather`** ← `gather_topics` = the `query` of every `sources`/`section` task. The specific
  comments that need sources deterministically drive and steer the gather — not an LLM's
  whole-set guess. (This is the elephantRoom fix, generalized to all comment types.)
- **`graft`** ← the `section` queries as focus additions, and the requesting comment's own
  anchor as the insertion position.
- **`report`** ← the `redirect` query as a focus addition (its only channel from the comments,
  since `report` does not read the docx).
- **`revise`** ← the docx itself, for the reviser's own per-comment routing (unchanged).
- **`redirect`** ← the rewritten brief.

## Known limitation (preserved, made explicit)

### Why a section no longer costs a re-draft

`graft` exists because rework was scaled to the heaviest ask in a set rather than to the ask
itself. One `section` need sent the whole annotation set to `report`, which re-plans and
regenerates every section — a second full read of a 27-page document to see two new ones, with
every comment thread discarded because `report` writes a fresh .docx with no anchors.

`graft` drafts only the requested strand and splices it into a COPY of the reviewer's own .docx
as a tracked insertion. Existing paragraphs are never passed to a model, so they come through
byte-identical and the comment threads survive. That is also what makes the diff mean anything to
the redline engine: a document that changes 100% every cycle gives it nothing to anchor against.

Position, in priority order: the requesting comment's own anchor (deference — where the reviewer
wrote is a statement about where the ask belongs); else the nearest existing section by
embedding; else the end of the review, **reported to the human**, never silent.

`report` regenerates the review from the corpus and does not read the redline, so a cycle that
adds a section cannot also carry in-place sentence edits — **section-add dominates a cycle.**
This is current behaviour; the decomposition surfaces it honestly (a `section` task in the set
routes the redraft to `report`) rather than hiding it. Handling mixed section+edit in one pass is
out of scope here.

## Consolidation — `haarpi next` is the sole litreview planner (2026-08-14)

rabbitHole once had its **own** planner (`rabbithole/plan.py`, the `rabbitHole parseNplan` verb;
also invoked inline by `revise`'s auto-requeue). It collapsed the whole annotation set into one
tier — the exact approach this decompose path replaced — and it **diverged**: `plan.py` learned to
distinguish already-in-Zotero papers (embed-and-cite) while `decompose` did not, so a fix landed in
the layer the pipeline never runs. The pipeline queues `haarpi next`, never `parseNplan`.

That duplicate is now **retired**. `plan.py` and rabbitHole's own trundlr client are deleted; the
`parseNplan` CLI verb is gone; `revise` is **draft-only** (its `--no-queue` flag is kept for
compatibility but does nothing — revise never re-plans). The one piece that stayed in rabbitHole is
the litreview config-steering writer, moved to `rabbithole/steering.py` (`_write_gap_config` /
`_write_section_config`), which haarpi soft-imports. Porting `plan.py`'s `zotero_additions` rule
here as the `cite` need — and bringing `audit`/`build` into the chain, which `decompose` had been
missing — is what closed the divergence *and* the live embedding regression (revise stopped
embedding, but the live chain had no `build`).

## Scope & blast radius

`classify` and `STAGE_TIERS` are **shared with the paper ladder** (raconteur, with its own
deliverable tier tables). The decompose path is the **litreview** planner; the paper stage stays on
the tier classifier. The paper ladder is untouched.

## Decisions to confirm

1. **Per-comment decomposition replaces the whole-set tier as the primary step**; tier becomes a
   derived summary. (The general solution.) Recommend: yes.
2. **Litreview first**, paper ladder unchanged for now. Recommend: yes (safe blast radius; the
   observed failure and the retry are both litreview).
3. ~~**Accept the `report` section-dominates-a-cycle limitation**~~ — SOLVED by `graft`; a
   section no longer dominates a cycle. Was: (surface it, don't solve
   it). Recommend: yes.

## Build order (each step ends green)

1. **DONE** — `decompose(comments, cfg)` + `_DECOMPOSE_PROMPT` + `_normalise_tasks` (total-coverage
   guarantee, safe `edit` fallback on a bad parse). In `haarpi/planner.py`.
2. **DONE** — `chain_from_tasks(tasks)` — the deterministic sequencer + instruction builder
   (union of verbs, `report`-if-section, derived tier, seeded `gather_topics`/`section_focus`).
3. **DONE** — `_write_litreview_steering` — the instruct hand-off: soft-imports rabbitHole's
   `_write_gap_config`/`_write_section_config` to write the numbered litrev config that steers the
   gather (the channel the classify path never wrote). Defensive: a write failure warns and runs
   unsteered, never crashes the gate.
4. **DONE** — wired into `run_next`: litreview → `decompose → chain_from_tasks → steer`; every
   other stage keeps `classify`. queueing / notify / confirm-gate / loop-guard intact.
5. **DONE** — `tests/test_next_orchestration.py` (14) incl. the elephantRoom-shaped set;
   `test_pipeline.py` updated to the task-list reply shape.

Suites: haarpi 243, rabbitHole 135, raconteur 630. Not yet committed.

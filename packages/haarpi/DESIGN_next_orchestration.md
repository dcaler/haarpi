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

`need ∈ {edit, sources, section, redirect}` (the litreview verb-need vocabulary). Every comment
lands in exactly one task; a `query` is required for `sources`/`section`/`redirect`. This is the
"list all the tasks it needs to do."

## 2 · Sequence — derive the chain from the tasks

The chain is **built from the task set**, not looked up from a tier template. Verbs are unioned
in dependency order:

- any `sources` or `section` or `redirect` task → prepend **`gather → collect`**;
- the redraft verb is **`report`** if any `section`/`redirect` task exists (it re-plans the
  review's sections from the corpus), otherwise **`revise`** (in-place per-comment edits);
- a `redirect` task also **rewrites the brief** before gather;
- always end **`… → comment → next`**.

**Tier becomes derived, not primary:** `cosmetic` = every task is `edit`; `gap_fill` = ≥1
`sources`; `redirection` = ≥1 `redirect`. So everything downstream that keys on tier — the
`confirm_tiers=[redirection]` human-approval gate, notifications — keeps working, now as a
*summary* of the decomposition rather than its driver.

## 3 · Instruct — per-verb payloads from the tasks

- **`gather`** ← `gather_topics` = the `query` of every `sources`/`section` task. The specific
  comments that need sources deterministically drive and steer the gather — not an LLM's
  whole-set guess. (This is the elephantRoom fix, generalized to all comment types.)
- **`report`** ← the `section`/`redirect` queries as focus additions (its only channel from the
  comments, since `report` does not read the docx).
- **`revise`** ← the docx itself, for the reviser's own per-comment routing (unchanged).
- **`redirect`** ← the rewritten brief.

## Known limitation (preserved, made explicit)

`report` regenerates the review from the corpus and does not read the redline, so a cycle that
adds a section cannot also carry in-place sentence edits — **section-add dominates a cycle.**
This is current behaviour; the decomposition surfaces it honestly (a `section` task in the set
routes the redraft to `report`) rather than hiding it. Handling mixed section+edit in one pass is
out of scope here.

## Scope & blast radius

`classify` and `STAGE_TIERS` are **shared with the paper ladder** (raconteur, with its own
deliverable tier tables). This redesign lands for the **litreview stage first**: a new
`decompose` path builds the litreview chain from tasks, while the paper stage stays on the
existing tier classifier until we deliberately extend it. The paper ladder is untouched.

## Decisions to confirm

1. **Per-comment decomposition replaces the whole-set tier as the primary step**; tier becomes a
   derived summary. (The general solution.) Recommend: yes.
2. **Litreview first**, paper ladder unchanged for now. Recommend: yes (safe blast radius; the
   observed failure and the retry are both litreview).
3. **Accept the `report` section-dominates-a-cycle limitation** for now (surface it, don't solve
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

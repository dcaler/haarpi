# rabbitHole `mindmap` — a per-draft contribution map + coverage diagnostic

## What it is

A rabbitHole verb that reads the **current litreview draft** (or the minted release) and renders a
**contribution map** into the review's own outputs (`litReview/output/`). It is a *diagnostic the
author consults while writing*, not a mint-only deliverable: it surfaces, every revise cycle,
where the review actually invests its argument and which themes are peripheral to its core — while
there is still time to act. It does **not** enter the shared `figures/` pool (nothing here reaches a
paper or deck). The last draft's map is also the minted map, so nothing is lost at release.

Diagram-as-code, no diffusion: deterministic geometry + batched model calls for the node phrases.

`compose` runs **one call per chunk of papers within a theme** (`_COMPOSE_BATCH = 25`), never one
call for the review. A single whole-review call does not fit: a 160-paper review built a
14.7k-token prompt and then needed ~5.6k tokens of JSON back, against a 16k window — the reply
truncated, the repair pass re-sent the same oversized prompt and truncated identically, and the map
shipped with **zero** papers, a blank diagnostic that still looked like a rendered figure. Batching
also contains failure: an unparseable batch costs its own papers, not the whole map. Each paper is
composed once, under the first theme that cites it.

## The map (what the reader sees)

One node per cited paper, laid out so that **four orthogonal signals** read at a glance:

- **Radial band = importance to THIS review.** Papers rank by `evidence_weight` (the words of review
  prose devoted to them) and split into four bands by fixed **quantiles of the project corpus** —
  `BAND_QUANTILES = (0.05, 0.25, 0.50)` — separated by radial gaps. Three **red rings** sit in the
  gaps, so *exactly* the top 5% / 25% / 50% of the corpus fall inside each. The innermost ring is
  the same slice the review's **Most load-bearing sources** header prints, so map and header are one
  ranking.

  The rings are **not a budget.** They used to be (`target_min`/`target_max`), and the legend said
  "papers outside exceed it" — a false claim about a healthy review, since a litreview is a coverage
  instrument and exceeding a reference target when the work asks for it is correct. They now report
  where a paper sits in the corpus by importance, which is a fact rather than a verdict. Band
  membership
  is the diagnostic; radius *within* a band is just packing.
- **Angle = theme.** Each theme owns an angular **pie slice**. A theme with no core papers shows an
  empty inner slice — the signal that the topic is peripheral to the review's argument.
- **Blob size = total (field-wide) citations** — OpenAlex `cited_by_count`, log-scaled.
- **Black ring thickness = citations *within* the corpus** — the in-degree of the real OpenAlex
  citation graph (how many of *these* papers cite it).

Faded grey arrows overlay the **real** citation graph (A→B iff B ∈ A's OpenAlex `referenced_works`).
A centre hub carries the title; theme labels sit outside the outermost ring; a legend decodes every
channel.

Because the four signals are orthogonal, the map is a diagnostic: a paper can be *famous but
peripheral here* (big, outside the rings — e.g. a canonical reference the review barely discusses),
or *central but new* (inside the rings, small, no black ring — recent work the review leans on hard
that the field has not caught up to).

## The node phrase — the qualitative contribution

Each node carries its `Author Year` citation and a one-line **contribution**: *qualitatively and at a
high level, what we now know from this paper that we did not before*. Not the topic ("Examines how
proximity affects sorting"), not a context-free statistic ("b=0.74"): the claim, in plain words
("Physical proximity to bins matters more than attitudes"). This is a **rewriting** task — the
review's prose carries the claim woven around statistics and connectives — so it is the map's one
model call (production GPU): the coordinator distils each paper's contribution from the review
sentences that cite it (`citation_evidence`), prompted to forbid method-openers *and* statistics and
grounded strictly in that paper's evidence. A draft may render **labels-only** (empty phrase) when a
GPU-free pass is wanted; the layout carries the diagnostic without the phrases.

## Where correctness lives

The deterministic scaffold is hardened and unit-tested; the one LLM step (the phrases) concentrates
the risk. Everything else — importance ranking, band geometry, the citation graph, the target rings,
the collision-free layout — is pure and frozen by tests before any live model run.

**Grounding law** (mirrors the redline contract). Every `key` MUST be a citekey in
`litReview/output/refs.bib`. `validate()` drops any paper with an unknown or duplicate key, coerces
`theme` to a real thread, and takes the `Author Year` label from `refs.bib`. The model may summarise,
never invent a paper; a reply with no usable JSON yields a labelled-stub map, never a crash.

## Data sources

- **`evidence_weight(review_md)`** — deterministic. Per paper, the total words of review prose in the
  sentences that cite it. Drives the importance ranking / band membership.
- **`citation_evidence(review_md)`** — deterministic. The citing sentences per paper, fed to the
  model as the grounding for its contribution phrase.
- **`citation_graph(papers, dois, email)`** — OpenAlex (`referenced_works` + `cited_by_count`),
  deterministic given the injected `fetch`. Yields the real citation edges (faded arrows + black-ring
  in-degree) and the field-wide citation counts (blob size). Papers without a DOI / OpenAlex record
  simply get no arrows and no size — an honest gap, never an invented link.
- **the model** — one coordinator call for the contribution phrases (production GPU).

## Pipeline

`current litreview .md` (draft or minted) + `refs.bib`
 → `parse_threads` (the `## ` thesis threads, minus the Narrative-Review wrapper and the bibliography)
 → `bib_keys` / `bib_dois` (the grounding sets)
 → **model** distils a contribution per paper from its citing sentences (`compose`, grounded)
 → `evidence_weight` (importance) + `citation_graph` (edges, sizes) overlaid on the papers
 → `band_layout` → `to_dot` (importance bands as theme pie-slices, packed rings, collision-scaled to
   zero overlap, native red rings at the corpus quantiles, centre hub, outside labels)
 → `_render_pinned` renders the pinned coordinates with **`dot -Kneato -n2`** (positions are final;
   no layout) → `emit` writes `…_litmap_ra.{dot,svg,png}` into `litReview/output/` (same `_ra`-only
   clobber guard and Inkscape hand-edit workflow as the figures pool, but **not** the pool).

## Per-draft integration

`mindmap` is a step in the litreview chain, queued automatically after every re-draft and before the
author reviews:

- opening chain: `gather → collect → report → mindmap → comment`
- rework chain (`planner.chain_from_tasks`): `… → revise|report → mindmap → comment`

so the diagnostic lands beside each `_ra` draft. `run()` consumes the newest `*_litreview*.md`
(`_find_review_md`) and skips gracefully when `refs.bib` is not there yet.

## Frozen tests (all GPU-free; the brain is mocked)

1. `parse_threads`, `bib_keys`, `bib_dois` — parsing → themes/citekeys/labels/DOIs.
2. `validate` — the grounding law (invented/duplicate keys dropped; theme coerced; never raises);
   reads the `contribution` key (with `finding`/`phrase` fallback).
3. `citation_evidence` / `evidence_weight` — citing-sentence extraction and per-paper prose weight
   (bibliography + headings excluded; tags stripped).
4. `citation_graph` — the real reference graph from a fake `fetch` (edges + `cited_by_count`), with
   non-corpus and DOI-less papers correctly dropped.
5. `band_layout` — bands by importance (most-discussed nearest the centre), the target rings holding
   **exactly** each quantile cut, and a collision-scale that leaves **zero** overlapping
   boxes across varied sizes and themes.
6. `to_dot` — a pinned `digraph` with the centre hub, a red ring per quantile cut, theme labels, size +
   black-ring encoding, faded citation arrows, and a legend; renders under `dot -Kneato -n2`.
7. `build_spec` against a **fake brain** → the composed, grounded `FigureSpec` is deterministic.

## Not in scope (yet)

- The live model run for the real contribution phrases (needs a free GPU) — the deterministic map is
  frozen and rendered before any live model run.
- Figure placement into the paper (a contribution map never enters a paper).

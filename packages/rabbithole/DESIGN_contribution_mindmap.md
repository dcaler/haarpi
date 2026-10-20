# rabbitHole `mindmap` — a per-draft contribution map + coverage diagnostic

## What it is

A rabbitHole verb that reads the **current litreview draft** (or the minted release) and writes a
**contribution map** — one self-contained, interactive HTML page — into the review's own outputs
(`litReview/output/`). It is a *diagnostic the
author consults while writing*, not a mint-only deliverable: it surfaces, every revise cycle,
where the review actually invests its argument and which themes are peripheral to its core — while
there is still time to act. It does **not** enter the shared `figures/` pool (nothing here reaches a
paper or deck). The last draft's map is also the minted map, so nothing is lost at release.

Diagram-as-code, no diffusion: deterministic geometry + batched model calls for the node phrases.

**A page, not a picture (2026-10-20).** The map used to be a pinned graphviz figure (`.dot` →
`.svg` → `.png`) with every paper's contribution sentence printed beside its node, embedded in
the review. Keeping 224 such labels from overlapping scaled DigiPros' canvas to 64,000 pt
square; rasterised to 1600 px for the docx, every node was a speck. Now each paper is a small
dot whose `Author Year` label appears on zoom; the sentence is a hover tooltip, and a click opens
a panel with the paper's facts and page links. The layout packs tight because no text has to
fit beside a dot. The page inlines a vendored vis-network (`assets/vis-network-9.1.9.min.js`),
so it opens offline, and the review **links** to it rather than embedding it.

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
- **Angle = theme (narrative) or topic (fact base).** Each owns an angular **pie slice**. A slice
  with no core papers shows an empty inner wedge — the signal that the topic is peripheral to the
  review's argument. A fact base's slices are its approved **topics** (T1…Tn), not its themes:
  DigiPros' 72 themes drew as 59 slices of 6° each, with no visible structure. Each source sits in
  one slice (`facts.map_slice`: the topic its counted facts score highest on, preferring one that
  serves a research question); sources with no useful fact share a grey slice.
- **Dot size = total (field-wide) citations** — OpenAlex `cited_by_count`, log-scaled.
- **Dark ring thickness = citations *within* the corpus** — the in-degree of the real OpenAlex
  citation graph (how many of *these* papers cite it).

Faded grey arrows overlay the **real** citation graph (A→B iff B ∈ A's OpenAlex `referenced_works`).
Slice titles sit outside the outermost ring; the side panel holds a legend, a search box, a
band filter (only the top 5% / 25% / 50%), a checkbox per slice, and the selected paper's panel:
its contribution, slice and band, citation counts, and — for a fact base — its fact counts, the
questions it serves, its best three facts with their page links, and an *Open in Zotero* link.

Because the four signals are orthogonal, the map is a diagnostic: a paper can be *famous but
peripheral here* (big, outside the rings — e.g. a canonical reference the review barely discusses),
or *central but new* (inside the rings, small, no black ring — recent work the review leans on hard
that the field has not caught up to).

## The node phrase — the qualitative contribution

Each node carries its `Author Year` citation and, in its tooltip, a one-line **contribution**: *qualitatively and at a
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
the packed layout — is pure and frozen by tests before any live model run.

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

`current litreview draft` (`.md`, or the `.docx` a redline revise left) + `refs.bib`
 → `_read_review` (a docx is read with insertions applied, deletions gone, `Heading N` → `#`×N)
 → `parse_threads` (the `## ` thesis threads, minus the Narrative-Review wrapper, the load-bearing
   front block, and the bibliography)
 → `bib_keys` / `bib_dois` (the grounding sets)
 → **model** distils a contribution per paper from its citing sentences (`compose`, grounded)
 → `evidence_weight` (importance) + `citation_graph` (edges, sizes) overlaid on the papers
 → `band_layout` → `map_data` (importance bands as pie-slices of packed dots, red rings at the
   corpus quantiles, slice-title anchors, per-paper details) → `to_html` (the data and the vendored
   vis-network inlined into one page; physics off, so the positions are final)
 → `emit` writes `…_litmap_ra.html` into `litReview/output/` (**not** the figures pool). A fact
   base's `report` links it from the review; the inputs come from `facts.map_inputs` (one thread
   per topic) and `facts.map_details` (facts with page links and Zotero links, read from the
   sidecar).

## Per-draft integration

`mindmap` is a step in the litreview chain, queued automatically after every re-draft and before the
author reviews:

- opening chain: `gather → collect → report → mindmap → comment`
- rework chain (`planner.chain_from_tasks`): `… → revise|report → mindmap → comment`

so the diagnostic lands beside each `_ra` draft. `run()` consumes the newest `*_litreview*` draft
(`_find_review`) and skips gracefully when `refs.bib` is not there yet.

**The draft is not always markdown.** A redline revise writes tracked changes into the docx and
returns before step 8, where the `.md` is written — deliberately: the accepted text is the
reviewer's to settle, and an `.md` written there would become the "current narrative" the next
revise reads, treating every proposal as settled. So the map reads the docx instead, insertions
applied and deletions gone — *if this redline were accepted, here is the shape of the review*.
Globbing `*.md` alone meant the map died with "render a draft first" after every redline cycle.

## Frozen tests (all GPU-free; the brain is mocked)

1. `parse_threads`, `bib_keys`, `bib_dois` — parsing → themes/citekeys/labels/DOIs.
2. `validate` — the grounding law (invented/duplicate keys dropped; theme coerced; never raises);
   reads the `contribution` key (with `finding`/`phrase` fallback).
3. `citation_evidence` / `evidence_weight` — citing-sentence extraction and per-paper prose weight
   (bibliography, load-bearing front block, and headings excluded; tags stripped).
4. `citation_graph` — the real reference graph from a fake `fetch` (edges + `cited_by_count`), with
   non-corpus and DOI-less papers correctly dropped.
5. `band_layout` — bands by importance (most-discussed nearest the centre), the rings holding
   **exactly** each quantile cut, no two dots overlapping, and a DigiPros-sized map (224 papers,
   10 slices) packing inside a radius of 1500 px.
6. `map_data` / `to_html` — one self-contained page (script inlined, nothing able to close the
   script early), a ring per quantile cut, a palette hue per slice, size + ring encoding, the
   citation edges, and only the known detail fields reaching the click panel.
7. `build_map` against a **fake brain** → the composed, grounded `Mindmap` is deterministic;
   `make_map(pin_slices=True)` keeps each paper in the slice that lists it.
8. `test_docx_draft.py` — the docx-only draft: a tracked insertion is not invisible, a deletion
   stays gone, `Heading N` comes back as `#`×N and parses as the same threads, and `_find_review`
   picks the docx when a redline left no markdown (preferring a same-stem `.md` when there is one,
   ignoring Word's `~$` lock files).

## Not in scope (yet)

- The live model run for the real contribution phrases (needs a free GPU) — the deterministic map is
  frozen and rendered before any live model run.
- Figure placement into the paper (a contribution map never enters a paper).

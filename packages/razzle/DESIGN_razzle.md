# razzle — the venue-specific presentation agent

## What razzle is

razzle is a new `ra*` agent that turns a finished paper into a **venue-specific slide deck** (`.pptx`).
Where raconteur is the storyteller (the paper), razzle is the podium: it takes raconteur's committed
narrative + the project's real figures and results, drapes them over a **branded master slide set**,
stamps the **authors/affiliations and funders (with their logos)**, and cuts a deck tailored to each
venue's format and time budget.

The governing principle is the same one that rules the rest of the pipeline: **razzle communicates
what the research established — it never invents it.** Every number on a slide traces to a minted
result; every figure is a real rendered figure; the narrative is raconteur's, not a fresh story.

## Position in the pipeline

razzle is a **new stage** (`deck`), downstream of `paper`, and — like `paper` — it **forks per
venue**. raconteur already analyses candidate venues and produces a per-venue manuscript; razzle
produces a per-venue **deck** off the same narrative.

```
… → experiments → paper (raconteur) ─┬─► deck: JASSS talk   (razzle)
                                     └─► deck: ISMIR poster (razzle)
```

`DEFAULT_STAGES` gains:

```python
"deck": {
    "dir": "slides", "tool": "razzle", "inputs": ["paper", "experiments"],
    "infix": "deck", "attended": False,   # opens per selected venue once its paper deliverable exists
},
```

- **Inputs**: `paper` (the narrative + claims — the spine) and `experiments` (the figures + real
  numbers). It reads the conceptual figures (the figure engine) and rayleigh's data figures from
  there.
- **Venue fork**: razzle reads raconteur's **venue slate** (the analysis that already records which
  venues were selected, and each one's kind/length/format) and produces one deck per venue, in
  `slides/{venue}/`, mirroring raconteur's per-venue folders.

## What razzle feeds on (five sources)

1. **raconteur's narrative + claims** — the paper stage's one-pager is the **talk's spine**; the
   manuscript supplies the claims and their support. razzle does not re-argue; it re-presents.
2. **Figures** — rayleigh's data figures (already rendered) + the conceptual figures from the figure
   engine. Slides are mostly figures + a sentence, so this is razzle's main visual payload.
3. **The master slide set** — a branded `.pptx` template with named layouts and logo placeholders
   (below). razzle fills it; it does not design slides from scratch.
4. **Authors + affiliations (+ logos)** — from the manifest's `authors` (name, affiliations) resolved
   against a shared **affiliation→logo registry**. Builds the title slide and the byline.
5. **Funders (+ logos)** — a per-project `funders` list resolved against a shared **funder→logo
   registry**. Builds the acknowledgements/funding slide and any title-slide funder strip.

## The three asset registries

Branding and logos are reused across projects and are often licensed art, so they live in **neutral,
shared, git-ignored** territory (the same PII-boundary pattern as the style profiles —
`~/.config/haarpi/razzle/`), with the project selecting which apply.

1. **Master slide set** — `~/.config/haarpi/razzle/masters/<name>.pptx` plus a small
   `<name>.layouts.yaml` descriptor that tells razzle the master's vocabulary: which master layout
   plays each **role** (title, section divider, content, figure-full, figure+caption, two-column,
   acknowledgements, closing) and the **named placeholders** for text, the picture area, and the
   **logo slots** (author-affiliation logos on the title; funder logos on the ack/title). razzle maps
   its slide spec onto these roles; the master owns the look.
2. **Affiliation → logo registry** — `~/.config/haarpi/razzle/affiliations.yaml`: affiliation name →
   `{logo: path, display_name?}`. Reused everywhere; a project just names affiliations on its authors.
3. **Funder → logo registry** — `~/.config/haarpi/razzle/funders.yaml`: funder name →
   `{logo: path, grant_format?}`. The project's `funders` list picks names + grant numbers.

An affiliation/funder with **no registered logo** degrades gracefully to text (name only) and razzle
warns — a missing logo never blocks the deck.

## Render backend — python-pptx (with a pandoc quick-draft)

The master slide set + logo placement is the deciding requirement: it needs **placeholder-level
control** (clone a named layout, fill its title/body/picture placeholders, drop logos into named
slots at fixed positions). Pandoc's `--reference-doc` only carries theme/fonts — it cannot place a
funder logo in the corner of the title slide. So:

- **Primary: `python-pptx`.** Clone the master's layouts by role, fill placeholders (text, figures,
  logos, speaker notes), emit `slides/{venue}/{cycle}_{short}_{venue}_deck.pptx`.
- **Quick-draft fallback: pandoc markdown → pptx** (`--reference-doc=master.pptx`) for a fast,
  logo-less content pass when you just want the structure. Optional; not the shipped path.

## The generation flow (spec → render → gate)

Same shape as every deliverable — the LLM authors a **structured deck spec**, a deterministic
renderer draws it, and a human gates it.

1. **gather** — collect the narrative (one-pager), the figures, the claims/numbers, the selected
   authors/affiliations/funders + resolved logos, the venue's constraints (kind, length → slide
   budget, aspect ratio), and the chosen master + its layout descriptor.
2. **author the deck spec** (LLM) — a per-slide structured spec grounded in the one-pager arc
   (motivation → question → approach → results → takeaway), each slide naming its **role** (→ master
   layout), title, bullets, the **figure** it shows, and **speaker notes**. Venue-aware: an 12-min
   ISMIR talk is ~12–15 slides; a poster is one board. Numbers come from the results, never invented.
3. **render** (python-pptx) — the spec → the branded `.pptx`, with author/affiliation/funder logos
   placed from the registries.
4. **gate** — the human reviews and finalises (see the open question below).

The **spec is the durable artifact** (editable, diffable, re-renderable); the `.pptx` is the output
collaborators tweak in PowerPoint.

## Open question — how a `.pptx` deliverable is gated

The redline gate is docx-centric, and a deck is a *communication* artifact the author will almost
always hand-polish in PowerPoint. So forcing a docx-style annotate→mint cycle on a `.pptx` is
probably the wrong fit. Two candidates (decision for you):

- **(A) Gate the spec, not the pptx.** The human annotates the structured deck spec (or a rendered
  PDF/outline of it); razzle revises the spec and re-renders. The `.pptx` is a build output, not the
  reviewed surface. Clean, matches the rest of the pipeline.
- **(B) Lightweight commit + hand-off.** razzle renders the draft `.pptx`; the human polishes it
  directly in PowerPoint and that *is* the finalisation — razzle's job ends at a good first draft.
  Optionally razzle can re-ingest the edited `.pptx` to update the spec for the next cycle.

Recommendation: **(A) for the review loop, (B) for the finish** — gate the spec so razzle's draft is
directed by real feedback, then let the human polish the rendered deck by hand. A deck is not a
preregistration; it doesn't need a strict mint.

## Manifest additions

- **`funders: [{name, grant}]`** — new project-level field (which grants funded THIS work); names
  resolve to logos via the shared funder registry.
- **`authors[].affiliations`** already exists — razzle resolves each affiliation name to a logo via
  the shared affiliation registry. No manifest change needed for authors beyond what's there.
- **`deck` stage config** in the manifest (dir/tool/inputs/infix), added by scaffold/migration.

## Decisions to confirm

1. **New `deck` stage, venue-forked** off `paper` (razzle owns it), vs. a raconteur sub-verb.
   Recommend: new stage — it's a distinct, forking deliverable with its own assets and gate.
2. **python-pptx as the render backend** (pandoc as an optional quick-draft). Recommend: yes — the
   master + logos need placeholder control pandoc can't give.
3. **Shared, neutral, git-ignored asset registries** (masters, affiliations→logos, funders→logos),
   with per-project selection. Recommend: yes — logos are reused and often licensed.
4. **Gate model** — (A) gate the spec + (B) hand-polish finish. Recommend: yes.

## Build order (each step ends green)

1. **Manifest + stage**: add the `deck` stage to `DEFAULT_STAGES`, the `funders` field, and
   scaffold `slides/`. Migration inserts `deck` into existing manifests. Stage-graph tests.
2. **Asset registries**: the three loaders (`masters/<name>.pptx` + `.layouts.yaml`,
   `affiliations.yaml`, `funders.yaml`) in neutral `~/.config/haarpi/razzle/`, with graceful
   text-fallback + warnings for missing logos. Ship one default master + layout descriptor.
3. **Render engine** (`razzle.render`, python-pptx): given a deck spec + master + resolved logos →
   `.pptx`. Deterministic; tested against a fixture master (title/section/content/figure/ack roles;
   logo placement). This is the load-bearing, LLM-free core — build and test it first.
4. **Spec authoring** (`razzle.compose`): the LLM turns the one-pager + figures + claims + venue
   constraints into the deck spec (per-slide role/title/bullets/figure/notes; venue-sized). Grounded;
   never invents a number. Fixture-tested with a fake brain.
5. **Venue fork + gather**: read raconteur's venue slate; per selected venue, gather inputs and run
   compose→render into `slides/{venue}/`. Wire the `deck` stage opening in `_advance`.
6. **Gate**: the spec-annotation review loop (A) + the hand-polish finish (B); `haarpi next`
   integration for the deck stage (its own `STAGE_STEPS`/`TIERS`/`PROMPTS`, like design/build).

Suites to stay green: a new `razzle` suite (render + compose + registries) plus haarpi's stage-graph
tests. The render engine (step 3) is the piece to prototype first — it proves the master/logo
mechanics before any LLM is involved.

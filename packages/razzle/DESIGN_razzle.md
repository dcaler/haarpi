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

## How a `.pptx` deliverable is gated — RESOLVED: comment ON the pptx

Decision (Cale): **comment on the `.pptx` itself**, reusing the redline procedure — not gating a spec
or a docx outline. A deck *is* its own markup. PowerPoint has no tracked changes, so **modern comments
carry the whole review**, and the redline rule maps cleanly:

- razzle drafts `slides/{fmt}/{date}_{short}_deck_ra.pptx` — a first-class revision-chain artifact.
- The author reviews it **in place** in PowerPoint (comments, resolving what's addressed). There is no
  rename to initials — the `.pptx` is the markup, and *the presence of a comment* is the "a human went
  last" signal (a draft nobody commented on is not finished markup).
- `haarpi next` reads the modern comments (`redline.pptx_comment_threads` / `gate_check`, pure
  zipfile+lxml, no python-pptx): **clean ⟺ every comment resolved.** Clean → mint by **promotion**
  (copy to the token-free `{date}_{short}_deck.pptx`; no redline resolution, no md sibling — gate model
  B for the finish). Any open comment → queue the `deck_session` rework tier (re-open `razzle deck`,
  address the comments, re-render).

So: **(A-by-comment) for the review loop, (B) for the finish** — the author's real comments direct the
next draft, and the minted deck is theirs to hand-polish. A deck is not a preregistration; the mint is
a promotion, not a contract.

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

1. **DONE (stage + CLI wiring)** — the `deck` stage is in `DEFAULT_STAGES` (razzle, `dir: slides`,
   inputs `[paper, experiments]`, attended, opens with `razzle deck`); the manifest gained a `funders`
   field; razzle is registered in haarpi's `TOOLS` (so `haarpi razzle …` dispatches) and `_OPENING`
   (minting the paper opens a `razzle deck session` — verified). The `razzle` CLI has `deck` (gather +
   launch an authoring session, or `--no-launch` to print the manual path) and `render`
   (`slides/<fmt>/spec.json` → the branded `.pptx`). Stage-graph + CLI tests.
   **Per-project format selection + migration (DONE):** the manifest gained `deck_formats` (the
   presentation formats this project builds a deck in — razzle's venue-analogue). The deck stage FORKS
   per format — `_open_deck` queues one `razzle deck --format <fmt>` session per entry; an empty list
   opens a single "pick format(s)" prompt (razzle owns the vocabulary, so haarpi passes names through
   verbatim and razzle validates on run, keeping the dependency one-directional). Migration is **by
   load**: `load_manifest` merges `DEFAULT_STAGES`, so a manifest written before the deck stage existed
   loads with it and an empty `deck_formats` — no rewrite needed. (`_OPENING` no longer keys `deck`.)
2. **Asset registries**: the three loaders (`masters/<name>.pptx` + `.layouts.yaml`,
   `affiliations.yaml`, `funders.yaml`) in neutral `~/.config/haarpi/razzle/`, with graceful
   text-fallback + warnings for missing logos. Ship one default master + layout descriptor.
3. **DONE (render engine + neutral assets)** — `razzle.render.render_deck(spec, master, descriptor,
   out, figures=, logos=)` (python-pptx) clones the master's layouts by role, fills text placeholders,
   and places figures/logos as pictures fitted into the placeholder boxes (python-pptx can't insert
   into an OBJECT placeholder, so `add_picture` at its geometry + drop the empty placeholder;
   `_clear_slides` drops slide RELS so the deck isn't corrupted). `razzle.assets` resolves the
   **neutral, never-in-repo** branding from `~/.config/haarpi/razzle/` — a house `master` (.pptx +
   `.yaml` descriptor: 16:9 title/figure/content roles → placeholders + logo slots) and the
   affiliation/funder → logo registries. Logos gracefully skipped when absent. The package ships only
   CODE + a generic `layouts/example.yaml` documenting the format — no real master/logos ever touch
   git (belt-and-suspenders: `*.pptx`, `/logos/` gitignored). New `razzle` package, editable-installed;
   render tested against the house master (skip-guarded on the neutral assets' presence).
4. **DONE (spec authoring)** — `razzle.compose(brain, narrative, figures, claims, venue=, max_slides=)`
   has the LLM turn the one-pager (the talk's spine) + the figure pool + the real claims into a deck
   spec as JSON, then parses/validates/**normalises** it: a valid role per slide, a leading title
   slide, figure refs demoted to plain slides if the id doesn't exist, `max_slides` respected; a bad
   parse degrades to a title-only deck (never a crash). Output feeds `render_deck` directly — proven
   end to end (compose → render → a 3-slide branded pptx). 4 fake-brain tests. (Which brain drives it
   — an ollama coordinator vs the interactive session — is settled at the stage opening, step 5.)
5. **DONE (gather + orchestrator)** — `razzle.gather` pulls the real inputs from a project: the
   narrative (raconteur's `load_onepager`, release > draft), the figure pool (by id + caption), the
   claims (each experiment's observed `finding` from `findings.json`, verbatim), and the author/funder
   logos (from the manifest + neutral registries). `razzle.deck.build_deck(root, fmt, brain)`
   orchestrates gather → compose (sized to the format) → render into `slides/{fmt}/`, writing the deck
   spec (`spec.json`, the durable artifact) and the `.pptx`; figures export to PNG on demand. Best-
   effort throughout. Tested with a fixture project + fake brain. **Remaining for step 5:** the
   per-project *selection* of which formats to build, a `razzle` CLI entry, and wiring the `deck`
   stage opening in `_advance` (with the stage-graph work in step 1).
6. **DONE (gate — comment on the pptx)** — the deck's review surface is the `.pptx` itself, gated by
   reusing the redline procedure on PowerPoint **modern comments**. `redline.pptx_comment_threads` +
   the `_gate_check_pptx` branch of `gate_check` read the comments (id/author/text/slide/resolved) with
   pure zipfile+lxml — clean ⟺ every comment resolved, `reviewer_changes` always 0 (no tracked changes
   in a .pptx). `find_finished_markup` scans `*.pptx` and surfaces a deck **iff it carries a comment**
   (commented-in-place; no rename to initials); `naming` accepts `pptx`; the clean branch mints by
   **promotion** (copy `_ra` → token-free release, no md sibling); the `deck` stage has its own
   `STAGE_STEPS`/`STAGE_TIERS`/`STAGE_PROMPTS` (one `deck_session` rework tier, like design/build), and
   `_archive_chain` sweeps the spent `_ra` pptx. **On mint, a PDF twin** of the released deck is
   written beside it (`_render_pptx_pdf` → LibreOffice headless, the only faithful `.pptx` renderer on
   a server) — best-effort, so a missing `soffice`/`libreoffice` never blocks the release (it prints
   "PDF skipped"). Fidelity depends on the deck's fonts being installed where the mint runs; a
   pixel-exact match means exporting from PowerPoint itself. Tested end to end (`test_deck_gate.py`:
   reader, clean/blocked gate, `find_finished_markup` surface/ignore-draft/ignore-release, and the PDF
   twin's skip/success paths).

   > **Dependency:** the PDF twin needs LibreOffice on the box that runs `haarpi next`
   > (`apt install libreoffice-impress`, or the full `libreoffice`). Absent it, the deck still mints —
   > only the `.pdf` is skipped.

## Render fixes from the first commented demo (DONE)

Cale's two open comments on the demo were real furniture feedback, now addressed in `render_deck`:

- **"only citations go here in this format"** — the caption slot is **citations-only**. The `figure`
  role's placeholder is `citation` (a bare source ref), not a prose `caption`; a figure slide's message
  is its **title**. `compose` emits `citation`, the descriptors map it, the prose caption is gone.
- **"legacy bits"** — `render_deck` was leaving the master's **unfilled placeholders** (empty
  caption/footer strips) on each slide. It now tracks the placeholders it fills and **strips the rest**
  (`_strip_unused`, keeping only the auto-numbering slide-number placeholder), so no leftover template
  furniture renders.

## Open items (to discuss)

- **The draft house master is not good enough — revisit it.** The current descriptor was reverse-
  engineered from a reference deck (design idea only), and its layouts are thin (title / figure /
  content). We need to design razzle's actual house master + its role set together — section dividers,
  two-column, an acknowledgements/funding slide, the logo strip treatment — before razzle ships decks
  anyone presents. The **running footer** (venue|short-title, page numbers) belongs here too: for now
  unfilled footer placeholders are stripped rather than populated.
- **Poster** is not a slide count — it needs its own shape (one board), a separate mode from the
  timed-talk formats.

Suites to stay green: a new `razzle` suite (render + compose + registries) plus haarpi's stage-graph
tests. The render engine (step 3) is the piece to prototype first — it proves the master/logo
mechanics before any LLM is involved.

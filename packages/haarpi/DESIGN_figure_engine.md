# haarpi.figure — the shared diagram-as-code figure engine

## The shift

Conceptual figures — flowcharts, the stage ladder, experiment DAGs, the analytical-framework
schematic, model/mechanism diagrams — are **ingredients of many deliverables**, not a deliverable of
their own. The same architecture diagram feeds the paper (raconteur), the deck (razzle), and the
methods digest (raster). So the figure capability is a **shared engine**, `haarpi.figure`, in the same
family as the redline and style engines: one engine, per-tool policy, every tool emits figures the
same way. It is emphatically **not** a new agent or stage — a figure is a component, and giving it
agent status would fragment ownership of one figure across its three consumers.

**Diffusion image models are the wrong tool here and are out of scope.** A flowchart must place exact
boxes, arrows, and *legible text labels*, and must stay correct and editable — none of which a raster
image model can do. The engine is **diagram-as-code**: a text source (Mermaid / Graphviz / TikZ /
matplotlib) rendered deterministically. This plays to HAARPi's strengths (it already runs on LLMs and
pandoc, no GPU) and its ethos (a figure derived from the real structure cannot misrepresent it).

## Two production modes

**1. Deterministic — from structured data, no LLM.** Some figures need no model at all; they are pure
functions of a structured artifact and are therefore always correct and always in sync:

- the **stage ladder** from `DEFAULT_STAGES` (`litreview → design → build → experiments → paper → deck`),
- the **experiment DAG** from `experiments.yaml`,
- the **module dependency graph** from raster's `tasks.yaml`.

These emit Mermaid/Graphviz straight from the file. This is the cheapest first win — a real figure out
of the pipeline with zero model involvement.

**2. Conceptual — LLM-authored, approved.** The framework schematic, a mechanism diagram, an
argument figure: the coordinator (or Claude, for the hard ones) writes the *diagram source* from a
prose request + context; the human sees the rendered figure and approves/edits the source. The model
writes **code**, never pixels.

## The figure pool — first-class revision-chain artifacts

Figures collect in a per-project **pool** (`figures/`) — but not as a bespoke store with a side index.
A figure is a **revision-chain artifact**, named and resolved by `haarpi.naming` exactly like every
other deliverable. The `<id>` is a single chain token (a deliverable word, like the paper stage's
`onepager`/`outline`):

- `{YYMMDD}_{proj}_{id}_ra.dot` — the **source** (the engine's draft): durable, editable, diffable,
- `{YYMMDD}_{proj}_{id}_ra.svg` — the **canonical render** (graphviz `dot -Tsvg`),
- `{YYMMDD}_{proj}_{id}_ra_DCR.svg` — **your Inkscape edit**, saved the same trailing-initials way as
  a docx annotation,
- `{YYMMDD}_{proj}_{id}.svg` — a **minted release** (token-free), what consumers bind,
- `{YYMMDD}_{proj}_{id}_ra.png` / `.pdf` — **derived exports**, rasterised from the authoritative SVG
  on demand and cached.

There is **no `index.yaml`** — the filesystem chain IS the metadata (HAARPi's no-side-database ethos).
Caption + provenance ride in the source header (`// caption:` / `// provenance:` comments in the
`.dot`). Resolution is `naming.find_latest_release` / `find_latest` over `figures/`, keyed by the `id`
token — the same resolver every stage's release uses. **Precedence: a minted release > your newest
hand-edit > the tool's `_ra` draft.**

The **clobber guard is free**: the engine only ever writes `_ra`, so it structurally cannot overwrite a
`_DCR` hand-edit or a release — no content hash needed, the naming convention *is* the guard. A
re-derive reuses the figure's datestamp (overwrites the `_ra`, no pile-up) and warns, if a hand-edit or
release exists, that your version stays authoritative. Figures thus inherit **staleness** (a
deterministic figure whose source data changed re-drafts a fresh `_ra`, surfacing the divergence) and
**archiving** (`old/`) for free. (This required broadening `haarpi.naming`'s extension whitelist to
recognise figure files — svg/dot/mmd/tex/png/pdf — so a figure parses on the chain.)

## Engine + policy (the boundary law)

Mirrors the redline/style engines: the **engine owns the invariant**; the **policy injects I/O**.

- **Engine** (`haarpi.figure`, pure): the deterministic emitters; `render(source, format) -> svg`
  (source text → canonical SVG via the right renderer); `export(id, kind=png|pdf, size)` (rasterise/
  convert the canonical SVG on demand, cached); the pool/index reader-writer; the renderer/rasteriser
  availability checks and graceful fallback.
- **`FigurePolicy`** (per tool, a Protocol): supplies the brain (for conceptual authoring), the pool
  location, the project context a deterministic emitter derives from, and logging. rayleigh, raconteur,
  raster, razzle each carry a thin policy; none re-implements rendering.

## Rendering: SVG-native, raster on export (deterministic, local, no GPU)

**SVG is the canonical render** — every source format targets SVG first. SVG is the vector source of
truth: editable (Inkscape/Illustrator), scalable to any size without regeneration, small, and
version-friendly. **PNG (and PDF) are derived exports**, rasterised/converted from the SVG on demand at
a target width or DPI — so the *same* figure serves a slide, a paper, and the web without ever being
re-generated. This is the whole point of SVG-native: make the figure once as vector, size it per
target as needed.

| Figure kind | Format | Source → SVG |
|---|---|---|
| Flowcharts, pipeline, DAGs | **Mermaid** | `mmdc -o fig.svg` |
| Dense / auto-laid-out graphs | **Graphviz** | `dot -Tsvg` |
| Publication conceptual figures | **TikZ** | `pdflatex` → `dvisvgm`/`pdf2svg` → svg |
| Programmatic / mixed data+concept | **matplotlib** | `savefig("fig.svg")` |

**Export from the canonical SVG:** `to_png(id, width|dpi)` via a rasteriser (`cairosvg` — pure-Python,
in-stack, no extra system binary — or `rsvg-convert`), and `to_pdf(id)` for a LaTeX manuscript that
wants vector. **PNG is the embed format for slides** (python-pptx embeds PNG reliably; its SVG support
is patchy) **and for docx** (pandoc); **PDF** is the higher-quality path for a LaTeX paper. Fonts must
be present at raster time (or the source converts text→path) for crisp labels.

v1 ships **Graphviz `dot` → SVG** plus **SVG → PNG (cairosvg)** export. Graphviz is chosen over Mermaid
for the machine-emitted DAGs on two grounds: it's a lightweight native binary (no headless Chromium —
`mermaid-cli` drags in puppeteer + a ~300 MB Chromium + a dozen X11 libs + ~100–300 MB RAM per render,
disproportionate on a headless server), and its SVG is **clean and Inkscape-editable** (semantic
`<g class="node">`, real `<text>` labels), whereas Mermaid's nested/`<foreignObject>` output is not.
Mermaid is deferred to the *conceptual* figures where its styling earns its weight and volume is low.
TikZ, matplotlib, and PDF export land later for paper-grade figures. Each renderer/rasteriser is
checked at call time; if absent, the engine **keeps what it has and warns** — a missing `dot` or
`cairosvg` never blocks a figure, exactly as a missing pandoc never blocks a docx.

## The figure spec

A conceptual request and a deterministic emitter both resolve to the same small spec the engine
renders:

```yaml
id: framework_schematic
kind: schematic            # dag | flowchart | schematic | graph | plot
format: mermaid            # mermaid | dot | tikz | matplotlib
caption: "The analytical framework: questions → approach → data infrastructure."
source: |                  # the diagram code (emitted deterministically, or authored by the LLM)
  flowchart LR
    Q[Research questions] --> A[Analytical approach] --> D[Data infrastructure]
provenance:                # the ethos: every figure says where it came from
  mode: conceptual         # conceptual | deterministic
  from: design/designdocs/EXPERIMENTS.md
  approved_by: DCR         # conceptual figures; deterministic ones record the source-file hash
born_stage: design
```

## Where figures are born vs. consumed

Figures are authored **where they are conceptually born**, via a shared `figure` capability on that
stage's session (not a separate agent to invoke), and consumed downstream by id:

| Figure | Born in | Mode | Consumed by |
|---|---|---|---|
| Stage ladder | any (from manifest) | deterministic | docs, onboarding |
| Experiment DAG | experiments (`experiments.yaml`) | deterministic | paper, deck, results write-up |
| Module graph | build (`tasks.yaml`) | deterministic | methods digest |
| Analytical-framework schematic | design (rayleigh init) | conceptual | paper, deck |
| Model / mechanism diagram | build/design | conceptual | paper, deck, methods |
| Data figures | experiments (rayleigh process) | (R/ggplot2, already) | paper, deck |

razzle and raconteur **draw from the pool**; they don't make figures. rayleigh's existing data figures
register into the same index so a deck can place them by id.

## Gating

Figures add **no new stage gate**. A conceptual figure is approved *in the session that authors it*
(the human sees the render, approves or edits the source), and is then re-reviewed wherever it is
embedded — the paper gate and the deck gate already scrutinise the deliverables that contain it.
Deterministic figures need no approval: they are derived from already-gated structured data, and
re-derive when that data changes.

## Consumers wire in by id

- **raconteur** embeds a figure by id — a **PNG** export for the docx render (pandoc), a **PDF** export
  for a LaTeX manuscript (vector) — both derived from the canonical SVG. A figure the argument needs
  but the pool lacks is a prompt to author one (conceptual), not to invent an image.
- **razzle** places a **PNG** export at slide resolution by id (python-pptx embeds PNG reliably). Its
  main visual payload.
- **raster** references the module graph / model diagram in the methods digest.
- **rayleigh** contributes data figures into the pool (rendered to SVG, exported as needed).

## Decisions to confirm

1. **Shared engine, not an agent/stage.** Recommend: yes — figures are components of gated
   deliverables, not deliverables themselves.
2. **Diagram-as-code only; diffusion out of scope** for figures. Recommend: yes.
3. **v1 = Mermaid + Graphviz**, TikZ + matplotlib later. Recommend: yes (covers flowcharts + all DAGs
   first; paper-grade formats when the paper needs them).
4. **Figures are revision-chain artifacts resolved through `haarpi.naming`** (no side index) — reusing
   release/hand-edit/draft resolution, the `_ra`-only clobber guard, staleness, and `old/` archiving.
   Resolved. (Superseded the earlier `index.yaml` idea.)
5. **SVG is the canonical render; PNG/PDF are derived exports** (PNG for slides + docx, PDF for LaTeX),
   rasterised on demand from the SVG. Resolved.
6. **Graphviz `dot` is the v1 renderer** (lightweight, Inkscape-editable SVG); Mermaid deferred to
   conceptual figures. Resolved.

## Build order (each step ends green)

1. **DONE** — Render core: `render(spec) -> svg` (graphviz `dot -Tsvg`), `export_png(svg, png, width)`
   (cairosvg), the **chain-named pool via `haarpi.naming`** (no index), `resolve` precedence
   (release > hand-edit > `_ra`), and the best-effort fallback. `haarpi.naming` extended to recognise
   figure extensions so a figure is a first-class chain artifact. The `_ra`-only write IS the clobber
   guard.
2. **DONE** — Deterministic emitters: `stage_dag(stages)`, `experiment_dag(experiments)`, and
   `module_graph(tasks.yaml)` → DOT, provenance in the source header. Renders the real `DEFAULT_STAGES`
   ladder to SVG + PNG. raster emits the module graph at `handoff` (`raster.figures`) — the third
   producer, beside rayleigh. Figure tests green; full haarpi suite 263.
3. **DONE** — Engine + `FigurePolicy` + conceptual `compose`: the LLM authors DOT source grounded in
   context; the output is EXTRACTED (fenced or bare) and VALIDATED (must compile), with one repair
   pass and a labelled compiling stub on total failure (never a crash). `run_compose(policy, …)`
   threads a tool's brain + grounding. Provenance marks it conceptual (a human still approves it where
   embedded). 4 fake-brain tests; full haarpi suite 261.
4. **DONE (rayleigh producer)** — `rayleigh.figures`: the DETERMINISTIC experiment DAG from
   `experiments.yaml` (`rayleigh plan`, post-session), and the CONCEPTUAL framework schematic authored
   by the Claude design session as `framework.dot` and rendered onto the chain (`rayleigh init`,
   post-session; `DESIGN_PROMPT` instructs the session to draw it). rayleigh has **no ollama brain**,
   so it uses the deterministic + render/naming paths, not `compose` — the strong in-session Claude is
   the conceptual author. 4 rayleigh tests; sweeps green.
   **DONE (raconteur consumer)** — `haarpi.figure` gained `list_ids`/`caption_of` (pool enumeration);
   raconteur's `load_pool_figures` surfaces the pool as a THIRD figure source (beside rayleigh's
   results figures and the author's `figures.yaml`) — author-origin, PNG-exported, captioned from the
   source header, wired into the outline's figure placement. Empty outside a project; raconteur 632, no
   regression. *Still to come:* register rayleigh's R data figures onto the pool; razzle as the deck
   consumer.
5. **Paper-grade formats**: TikZ (`pdflatex` → pdf → svg) and matplotlib renderers, for raconteur's
   publication figures.
6. **razzle** consumes the populated pool (its own build) — the figure engine is razzle's prerequisite.

Suites to stay green: a new `figure` suite (render core, deterministic emitters, compose) plus haarpi's
existing suites. Step 1 (render core) and step 2 (deterministic DAGs) are the demonstrable first slice —
a real figure out of the pipeline with no model in the loop.

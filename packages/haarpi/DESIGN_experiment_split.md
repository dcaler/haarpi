# Split rayleigh into a *design* stage and a *conduct* stage

## The shift

Today one `experiments` stage (rayleigh) sits **after** `build` and does two unlike things
in one place: it **designs** the experiments (`rayleigh init` — the preregistration) and it
**conducts** them (`conduct` → `process` → `review`). Because the stage depends on `build`,
the design is authored by reading the *finished code* — the exact preregistration
anti-pattern rayleigh's own playbook warns against (*"design each experiment before any data
exists — that is what makes a later result honest"*). Reading the code to write the design
lets the code shape the questions.

The fix splits rayleigh across **two stages of the ladder**, with `build` between them:

```
litreview ──► design ──► build ──► experiments ──► paper
(rabbitHole)  (rayleigh  (raster)  (rayleigh        (raconteur)
               init)                conduct/process/
                                    review)
```

- **design** commits the preregistration *before* any code exists.
- **build** is then constructed *to satisfy* that preregistration.
- **experiments** conducts the committed design against the built code and writes it up.

One tool, not two: rayleigh stays a single package. haarpi's stage graph models the two
halves as two stages that both dispatch to rayleigh — `design` with `init`, `experiments`
with `conduct`/`process`/`review`. The verbs already exist (`rayleigh <init|conduct|process|
review|queue>`); nothing in rayleigh's CLI surface has to move.

## The division of labour (decision 3)

**rayleigh specifies; raster devises.** The design stage writes the *contract the code must
honour* — the entrypoint signature, the parameters to sweep, the output artifacts — into
`experiments.yaml`. It does **not** read `code/` to discover that contract (there is no code
yet). raster's own `build` design session reads the committed contract and **devises a way to
get the code up to that point** — writing the entrypoint, or a thin shim, so that by the time
`conduct` runs, `code/` satisfies the spec.

So the `run_adapter:` block of `experiments.yaml` inverts meaning: today it *records what was
discovered* from code; now it *states what is required* of code. Everything downstream of it
(`conduct`, `process`) is unchanged — it still loads the same file and runs the same cells.

Re-implementation projects (e.g. pydsk re-implements the Wieners DSK model) still fit: a
*reference* codebase is **prior art that feeds design** (via litreview / the priors index),
not the `build` stage's output. Design may read the reference to choose sweep axes; it still
*specifies* the target contract that raster builds.

## Decision 1 — one tool, two stages

`DEFAULT_STAGES` gains a `design` stage and repositions `experiments`:

```python
"design": {
    "dir": "design", "tool": "rayleigh", "inputs": ["litreview"],
    "infix": "prereg", "attended": True,        # opens with `rayleigh init`
},
"build": {
    "dir": "code", "tool": "raster", "inputs": ["litreview", "design"],
    "infix": "methods", "attended": True,       # opens with `raster plan`
},
"experiments": {
    "dir": "results", "tool": "rayleigh", "inputs": ["build", "design"],
    "infix": "results", "attended": True,       # opens with `rayleigh queue` (a kickoff),
},                                              #   review is the deeper re-design gate
"paper": {
    "dir": "paper", "tool": "raconteur",
    "inputs": ["litreview", "build", "experiments"],
    "infix": "", "attended": False,
},
```

- `design` gets its **own directory** (`design/`, a peer of `litReview/`, `code/`, `results/`,
  `paper/`) rather than sharing `results/`. `rayleigh init` authors `design/designdocs/`
  (PLANNING, PRIORS, EXPERIMENTS, `experiments.yaml`) and mints the prereg docx into
  `design/output/`. This keeps the workspaces one-stage-per-directory — see "No shared
  directory" below.
- `experiments` keeps its meaning ("the runs + the write-up") and its `results` infix; it just
  moves *after* build and gains `design` as an input. It **reads** `experiments.yaml` from
  `design/` (its input), conducts it, and writes data + the results docx into `results/`.
- `experiments` is no longer opened by `rayleigh init` — that verb now belongs to `design`.
  Conduct is **dynamic** (one `conduct_exp` per experiment, fanned out from `experiments.yaml`
  by `rayleigh queue`), so it can't be a fixed template chain. `experiments` therefore opens as
  an **attended `rayleigh queue` kickoff** — a human decides to spend the compute (rayleigh's
  own rule #2: surface every compute decision), and `queue` then fans out the conduct→process
  run. `review` remains the deeper re-design gate once results exist (the `extend`/
  `review_session` tier). `attended` stays `True`.
- The `_advance` opening map is keyed by **stage**, not tool, because one tool now opens two
  stages with different verbs: `{"design": "init", "build": "plan", "experiments": "queue"}`.

## Decision 2 — design gates as a preregistration docx (option A)

The design stage's deliverable is `experiments.yaml`, which the docx-redline mint machinery
cannot gate directly. So `rayleigh init` also renders a **preregistration docx** — a
human-readable rendering of the committed design (the `EXPERIMENTS.md` content as a `.docx`):

```
260804_pydsk_prereg_ra.docx        ← rendered by `rayleigh init`, alongside experiments.yaml
260804_pydsk_prereg_ra_DCR.docx    ← the author annotates the preregistration
260804_pydsk_prereg.docx           ← MINTED: the preregistration is locked; build unlocks
```

Minting the prereg docx **is** locking the preregistration of record. `experiments.yaml` rides
alongside it as the machine artifact; the mint is the human's commitment that the design is
final *before* the code (and therefore any data) exists. This reuses the existing gate
verbatim — no new "release" concept, no non-docx release path. It costs one rendered docx.

**But the async annotation cycle is NOT forced here — the design is authored in an interactive
Claude+Cale session, so the review already happened live.** Unlike an unattended draft (which the
human must mark up in Word before it can mint), a design doc that the author is happy with is
**clean on arrival**: `rayleigh init` renders the prereg docx at session end, the author reads it,
and a single `haarpi next` mints it — no Word round-trip. The docx is kept only so the *option*
exists to leave margin comments and sleep on a commitment before locking it (worthwhile for a
prereg); dropping a comment re-opens the `design_session`, and simply re-running the session is the
other way to revise. So the gate collapses to a one-command commit for the interactive case; the
mint still earns its keep (it unlocks build, records provenance, and marks the deliberate prereg
commitment) without re-reviewing what was settled in conversation.

Post-conduct revisions are unaffected and stay honest: `rayleigh review` (in the **experiments**
stage, after `process`) still writes `experiments_2.yaml`, `_3`, … The diff of a later
`experiments_N.yaml` against the design-stage prereg is the durable record of every amendment
made *after* seeing results — the split makes "what we committed to" and "what we changed after
data" two different, individually-minted rungs instead of one blurred stage.

## No shared directory: `design/` is its own workspace

`design` and `experiments` get **separate directories** (`design/` and `results/`). Every stage
then owns exactly one directory, so the gate is untouched: `find_finished_markup`,
`_markup_dirs`, and `in_flight` map a markup file to a stage by directory as they do today, with
no ambiguity to resolve. (An earlier draft kept both in `results/` and infix-scoped the gate
scan to tell `prereg` from `results` markup — rejected: giving `design` its own directory is the
cleaner separation and needs *no* change to shared gate code.)

The one cost lands on rayleigh, not haarpi: **rayleigh now works in two directories, by verb.**
`init` operates in `design/`; `conduct`/`process`/`review` operate in `results/`. Today rayleigh
"works entirely inside results/" (per `init.py`), so this is a real change — the verb's working
directory is no longer fixed. haarpi already knows each stage's `dir`, so the queued opening task
runs the verb from the correct stage directory; a manual `haarpi rayleigh init` likewise resolves
`design/` from the manifest. `experiments.yaml` is authored in `design/` and **read** from there
by the experiments stage as its input; post-conduct amendments (`review` → `experiments_2.yaml`)
are written in `results/`, so "the committed preregistration" and "what changed after data" live
in different directories as well as different mints.

## rayleigh-side changes (the `init` verb only)

- **Stop requiring `code/`.** The priors index (`PRIORS.md`) and the run-adapter block must not
  assume a built `code/`. Point priors at litreview + any *reference* code as prior art; treat
  `run_adapter:` as a **specification** the author authors, not a discovery from code.
- **Render the prereg docx** at the end of `init` (the option-A deliverable), named by the
  `prereg` infix via the existing naming helpers.
- `conduct`, `process`, `review` are untouched — they run in the `experiments` stage exactly as
  today, just later on the ladder.

## raster-side expectation (interface, not owned here)

`build` gains `design` as an input and its `raster plan` session reads the committed
`experiments.yaml` `run_adapter:`/`axes:`/`outputs:` as the **build target**: the entrypoint
signature, the knobs to expose, the artifacts to emit. How raster gets the code there (native
entrypoint vs. shim) is raster's own design session's job. This doc only fixes the contract's
*shape* and *location*; raster's build session owns the *how*.

## Scope & blast radius

- **haarpi:** `DEFAULT_STAGES` (+1 stage, edges rewired); `_advance` opening-verb map keyed by
  stage; `scaffold` grows the `design/` directory. The gate (`find_finished_markup`/markup scan)
  and `latest_release`/`unlocked` need **no change** — one directory per stage, already
  infix-aware.
- **rayleigh:** `init` — drop the code-discovery assumption, render the prereg docx, and author
  into `design/`; `conduct`/`process`/`review` keep working in `results/`. rayleigh's working
  directory is now verb-dependent (was fixed at `results/`).
- **raster:** `build` consumes the prereg contract (interface expectation; raster's own change).
- **Existing projects:** a manifest written before this change has no `design` stage. Migration
  is additive — `seed`/`scaffold` inserts `design` and rewires `build`/`experiments` inputs;
  a project mid-flight (like pydsk, litreview not yet minted) simply gains the new rung ahead
  of build. No released artifact is invalidated.
- **The opening-verb interlock** (the separate guard discussion — refusing an opening verb when
  a stage's inputs aren't minted) is *related but out of scope here*; it applies cleanly to
  both new opening verbs once built.

## Decisions — RESOLVED

1. **One rayleigh tool, two haarpi stages** (`design`=init, `experiments`=conduct/process/
   review), each with its **own directory** (`design/` and `results/`) — no shared workspace,
   so the gate is untouched. ✔
2. **Design gates via a preregistration docx** through the existing mint; `experiments.yaml`
   rides alongside. ✔ (option A)
3. **rayleigh specifies the experiment design (the code contract); raster's build session
   devises how to satisfy it.** ✔

## Build order (each step ends green)

1. **DONE** — `DEFAULT_STAGES`: add `design` (`dir: design`, `infix: prereg`), rewire
   `build`←[litreview, design] and `experiments`←[build, design]; `experiments` stays attended
   (opens with `rayleigh queue`). `scaffold` creates `design/output/` automatically (it iterates
   the stages). Stage-graph tests added; `haarpi status` renders the new rung.
2. **DONE** — `_advance`: opening map keyed by stage (`design→init`, `build→plan`,
   `experiments→queue`). Design-stage gate registry added (`STAGE_STEPS`/`STAGE_TIERS`/
   `STAGE_PROMPTS`: one `revise` tier → attended `design_session` re-run). Title parsing
   disambiguates rayleigh's two stages by step. Tests: minting litreview queues `rayleigh design
   session`; a dirty prereg re-opens the design session; title round-trip.
3. **DONE** — rayleigh `init`: authors into `design/`, renders the `prereg` docx at session end,
   and reframed to the new domain (reads the minted litReview + brief, produces research questions
   + analytical approach, upstream of the code; the prereg mint hands off to raster). `DESIGN_PROMPT`
   + `PLANNING.md`/`EXPERIMENTS.md` templates rewritten; priors put litReview first, `code/` demoted
   to optional prior art. New rayleigh test suite (init authors under `design/` with no `code/`;
   new-domain playbook; `render_prereg` → gate-ready `…_prereg_ra.docx`).
4. **DONE (redefined)** — the experiments stage opens with a SECOND interactive session,
   **`rayleigh plan`**, not a mechanical kickoff. Where `init` designs the analytical *framework*
   upstream of the code, `plan` runs AFTER raster's build: it reads the committed prereg + the
   built `code/` and authors the EXECUTABLE `results/designdocs/experiments.yaml` (sweeps/cells,
   metrics, a `run_adapter` bound to the real entrypoint), then hands off to `conduct`/`process`.
   This is where "read finished code to design experiments" correctly lives (init no longer does).
   `init` trimmed to the framework (no executable `experiments.yaml`/`PROGRESS.md`); `_OPENING`
   for experiments → `rayleigh plan`. **No `design/` repoint needed** — `plan` writes into
   `results/`, exactly where `conduct`/`queue` already read. New `rayleigh plan` verb + templates
   (`results_PLANNING`/`results_EXPERIMENTS`/`results_gitignore`); tests for both stages.
5. Migration: `scaffold`/`seed_tool_configs` insert `design` into an existing manifest additively
   and create `design/`; pydsk-shaped fixture (litreview unminted) gains the rung without
   invalidating anything.
6. **DONE (minting)** — raster `build` now MINTS like the rest: `raster handoff` renders the
   methods digest (`…_methods_ra.md`) to a `…_methods_ra.docx` (pandoc + track-changes) so the
   docx-only gate can see it; a clean read mints in one `haarpi next` and unlocks experiments +
   paper. `build` wired into `STAGE_STEPS`/`STAGE_TIERS`/`STAGE_PROMPTS` (one `revise` tier → an
   attended `build_session` re-run). Tests: clean methods mints build + opens experiments; dirty
   methods re-opens the build session; `raster handoff` emits the gate-ready docx.
6b. **DONE** — `raster plan` now READS the committed prereg as its build target: `PLAN_PROMPT`
   and raster's `PLANNING.md` playbook point the session at `design/output/…_prereg` +
   `EXPERIMENTS.md` ("Data infrastructure required") + `experiments.yaml` first, and build the
   code to *satisfy* the analytical approach (the prereg outranks the brief; graceful fallback to
   the brief for standalone raster projects). Tests pin the prompt + playbook wiring.

Suites to stay green: haarpi, rayleigh (raster step lands with its own suite). The gate code is
untouched, so its suite is a pure regression check. Not yet built — this is the spec.

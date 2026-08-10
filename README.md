# HAARPi

**Human Authored Agentic Research Pipeline** — a monorepo bundling the `ra*`
research tools, which carry a research idea from literature review through
preregistration, model building, experiments, and manuscript to a
venue-specific presentation deck — with a human reviewing at every gate.
Scheduling runs on [trundlr](https://github.com/dcaler/trundlr).

**Offline-first is a defining goal, not a feature.** The pipeline's working
loops — gathering, synthesis, building, experiments, drafting, revision — run
on local models via Ollama, on your own hardware; a research project never
needs to leave the machine. Cloud models appear only as explicitly-optional,
human-invoked deviations (an A/B coordinator swap in rabbitHole; the
interactive design sessions in raster and rayleigh; the deck-authoring session
in razzle), never as shared plumbing and never on an automated path.

| Stage | Tool | Works in | Produces |
|---|---|---|---|
| literature review | [rabbitHole](packages/rabbithole) | `litReview/` | review + annotated bibliography |
| experiment design | [rayleigh](packages/rayleigh) | `design/` | the preregistered experiment design |
| model building | [raster](packages/raster) | `code/` | a built, tested code repo |
| experiments | [rayleigh](packages/rayleigh) | `results/` | preregistered findings + write-up |
| paper | [raconteur](packages/raconteur) | `paper/` | the manuscript, revision by revision |
| deck | [razzle](packages/razzle) | `slides/` | venue-specific presentation decks |

The experiment **design** (preregistration) is committed *before* any code is
built — you fix the experiments, then build to satisfy them, never the reverse
— so rayleigh owns two stages, one either side of raster's build.

The [haarpi](packages/haarpi) package is the shared core: the trundlr client,
the document revision naming chain, the redline gate, the figure engine,
notifications, run logging, and pandoc rendering — plus the umbrella CLI
(`haarpi init / next / status / queue / authors`) that ties the stages into one
pipeline. `haarpi next` reads the human's markup on a stage's deliverable and
either mints the release and advances the ladder, or classifies the comments
and queues the rework.

## Install

```bash
git clone https://github.com/dcaler/haarpi.git
cd haarpi
uv sync            # one venv, all six CLIs
```

Each tool remains individually installable (`pip install -e packages/<tool>`)
and individually usable — the monorepo shares machinery, not opinions.

## History

Four of the tools began as standalone repos (`dcaler/rabbithole`, `raconteur`,
`raster`, `rayleigh`), now archived and private; their full histories continue
here under `packages/`. razzle was born in the monorepo.

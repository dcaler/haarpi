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
| literature review | [rabbitHole](packages/rabbithole) | `litReview/` | an organized collection of facts + contribution map |
| experiment design | [rayleigh](packages/rayleigh) | `design/` | the preregistered experiment design |
| model building | [raster](packages/raster) | `code/` | a built, tested code repo |
| experiments | [rayleigh](packages/rayleigh) | `results/` | preregistered findings + write-up |
| paper | [raconteur](packages/raconteur) | `paper/` | the manuscript, revision by revision |
| deck | [razzle](packages/razzle) | `slides/` | venue-specific presentation decks |

The experiment **design** (preregistration) is committed *before* any code is
built — you fix the experiments, then build to satisfy them, never the reverse
— so rayleigh owns two stages, one either side of raster's build.

The **literature review is a coverage instrument, not a draft chapter.** It is
not what you would drop into a paper; it is an organized collection of facts,
some of which feed one later, and its job is to let a human decide whether the
corpus is good enough to start the work. So it is built to be *checked* rather
than read through. It opens with the **most load-bearing sources** — the top
5% of the corpus by how much of the review's argument each one carries, with a
sentence on what the project relies on it for. Below that sits an annotated
bibliography where every claim is page-located in its source, and beside it a
**contribution map** that bands the same sources at the 5% / 25% / 50% marks,
so the map's innermost ring and the opening list are one ranking seen two ways.
The prose carries the through line that organizes those facts — the same
structure the map draws radially. Reference targets are a diagnostic band,
never a cap; a review is expected to exceed them when the work asks for it.

Rework is scaled to the ask, not to the heaviest ask in the set. A comment that
can be answered by editing what is there gets a redline; one asking for a
strand the review lacks gets that section drafted and **spliced in**, leaving
every other paragraph byte-identical and every comment thread intact; only a
genuine change of direction re-plans the whole document. Reading 27 pages again
is a real cost, and nothing but a redirect is allowed to charge it.

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

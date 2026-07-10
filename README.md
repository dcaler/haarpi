# HAARPi

**Human Authored Agentic Research Pipeline** — a monorepo bundling the `ra*`
research tools, which take a research idea from literature review to submitted
paper with a human reviewing at every gate. Scheduling runs on
[trundlr](https://github.com/dcaler/trundlr).

**Offline-first is a defining goal, not a feature.** The pipeline's working
loops — gathering, synthesis, building, experiments, drafting, revision — run
on local models via Ollama, on your own hardware; a research project never
needs to leave the machine. Cloud models appear only as explicitly-optional,
human-invoked deviations (an A/B coordinator swap in rabbitHole, the
interactive design sessions in raster and rayleigh), never as shared plumbing
and never on an automated path.

| Stage | Tool | Works in | Produces |
|---|---|---|---|
| literature review | [rabbitHole](packages/rabbithole) | `litReview/` | review + annotated bibliography |
| model building | [raster](packages/raster) | `code/` | a built, tested code repo |
| experiments | [rayleigh](packages/rayleigh) | `results/` | preregistered findings + write-up |
| paper | [raconteur](packages/raconteur) | `paper/` | the manuscript, revision by revision |

The [haarpi](packages/haarpi) package is the shared core: the trundlr client,
the document revision naming chain, notifications, run logging, pandoc
rendering — plus the umbrella CLI (`haarpi init / status / queue / parseNplan`)
that ties the stages into one pipeline.

## Install

```bash
git clone https://github.com/dcaler/haarpi.git
cd haarpi
uv sync            # one venv, all five CLIs
```

Each tool remains individually installable (`pip install -e packages/<tool>`)
and individually usable — the monorepo shares machinery, not opinions.

## History

The four tools began as standalone repos (`dcaler/rabbithole`, `raconteur`,
`raster`, `rayleigh`), now archived; their full histories continue here under
`packages/`.

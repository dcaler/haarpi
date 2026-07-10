# HAARPi

**Human Authored Agentic Research Pipeline** — a monorepo bundling the `ra*`
research tools, which take a research idea from literature review to submitted
paper with a human reviewing at every gate. All heavy reasoning runs on local
LLMs; scheduling runs on [trundlr](https://github.com/dcaler/trundlr).

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

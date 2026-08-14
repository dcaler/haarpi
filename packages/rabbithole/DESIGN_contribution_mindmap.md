# rabbitHole `mindmap` — a contribution map minted from a literature review

## What it is

A new rabbitHole verb that reads the stage's **minted** literature-review release and emits a
**contribution map** into the review's own outputs (`litReview/output/`), rendered by
`haarpi.figure`'s renderers with the same chain-naming. It is a *review-comprehension aid, not a
paper figure*, so it does **not** enter the shared `figures/` pool (nothing here reaches a paper or
deck). The map is a themed mind-map: one
node per cited paper carrying a short *contribution phrase* + an *Author Year* label, grouped into
the review's themes, with arrows for **influence**, **temporal** ordering, or theme **evolution**.

Diagram-as-code, no diffusion: the brain writes a small **spec**, the engine renders it to SVG.

## Where correctness lives (why the freeze-review discipline applies)

Adding this verb is an agent alteration, so it follows the same path as any raster build: the one
**LLM step concentrates the risk**, so the deterministic scaffold is hardened and the contract is
frozen *before* a live model run. Everything but the brain call is deterministic and tested.

## The contract (frozen)

The brain returns JSON; the renderer consumes only this shape:

```
{ "papers": [ { "key": "<citekey>", "theme": "<one of the review's themes>",
                "phrase": "<=1 sentence contribution" } ],
  "edges":  [ { "src": "<citekey>", "dst": "<citekey>",
                "kind": "influence" | "temporal" | "evolution" } ] }
```

**Grounding law** (mirrors the redline contract). Every `key`, `src`, `dst` MUST be a citekey that
exists in `litReview/output/refs.bib`. `validate()` **drops** any paper or edge with an unknown key,
coerces `theme` to a real thread heading, and defaults an unknown `kind` to `influence`. The
`Author Year` label is taken from `refs.bib`, never from the model — the brain may summarise, never
invent a paper. A model reply with no usable JSON yields a labelled stub figure, never a crash
(as `haarpi.figure.compose` does).

## Pipeline

`minted review .md` + `refs.bib`
 → `parse_threads` (the `## ` thesis threads, minus the Narrative-Review wrapper and the
   Annotated-Bibliography tail; each with the `[@citekey]`s it cites)
 → `bib_keys` (citekey → `Author Year`, the grounding set)
 → **brain** builds the spec from the threads
 → `parse_spec` + `validate` (grounding)
 → `to_dot` (themes = clusters, papers = nodes, edges styled by kind)
 → `emit` into `litReview/output/` (chain-named `…_litmap_ra.{dot,svg,png}`, id `litmap`) — the same
   `_ra`-only clobber guard and Inkscape hand-edit workflow as the pool, but **not** the pool.

## Frozen tests (all GPU-free)

1. `parse_threads` — a sample review → the right themes and per-theme citekeys (bibliography and
   the Narrative-Review wrapper excluded).
2. `bib_keys` — a sample `refs.bib` → citekey → `Author Year` (single author, `et al.`, missing year).
3. `validate` — the grounding law: invented keys dropped from papers *and* edges; unknown theme
   coerced; unknown kind defaulted; never raises.
4. `to_dot` — a known spec → a valid `digraph` (one cluster per theme, a node per paper, an edge
   per kind) that compiles under `dot -Tsvg`.
5. `build_spec` against a **fake brain** returning a fixed reply → the composed `FigureSpec` is
   deterministic and grounded (the model is mocked, exactly as raster's frozen tests exercise the
   doer without a live model).

## Not in scope (yet)

- The live run on a real review (needs a free GPU) — done only after the suite is green.
- Per-paper prose beyond one phrase; figure placement into the paper (that's the harvest's job).

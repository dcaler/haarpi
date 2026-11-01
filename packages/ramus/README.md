# ramus

The preregistration half of the experiment workflow, and the stage before raster.

From the minted literature review and the project brief, `ramus init` co-designs the research
questions and the analytical approach, states what would confirm or disconfirm each, and
specifies the **data infrastructure** raster must then build. It writes `METHODS_SCOPE.md`,
the methodological families the approach must defend, and renders the framework as a
**`scope`** docx on exit. Minting the scope queues the methods review. Once that is released,
`ramus design` binds every method to its sources and renders the **`prereg`** docx on exit;
the haarpi gate mints it, and that mint is the handoff to raster.

Each session's docx is what you review: mark it up, or leave it clean if you shaped it in
the session. An annotated docx re-opens the session that rendered it. Neither session asks you
to review its markdown.

It authors the **framework only**. The executable `experiments.yaml` is written later by
`rayleigh plan`, against code raster has actually built — designing executables against
finished code is the preregistration anti-pattern, which is why this stage sits before the
build in the graph.

Named for Petrus Ramus, whose project was *method*: laying a subject out systematically before
reasoning from it, in diagrams that branch from the general to the particular. `framework.dot`
is exactly that — research questions → analytical approach → data infrastructure. The Latin
sense of the word, a branch, says the same thing from the other side.

This was `rayleigh init` until the split. Rayleigh's name belongs to the far side of raster:
his reputation rests on measuring nitrogen two ways, finding a 0.5% discrepancy, and refusing
to write it off — which is interpretation, and there is nothing here to interpret yet.

```
ramus init                 # open or continue a cycle, run the framework session (→ scope docx)
ramus init --new-cycle     # archive this cycle's designdocs/ and open a new datestamp
ramus init --no-launch     # scaffold and write the prompt without launching
ramus init --name N --brief B   # override the project name / starting brief
ramus design               # after the methods review: bind the methods (→ prereg docx)
```

Or through the umbrella: `haarpi ramus init`.

# ramus

The preregistration half of the experiment workflow, and the stage before raster.

From the minted literature review and the project brief, `ramus init` co-designs the research
questions and the analytical approach, states what would confirm or disconfirm each, and
specifies the **data infrastructure** raster must then build. It renders a `prereg` docx; the
haarpi gate mints it, and that mint is the handoff.

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
ramus init                 # open or continue a cycle, run the design session
ramus init --new-cycle     # archive this cycle's designdocs/ and open a new datestamp
ramus init --no-launch     # scaffold and write the prompt without launching
```

Or through the umbrella: `haarpi ramus init`.

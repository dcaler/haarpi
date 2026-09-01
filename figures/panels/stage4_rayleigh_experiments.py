#!/usr/bin/env python3
"""Stage 4 (rayleigh runs the experiments) — content only; emitter is wip_svg_panel."""


STAGE = "experiments"
COVERS = {"process": "l_proc", "review_session": "l_rev", "comment": "comm"}
OMITS = {}

ROWS = [
 ("hdr",     None,      None),
 ("plan",    None,      "a_exp"),     # ─┐ the revisions band: what a comment can remake.
 ("queue",   "l_proc",  None),        #  │ Where it STARTS is the whole question here —
 ("conduct", None,      "a_cells"),   #  │ cosmetic re-reduces, extend goes back for data
 ("process", "l_rev",   "a_find"),    #  │
 (None,      None,      "a_figs"),    #  │
 ("write",   None,      "a_doc"),     # ─┘
 ("comm",    None,      None),
 ("gate",    None,      None),
 ("rel",     None,      "a_mint"),
]
BAND = (1, 6)

SPINE = {
 "hdr": ("head", "4. rayleigh runs the experiments"),
 "plan": ("amber", "rayleigh plan: the SECOND live session. Against the minted framework and "
                   "the code raster actually built — its entrypoint, config surface, output "
                   "format — Human + Claude author the EXECUTABLE experiments: sweeps, cells, "
                   "metrics, and a run_adapter bound to the real code"),
 "queue": ("indigo", "rayleigh queue  →  linearizes the experiments into a trundlr chain, one "
                     "conduct node per experiment then a final process. The coarse chain rides "
                     "trundlr; each node still fans its cells out locally"),
 "conduct": ("indigo", "rayleigh conduct_exp <E>  →  expands the design into cells (one parameter "
                       "combo × one seed) and invokes the code's own entrypoint per cell. It "
                       "reimplements none of the model, skips cells whose output exists, and "
                       "stamps provenance"),
 "process": ("indigo", "rayleigh process_outputs  →  reduces the cells into a tidy table and "
                       "writes a base R + ggplot2 script that produces every figure, table, "
                       "statistic and regression. rayleigh does not compute the analysis: R does, "
                       "and the script stays runnable"),
 "write": ("indigo", "assembles the report around what R produced — the datestamped write-up "
                     "the gate reads"),
 "comm": ("amber", "comment: the Human redlines the results write-up — accept and resolve are "
                   "human-only"),
 "gate": ("purple", "haarpi next  →  reads the markup and sorts it by WHAT IT WOULD TAKE: "
                    "presentation only, or new data"),
 "rel": ("mint", "experiments release: the findings, as preregistered"),
}
LANE = {   # the only stage whose rework splits two ways, on whether new DATA is needed
 "l_proc": "cosmetic  →  `rayleigh process` re-reduces and re-writes from the cells already "
           "on disk. NO new data, so nothing is re-run: the analysis script and the tidy table "
           "are durable, and the report is assembled again around them",
 "l_rev":  "extend  →  `rayleigh review`, an attended Human + Claude session, because the "
           "comment needs cells, seeds or experiments that do not exist yet. The session decides "
           "which layer diverged and QUEUES its own follow-on chain. rayleigh reports; it never "
           "concludes",
}
ARTS = {
 "a_exp":   ["results/designdocs/", "experiments.yaml"],
 "a_cells": ["the cell outputs", "(restartable, provenance-stamped)"],
 "a_find":  ["findings.json", "+ the analysis script"],
 "a_figs":  ["the data figures", "(into the project figure pool)"],
 "a_doc":   ["the results write-up"],
 "a_mint":  ["the minted findings"],
}
MAKES = [("plan","a_exp"), ("conduct","a_cells"), ("process","a_find"),
         ("process","a_figs"), ("write","a_doc"), ("rel","a_mint")]

OPTS = {"gate_label": "findings accepted"}

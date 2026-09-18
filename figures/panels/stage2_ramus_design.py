#!/usr/bin/env python3
"""Stage 2 (ramus designs the study) — content only; the emitter is wip_svg_panel.

THREE RUNGS IN ONE STAGE. The methods review lives inside this stage rather than beside it:
the ramus conversation is what works out which methodological families the approach needs, so
the search cannot precede it, and the prereg cannot precede the search.
"""


STAGE = "design"
COVERS = {"design_session": "session", "design_bind": "bind",
          "methods_gather": "mgather", "methods_collect": "mcollect",
          "methods_build": "mbuild", "methods_report": "mreport"}
OMITS = {}

ROWS = [
 ("hdr",      None,       None),
 ("session",  None,       "a_plan"),
 ("scope",    None,       "a_scope"),
 ("mgather",  "mcollect", "a_mlist"),   # ─┐ the methods review: rabbitHole's verbs, inside
 ("mreport",  "mbuild",   "a_mrev"),    # ─┘ this stage, against the scope above
 ("bind",     "resess",   "a_exp"),
 ("prereg",   None,       "a_doc"),
 ("comm",     None,       None),
 ("gate",     None,       None),
 ("rel",      None,       "a_mint"),
]
BAND = (1, 6)

SPINE = {
 "hdr": ("head", "2. ramus designs the study — and calls for the methods it needs"),
 "session": ("amber", "ramus init: from the MINTED literature review and the brief, Human + "
                      "Claude co-design the research questions and the analytical approach in "
                      "a LIVE session. It SPECIFIES only: building the code is raster's job"),
 "scope": ("mint", "rung 1 — scope: the framework, plus METHODS_SCOPE.md naming the "
                   "methodological families this approach must be able to defend. A session "
                   "that settles an approach and names nothing to read for it has not "
                   "finished, so the gate refuses to mint without it"),
 "mgather": ("indigo", "rabbitHole gather methods  →  searches those families — anchored on "
                       "them, with this project's own subject matter EXCLUDED. Seeded from the "
                       "scope brief, so nobody hand-writes the config"),
 "mreport": ("indigo", "rabbitHole report methods  →  synthesises ~20-25 sources: how each "
                       "choice is computed and defended, and its standing critiques"),
 "bind": ("amber", "ramus design: the SECOND live session — binds the methods now their "
                   "literature is in hand, and plans the study. No [PROVISIONAL] markers and "
                   "no methods addendum: the sources are here"),
 "prereg": ("indigo", "renders the preregistration for review: "
                      "{cycle}_{short}_prereg_ra.docx, track-changes on"),
 "comm": ("amber", "comment: the Human redlines each rung (accept and resolve are human-only)"),
 "gate": ("purple", "haarpi next  →  reads the markup. A clean SCOPE queues the methods "
                    "search; a clean METHODS REVIEW queues the binding session; a clean "
                    "PREREG mints the design and unlocks raster"),
 "rel": ("mint", "design release: the preregistration, committed BEFORE any code is built"),
}
LANE = {
 "mcollect": "rabbitHole collect methods: the Human adds the real sources to Zotero with their "
             "PDFs — coded into the SAME corpus ledger with purpose `methods`, in the SAME "
             "collection, so refs.bib is never split",
 "mbuild": "rabbitHole build methods  →  embeds only the ledger rows whose purpose is "
           "`methods`, into this review's own vector store",
 "resess": "any unresolved comment re-opens the session that authored that rung — a decision "
           "cannot be redlined, only remade, which is why ANY severity costs the whole session",
}
ARTS = {
 "a_plan":   ["designdocs/PLANNING.md", "designdocs/PRIORS.md"],
 "a_scope":  ["designdocs/METHODS_SCOPE.md", "(the families to defend)"],
 "a_mlist":  ["the methods collect-list"],
 "a_mrev":   ["the methods review", "(a rung release)"],
 "a_exp":    ["designdocs/EXPERIMENTS.md", "the framework schematic"],
 "a_doc":    ["the prereg .docx"],
 "a_mint":   ["the minted preregistration"],
}
MAKES = [("session","a_plan"), ("scope","a_scope"), ("mgather","a_mlist"),
         ("mreport","a_mrev"), ("bind","a_exp"), ("prereg","a_doc"), ("rel","a_mint")]

OPTS = {"gate_label": "design agreed"}

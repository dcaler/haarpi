#!/usr/bin/env python3
"""Stage 2 (rayleigh designs the study) — content only; the emitter is wip_svg_panel."""


STAGE = "design"
COVERS = {"design_session": "resess"}
OMITS = {}

ROWS = [
 ("hdr",     None,     None),
 ("session", None,     "a_plan"),    # ─┐ the revisions band: ANY unresolved comment re-opens
 (None,      "resess", "a_exp"),     #  │ the session, so everything the session authors is
 (None,      None,     "a_priors"),  #  │ what a revision remakes
 (None,      None,     "a_fig"),     #  │
 ("prereg",  None,     "a_doc"),     # ─┘
 ("comm",    None,     None),
 ("gate",    None,     None),
 ("rel",     None,     "a_mint"),
]
BAND = (1, 5)

SPINE = {
 "hdr": ("head", "2. rayleigh designs the study"),
 "session": ("amber", "rayleigh init: from the MINTED literature review and the brief, Human + Claude co-design the research questions and the analytical approach in a LIVE session — the strong-reasoning step, too open-ended to default. It SPECIFIES only: building the code is raster's job, downstream"),
 "prereg": ("indigo", "renders the preregistration for review: {cycle}_{short}_prereg_ra.docx, track-changes on"),
 "comm": ("amber", "comment: the Human redlines the preregistration (accept and resolve are human-only)"),
 "gate": ("purple", "haarpi next  →  reads the markup. Clean MINTS the design and unlocks raster; any unresolved comment re-opens the session"),
 "rel": ("mint", "design release: the preregistration, committed BEFORE any code is built"),
}
LANE = {   # one tool only — a design is a set of DECISIONS, not prose
 "resess": "design_session  →  re-open the live session to address the annotations and re-render the prereg. There is no in-place reviser here: a decision cannot be redlined, only remade — which is why ANY severity costs the whole session",
}
ARTS = {
 "a_plan":   ["designdocs/PLANNING.md"],
 "a_exp":    ["designdocs/EXPERIMENTS.md"],
 "a_priors": ["designdocs/PRIORS.md", "(prior ra* artifacts, indexed)"],
 "a_fig":    ["the framework schematic", "(into the project figure pool)"],
 "a_doc":    ["the prereg .docx"],
 "a_mint":   ["the minted preregistration"],
}
MAKES = [("session","a_plan"), ("session","a_exp"), ("session","a_priors"),
         ("session","a_fig"), ("prereg","a_doc"), ("rel","a_mint")]

OPTS = {"gate_label": "design agreed"}

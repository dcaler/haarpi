#!/usr/bin/env python3
"""Stage 1b (rabbitHole surveys the METHODS literature) — content only; emitter is
wip_svg_panel.

The same tool and the same verbs as stage 1, against a different question. It earns its own
panel because it is a separate STAGE with its own release, and because what differs is the
part a reader has to understand: the anchor is inverted onto the methodological families and
the project's own subject matter is EXCLUDED. A five-round steering effort failed to find
sequence-analysis methodology precisely by getting that backwards.
"""


# ── what this panel claims to depict ──────────────────────────────────────────
# Checked against planner.STAGE_STEPS / STAGE_TIERS by test_figure_drift.py.
STAGE = "methodsreview"
COVERS = {
 "gather": "gather", "collect": "collect", "report": "report",
 "mindmap": "mm", "comment": "comm",
 "ingest": "ingest", "audit": "audit", "build": "build", "revise": "revise",
}
OMITS = {
 "graft": "vestigial: only cli.py reaches graft.run(); see the stage-1 panel",
}

ROWS = [
 ("hdr",     None,      None),
 ("scope",   None,      "a_scope"),
 ("gather",  "ingest",  "a_list"),
 ("collect", "audit",   None),
 ("report",  "build",   "a_docx"),
 (None,      "revise",  "a_refs"),
 ("comm",    None,      None),
 ("gate",    None,      None),
 ("rel",     None,      None),
]
BAND = (2, 5)

SPINE = {
 "hdr": ("head", "1b. rabbitHole surveys the METHODS literature"),
 "scope": ("amber", "ramus init writes METHODS_SCOPE.md: the methodological FAMILIES the "
                    "analytical approach needs to defend — which opens this stage and seeds "
                    "its config, anchored on those families with the project's own subject "
                    "matter EXCLUDED"),
 "gather": ("indigo", "gather methods  →  the same verb, a different question: sequence "
                      "distances and cluster validity, not the domain the substantive review "
                      "already covered"),
 "collect": ("amber", "collect methods: the Human adds the real sources — coded into the SAME "
                      "corpus ledger with purpose `methods`, in the SAME Zotero collection"),
 "report": ("indigo", "report methods  →  synthesises the methods review, ~20-25 sources: how "
                      "each choice is computed, defended, and what its standing critiques are"),
 "mm": ("indigo", "mindmap  →  the contribution map, as for any review"),
 "comm": ("amber", "comment: the Human redlines it — accept and resolve are human-only"),
 "gate": ("purple", "haarpi next  →  mints the methodsreview release, which is what UNBLOCKS "
                    "the preregistration: ramus will not mint a prereg whose methods rest on "
                    "literature nobody has read"),
 "rel": ("mint", "methodsreview release  →  raconteur's Methods section, and the prereg gate"),
}
LANE = {
 "ingest": "ingest  →  fetches the references a reviewer NAMED for this review",
 "audit":  "audit  →  one shared collection, two reviews: the ledger says which review each "
           "source is FOR (purpose), and audit says whether it is in the corpus at all "
           "(status) — a homograph check, never a relevance one",
 "build":  "build  →  embeds only the rows whose purpose is `methods`, into this review's own "
           "vector store",
 "revise": "revise  →  answers the markup in kind, as for any review",
}
ARTS = {
 "a_scope": "METHODS_SCOPE.md", "a_list": "the collect-list",
 "a_docx": "the methods review .docx", "a_refs": "refs.bib — shared, never split",
}
MAKES = [("scope","a_scope"), ("gather","a_list"), ("report","a_docx"), ("build","a_refs")]
OPTS = {}

#!/usr/bin/env python3
"""Stage 1 (rabbitHole surveys the literature) — content only; emitter is wip_svg_panel."""


# ── what this panel claims to depict ──────────────────────────────────────────
# Checked against planner.STAGE_STEPS / STAGE_TIERS by test_figure_drift.py: add a verb
# to the registry and the suite fails until it is either drawn here or excused below.
STAGE = "litreview"
COVERS = {          # registry step -> the panel key(s) that depict it
 "gather": "gather", "collect": "collect", "report": "report",
 "mindmap": "mm", "comment": "comm",
 "ingest": "ingest", "audit": "audit", "build": "build", "revise": "revise",
}
OMITS = {           # deliberately not drawn, and why
 "graft": "vestigial: only cli.py reaches graft.run(); its drafting functions live "
          "inside revise (revise.py imports draft_sections/choose_position), and "
          "`haarpi next` never selects it",
}

ROWS = [
 ("hdr",     None,      None),
 ("brief",   None,      "a_cfg"),
 ("gather",  "ingest",  "a_list"),   # ─┐ the revisions band: everything a revision can
 ("collect", "audit",   None),       #  │ remake, from a new collect-list down to a new
 ("report",  "build",   "a_docx"),   #  │ contribution map
 (None,      "revise",  "a_refs"),   #  │
 (None,      "correct", "a_corp"),   #  │
 ("mm",      None,      "a_map"),    # ─┘
 ("comm",    None,      None),
 ("gate",    None,      None),
 ("rel",     None,      "a_mint"),
]
BAND = (2, 7)

SPINE = {
 "hdr":  ("head", "1. rabbitHole surveys the literature"),
 "brief": ("amber", "haarpi init: the Human sets the brief — the question, how many "
                    "sources, the Zotero collection"),
 "gather": ("indigo", "gather  →  searches, ranks and curates candidate sources, and writes "
                      "the collect-list for the Human to verify"),
 "collect": ("amber", "collect: the Human adds each real source to Zotero WITH its PDF — "
                      "verifying it exists, which is what guards against hallucinated citations"),
 "report": ("indigo", "report  →  plans the review's sections and synthesises the FIRST draft "
                      "from the corpus, embedding it as it goes. Later, only a redirect re-plans it"),
 "mm": ("indigo", "mindmap  →  places every cited source by how much of the review's argument "
                  "it carries, banded at the 5% / 25% / 50% marks of the corpus"),
 "comm": ("amber", "comment: the Human redlines the review — accept and resolve are human-only"),
 "gate": ("purple", "haarpi next  →  decomposes the markup ONE COMMENT AT A TIME into what "
                    "each asks for, then BUILDS the chain those needs require"),
 "rel": ("mint", "litreview release"),
}
LANE = {
 "ingest": "ingest  →  fetches the references a reviewer NAMED, matching what it can against "
           "Zotero and listing the rest for `collect`",
 "audit":  "audit  →  re-judges the corpus for CONCEPTUAL TRANSFER and moves the false-friends "
           "(shared word, different sense) into a shared `quarantine` collection — a move, "
           "never a delete",
 "build":  "build  →  reads the audited Zotero collection into the working corpus: candidates, "
           "citekeys, the ChromaDB index, per-paper notes. `revise` reads that cache and never embeds",
 "revise": "revise  →  answers EVERY comment in kind: a tracked rewrite where prose can carry "
           "it, a drafted section spliced in at the comment that asked for it, the cycle's "
           "corrections applied across the document",
 "correct": "correct  →  replaces a term the reviewer says is wrong across the brief, the litrev "
            "config, the draft .md and the .docx. Deterministic, and applied BEFORE the chain is "
            "built — so it queues no step at all",
}
ARTS = {
 "a_cfg": "litrev.yaml", "a_list": "the collect-list",
 "a_docx": "the review .docx", "a_refs": "refs.bib",
 "a_corp": "the embedded corpus", "a_map": "the contribution map",
 "a_mint": "the released review",
}
MAKES = [("brief","a_cfg"), ("gather","a_list"), ("report","a_docx"),
         ("report","a_refs"), ("report","a_corp"), ("mm","a_map"), ("rel","a_mint")]

OPTS = {}

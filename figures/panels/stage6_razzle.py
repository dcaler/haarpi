#!/usr/bin/env python3
"""Stage 6 (razzle makes the presentation) — content only; emitter is wip_svg_panel."""


STAGE = "deck"
COVERS = {"deck_session": "l_deck"}
OMITS = {}

ROWS = [
 ("hdr",     None,       None),
 ("config",  None,       "a_cfg"),
 ("deck",    "l_deck",   "a_spec"),   # ─┐ the revisions band: the spec and the render.
 ("render",  "l_render", "a_pptx"),   # ─┘ The interview's facts sit OUTSIDE it
 ("comm",    None,       None),
 ("gate",    None,       None),
 ("rel",     None,       "a_mint"),
]
BAND = (2, 3)

SPINE = {
 "hdr": ("head", "6. razzle makes the presentation"),
 "config": ("amber", "razzle interview: a pure-python session — NO model — that captures the "
                     "facts a tool must never invent: which formats to build, and per format the "
                     "venue, date, presenting authors, affiliation logos and funders. It writes "
                     "the manifest and prints the per-format command. It queues nothing, because "
                     "the deck stage is tracked by its DELIVERABLE"),
 "deck": ("amber", "razzle deck --format <fmt>: gathers the manuscript, one-pager, figures and "
                   "claims, then hands over to an INTERACTIVE session that authors the deck spec "
                   "— the only model call in the stage. The presenting byline is applied "
                   "deterministically afterwards, because who presents is a fact, not a guess"),
 "render": ("indigo", "python-pptx clones the master's layouts by ROLE and fills them: no model, "
                      "no GPU. A missing master degrades to writing the spec rather than failing. "
                      "Masters and logos live outside the repo, in ~/.config/haarpi/razzle/"),
 "comm": ("amber", "comment: the Human redlines the .pptx IN PLACE with PowerPoint comments — "
                   "accept and resolve are human-only"),
 "gate": ("purple", "haarpi next  →  mints the deck to its token-free name; any unresolved "
                    "comment re-opens the deck session"),
 "rel": ("mint", "deck release — ONE PER CHOSEN FORMAT (short talk, long talk, poster)"),
}
LANE = {
 "l_deck": "deck_session  →  re-open `razzle deck` to address the PowerPoint comments, revise "
           "the spec and re-render. Any severity, like every other attended stage: the spec is "
           "a set of authoring decisions, and a decision is remade rather than redlined",
 "l_render": "razzle render  →  when the spec is right and only the rendering is wrong, re-render "
             "from spec.json. No model, no GPU, no cost — which is why the spec, not the .pptx, "
             "is the durable artifact",
}
ARTS = {
 "a_cfg":  ["deck_formats + decks", "(written to the manifest)"],
 "a_spec": ["slides/{fmt}/spec.json", "(the durable artifact)"],
 "a_pptx": ["the branded .pptx"],
 "a_mint": ["the minted deck,", "one per format"],
}
MAKES = [("config","a_cfg"), ("deck","a_spec"), ("render","a_pptx"), ("rel","a_mint")]

OPTS = {"gate_label": "deck accepted"}

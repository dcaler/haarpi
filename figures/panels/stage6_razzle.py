#!/usr/bin/env python3
"""Stage 6 (razzle makes the presentation) — content only; emitter is wip_svg_panel."""


STAGE = "deck"
COVERS = {"author": "deck", "comment": "comm", "deck_session": "l_deck",
          "illustrate": "illus", "place": "l_render"}
OMITS = {}

ROWS = [
 ("hdr",     None,       None),
 ("config",  None,       "a_cfg"),
 ("deck",    "l_deck",   "a_spec"),   # ─┐ the revisions band: the spec, the render and the
 ("render",  "l_render", "a_pptx"),   #  │ pictures. The interview's facts sit OUTSIDE it
 ("illus",   None,       "a_ill"),    # ─┘
 ("comm",    None,       None),
 ("gate",    None,       None),
 ("rel",     None,       "a_mint"),
]
BAND = (2, 4)

SPINE = {
 "hdr": ("head", "6. razzle makes the presentation"),
 "config": ("amber", "razzle interview: a pure-python session — NO model — that captures the "
                     "facts a tool must never invent: which formats to build, and per format the "
                     "venue, date, presenting authors, affiliation logos and funders. WHICH "
                     "deliverables exist is itself this choice, so the stage cannot queue its own "
                     "work: a `haarpi next` behind the interview reads the config and queues one "
                     "chain per format"),
 "deck": ("indigo", "razzle deck --format <fmt>: gathers the manuscript, one-pager, figures and "
                    "claims, composes the spec on the pipeline's own coordinator, and renders it "
                    "— one process, no session to sit at. Every author is credited and one "
                    "contact address is applied afterwards, because who presents is a fact"),
 "render": ("indigo", "python-pptx clones the master's layouts by ROLE and fills them: no model. "
                      "The spec is NORMALISED on the way in, so the slide budgets hold whoever "
                      "authored it. Masters and logos live outside the repo, in "
                      "~/.config/haarpi/razzle/"),
 "illus": ("indigo", "razzle illustrate  →  draws candidates for each PICTORIAL illustration "
                     "brief with `imagine` (local Stable Diffusion), on the GPU lane. A "
                     "schematic brief (diagram, chart, worked example) is never drawn: it stays a "
                     "TODO in the notes. Nothing drawn reaches a slide unchosen"),
 "comm": ("amber", "comment: the Human redlines the .pptx IN PLACE with PowerPoint comments — "
                   "accept and resolve are human-only — and picks one candidate per slide. "
                   "razzle's own comment holds the deck open until the Human resolves it. The "
                   "stage's ONLY other human step"),
 "gate": ("purple", "haarpi next  →  mints the deck to its token-free name; any unresolved "
                    "comment of the Human's re-opens the deck session; picks not yet on the deck "
                    "re-render it"),
 "rel": ("mint", "deck release — ONE PER CHOSEN FORMAT (short talk, long talk, poster)"),
}
LANE = {
 "l_deck": "deck_session  →  re-run `razzle deck` to address the PowerPoint comments, re-compose "
           "the spec and re-render. Any severity: the spec is a set of authoring decisions, and a "
           "decision is remade rather than redlined",
 "l_render": "razzle render (place)  →  re-render from spec.json with the Human's picked "
             "illustrations in place, beside their bullets. No model, no GPU, no cost — which is "
             "why the spec, not the .pptx, is the durable artifact",
}
ARTS = {
 "a_cfg":  ["deck_formats + decks", "(written to the manifest)"],
 "a_spec": ["slides/{venue}/spec.json", "(the durable artifact)"],
 "a_pptx": ["the branded .pptx"],
 "a_ill":  ["illustrations/<slide>/", "cand_*.png → chosen.png"],
 "a_mint": ["the minted deck,", "one per format"],
}
MAKES = [("config","a_cfg"), ("deck","a_spec"), ("render","a_pptx"), ("illus","a_ill"),
         ("rel","a_mint")]

OPTS = {"gate_label": "deck accepted"}

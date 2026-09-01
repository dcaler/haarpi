#!/usr/bin/env python3
"""Stage 5 (raconteur writes the paper) — content only; emitter is wip_svg_panel."""
from _emitter import GREEN  # noqa: F401  (used by OPTS)

STAGE = "paper"
COVERS = {
 "onepager": "onepager", "venue": "venue", "skeleton": "skeleton",
 "outline": "outline", "draft": "draft",
 "comment": ("comm1", "comm2", "comm3"),
 "revise": "l_cosmetic", "recut": "l_recut",
}
OMITS = {}

ROWS = [
 ("hdr",     None,         None),
 ("onepager", None,        "a_op"),     # ─┐ the revisions band. A comment at ANY rung can
 ("comm1",   "l_recut",    None),       #  │ send the work back up the ladder — a narrative
 ("gate1",   None,         None),       #  │ objection at the manuscript re-cuts the
 ("rel1",    None,         "a_mint_op"),       #  │ one-pager, four rungs above it
 ("venue",   None,         "a_venue"),  #  │
 ("pick",    None,         None),       #  │
 ("skeleton", None,        "a_skel"),   #  │
 ("outline", "l_cosmetic", "a_out"),    #  │
 ("comm2",   None,         None),       #  │
 ("gate2",   None,         None),       #  │
 ("rel2",    None,         "a_mint_out"),       #  │
 ("draft",   "l_upstream", "a_ms"),     #  │
 ("comm3",   None,         None),       #  │
 ("gate3",   None,         None),       # ─┘
 ("rel3",    None,         "a_mint"),
 ("package", None,         "a_sub"),
]
BAND = [(1, 2), (5, 9), (12, 13)]   # one band per gate cycle

_G = "purple"
SPINE = {
 "hdr": ("head", "5. raconteur writes the paper"),
 "onepager": ("indigo", "raconteur onepager  →  the narrative through-line, in one page. "
                        "Everything below is built on it"),
 "comm1": ("amber", "comment: the Human redlines the one-pager"),
 "gate1": (_G, "haarpi next  →  cosmetic re-runs `onepager`; anything heavier IS a narrative "
               "complaint, because a structure objection to a five-beat narrative means the "
               "through-line is wrong — so it re-cuts"),
 "rel1": ("mint", "one-pager release"),
 "venue": ("indigo", "raconteur venue  →  a slate of candidate venues, analysed from the narrative"),
 "pick": ("amber", "the Human picks the venue — and with it the format, the length, and the "
                   "submission requirements everything downstream is sized to"),
 "skeleton": ("indigo", "raconteur skeleton  →  phase one: the sections and subsections, and the "
                        "words each can afford"),
 "outline": ("indigo", "raconteur outline  →  phase two: the content beats, written onto the "
                       "APPROVED skeleton"),
 "comm2": ("amber", "comment: the Human redlines the outline (or asks to refine one section)"),
 "gate2": (_G, "haarpi next  →  cosmetic and structural both re-run `outline`; a narrative "
               "objection goes back to the one-pager"),
 "rel2": ("mint", "outline release"),
 "draft": ("indigo", "raconteur draft  →  the full manuscript, written from the releases "
                     "upstream: the outline for structure, the litreview, methods digest and "
                     "findings for content"),
 "comm3": ("amber", "comment: the Human redlines the manuscript — accept and resolve are human-only"),
 "gate3": (_G, "haarpi next  →  routes by what the comment costs: in place, re-outline, re-cut, "
               "or back out to the literature"),
 "rel3": ("mint", "paper release: the manuscript"),
 "package": ("indigo", "raconteur package  →  assembles and compiles the venue submission"),
}
LANE = {   # every rung shares these four routes; only where they RE-ENTER differs
 "l_cosmetic": "cosmetic  →  answered in place, on the rung the comment sits on: `draft` "
               "redlines the manuscript, `outline` re-runs phase two, `skeleton` re-runs phase "
               "one. The cheapest route, and the only one that changes nothing above it",
 "l_struct": "structural  →  re-runs `outline`. On the SKELETON rung it re-runs phase one "
             "instead, which is the whole reason that rung exists: without it a comment on a "
             "heading would have queued a full draft against a structure just objected to",
 "l_recut": "narrative  →  `recut` the one-pager from scratch, with the annotations as the "
            "brief. A through-line complaint is always the one-pager's, however far down the "
            "ladder it was raised — and the outline is never rebuilt from a through-line the "
            "author has not signed off",
 "l_upstream": "upstream_literature  →  escalates OUT of this stage: gather, collect, report and "
               "comment, back in the literature review. The claim the comment doubts is not "
               "in the corpus yet",
}
ARTS = {
 "a_op":    ["the one-pager .docx"],
 "a_venue": ["the venue slate"],
 "a_skel":  ["the skeleton", "(sections + word budget)"],
 "a_out":   ["the outline .docx"],
 "a_ms":    ["the manuscript .docx"],
 "a_mint_op":  ["the minted one-pager"],
 "a_mint_out": ["the minted outline"],
 "a_mint":     ["the minted manuscript"],
 "a_sub":   ["the venue submission", "(assembled + compiled)"],
}
MAKES = [("onepager","a_op"), ("venue","a_venue"), ("skeleton","a_skel"),
         ("outline","a_out"), ("draft","a_ms"), ("rel1","a_mint_op"), ("rel2","a_mint_out"),
         ("rel3","a_mint"), ("package","a_sub")]

OPTS = {"gate_exits": {"gate1": 0, "gate2": 1, "gate3": 2},
        "release_keys": ("rel1", "rel2", "rel3"), "gate_key": "gate3",
        "gate_label": "manuscript accepted",
        "hop_labels": {"gate1": ("through-line agreed", GREEN),
                       "gate2": ("structure agreed", GREEN)}}

#!/usr/bin/env python3
"""Stage 1 (rabbitHole surveys the literature) — content only; emitter is wip_svg_panel.

Redrawn 2026-11-13 for claim grounding: a literature review is grounded claim by claim into the
shared card store and gated on one page, and a pass mints the claims release. The narrative
report and its rework (`report`, `revise`, `mindmap`, `comment`) now serve the methods review
only, which stage 2 draws.
"""


# ── what this panel claims to depict ──────────────────────────────────────────
# Checked against planner.STAGE_STEPS / STAGE_TIERS by test_figure_drift.py: add a verb
# to the registry and the suite fails until it is either drawn here or excused below.
STAGE = "litreview"
COVERS = {          # registry step -> the panel key(s) that depict it
 "topics": "topics", "topics-review": "tvet", "topics-apply": "topics",
 "gather": "gather", "collect": "collect", "audit": "audit", "build": "build",
 "ingest": "ingest",
 "cards-plan": "plan",
 "cards-init": "extract", "cards-kind-1": "extract", "cards-kind-2": "extract",
 "cards-extract": "extract",
 "cards-plant": "plant",
 "cards-reread-1": "verify", "cards-reread-2": "verify", "cards-locate": "verify",
 "cards-confirm-1": "verify", "cards-confirm-2": "verify",
 "cards-own-1": "verify", "cards-own-2": "verify",
 "cards-close": "close", "cards-topics": "score", "cards-gate": "page",
 "gate": "judge", "cards-review": "review",
}
_METHODS = ("the methods review only (the fact base it also wrote is retired, 2026-11-13); "
            "stage 2 draws the methods review as one of its rungs")
OMITS = {           # deliberately not drawn, and why
 "graft": "vestigial: only cli.py reaches graft.run(); its drafting functions live "
          "inside revise (revise.py imports draft_sections/choose_position), and "
          "`haarpi next` never selects it",
 "report": _METHODS, "revise": _METHODS, "mindmap": _METHODS, "comment": _METHODS,
}

ROWS = [
 ("hdr",     None,      None),
 ("brief",   None,      "a_cfg"),
 ("topics",  None,      "a_tpage"),
 ("tvet",    None,      None),
 ("gather",  "ingest",  "a_list"),   # ─┐ the revisions band: what the gate can send back —
 ("collect", "audit",   None),       #  │ a search for what is missing (gather down to a new
 ("build",   "review",  "a_corp"),   #  │ grounding graph), or a review by Cale and Claude
 ("plan",    None,      None),       #  │ and a fresh sample (the gate page again)
 ("extract", None,      "a_cards"),  #  │
 ("plant",   None,      None),       #  │
 ("verify",  None,      None),       #  │
 ("close",   None,      "a_slips"),  #  │
 ("score",   None,      None),       #  │
 ("page",    None,      "a_page"),   # ─┘
 ("judge",   None,      "a_ans"),
 ("gate",    None,      None),
 ("rel",     None,      "a_mint"),
]
BAND = (4, 13)

SPINE = {
 "hdr":  ("head", "1. rabbitHole surveys the literature"),
 "brief": ("amber", "haarpi init: the Human sets the brief — the research questions, how "
                    "many sources, the Zotero collection"),
 "topics": ("indigo", "topics  →  drafts the research questions (unless init took them) and the "
                      "topics every confirmed claim will be scored against, as a review page; "
                      "after the Human's review, `topics --apply` writes them into the config"),
 "tvet": ("amber", "the Human accepts or rejects each question and topic on that page and "
                   "saves the answers beside it"),
 "gather": ("indigo", "gather  →  searches, ranks and curates candidate sources, and writes "
                      "the collect-list for the Human to verify"),
 "collect": ("amber", "collect: the Human adds each real source to Zotero WITH its PDF — "
                      "verifying it exists, which is what guards against hallucinated "
                      "citations — then `collect` codes them into the corpus ledger"),
 "build": ("indigo", "build  →  reads THIS review's rows of the audited collection into the "
                     "working corpus: candidates, citekeys, per-paper notes"),
 "plan": ("indigo", "haarpi ground  →  counts what each step still has to do from the card "
                    "store and queues the grounding as a graph: each task waits on every input "
                    "it needs, sized from per-paper rates. A paper carded before costs nothing"),
 "extract": ("indigo", "cards init · kind · extract  →  reads each paper's text into its card, "
                       "two models say what kind of paper it is, and one extracts what the "
                       "paper claims (on both cards, in parts of about 8 h)"),
 "plant": ("indigo", "cards plant  →  plants this run's canaries: bad claims made by code from "
                     "the project's own claims (a changed number, a flipped direction, another "
                     "paper's claim or setting, a borrowed result). None reaches any output"),
 "verify": ("indigo", "cards reread · locate · confirm · own  →  two verifiers from other model "
                      "families each re-read the paper and point to the passage that says the "
                      "claim; code finds it in the paper's own words; each judges the claim "
                      "against the OTHER's passage, and whether it is the paper's own finding. "
                      "A borrowed idea is rejected"),
 "close": ("indigo", "cards close  →  confirms only a claim that passed every check, and "
                     "measures what the canaries say slipped: a kind that slips past both "
                     "verifiers more than 1 in 10, even at the low end of its 95% interval, "
                     "holds the release"),
 "score": ("indigo", "cards topics  →  scores each confirmed claim 0–3 against the project's "
                     "topics (the scores belong to the project; editing a topic re-scores)"),
 "page": ("indigo", "cards gate  →  writes the gate page: the contribution map, what slipped, a "
                    "sample of 25 confirmed claims never judged before, and the claims under "
                    "each topic"),
 "judge": ("amber", "gate: the Human spends about 40 minutes on that page — does the evidence "
                    "say the claim, is it the paper's own, what would a reader of the field "
                    "expect and not find — then pass, no confidence, or hold"),
 "gate": ("purple", "haarpi next  →  reads the saved answers: missing ideas are searched for "
                    "and carded; no confidence, or a canary kind holding, goes to Cale and "
                    "Claude and a fresh sample; a pass with nothing missing releases"),
 "rel": ("mint", "litreview release"),
}
LANE = {
 "ingest": "ingest  →  fetches the references a reviewer NAMED, matching what it can against "
           "Zotero and listing the rest for `collect`",
 "audit":  "audit  →  re-judges each paper against THE ASK THAT FETCHED IT, and marks the "
           "false-friends (shared word, different sense) `quarantine` in the corpus ledger — "
           "they stay in Zotero, and leave the corpus only",
 "review": "cards-review  →  Cale and Claude look at what the person had no confidence in, or "
           "at the canary kind that held, before `cards gate` draws a fresh sample",
}
ARTS = {
 "a_cfg": "litrev.yaml", "a_list": "the collect-list", "a_tpage": "the topics page",
 "a_corp": "the working corpus", "a_cards": "paper cards (cards/, shared by every project)",
 "a_slips": "the slip rates", "a_page": "the gate page + map",
 "a_ans": "the saved answers (…_gate_ra_DCR.json)",
 "a_mint": "the claims release (…_litreview.claims.json)",
}
MAKES = [("brief","a_cfg"), ("topics","a_tpage"), ("gather","a_list"), ("build","a_corp"),
         ("extract","a_cards"), ("close","a_slips"), ("page","a_page"), ("judge","a_ans"),
         ("rel","a_mint")]

OPTS = {"gate_label": "a pass, nothing missing"}

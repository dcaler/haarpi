#!/usr/bin/env python3
"""Stage 3 (raster builds the code) — content only; the emitter is wip_svg_panel."""
from _emitter import GREEN  # noqa: F401  (used by OPTS)

STAGE = "build"
COVERS = {"build_session": ("l_plan", "l_build", "l_hand")}
OMITS = {}

ROWS = [
 ("hdr",      None,     None),
 ("plan",     None,     "a_design"),  # ─┐ the revisions band: everything from the design
 (None,       "l_plan", "a_tasks"),   #  │ doc down to the methods digest is remade when
 ("freeze",   None,     "a_tests"),   #  │ the build re-opens
 ("queue",    "l_build", None),       #  │
 ("doer",     None,     "a_code"),    #  │
 ("gatecode", "l_hand", "a_graph"),   #  │
 ("handoff",  None,     "a_meth"),    # ─┘
 ("comm",     None,     None),
 ("gate",     None,     None),
 ("rel",      None,     "a_mint"),
]
BAND = (1, 7)

SPINE = {
 "hdr": ("head", "3. raster builds the code"),
 "plan": ("amber", "raster plan: from the preregistration and the build brief, Human + Claude author DESIGN.md and tasks.yaml in a LIVE session, and FREEZE the test suite — written before the code that has to satisfy it"),
 "freeze": ("indigo", "raster freeze-review  →  the pre-queue gate: the cross-reference linter over the frozen suite, plus an EXECUTED red-before-green. A suite that passes before the code exists proves nothing"),
 "queue": ("indigo", "raster queue  →  flattens the module/task DAG into one ordered trundlr chain: coding tasks to the GPU, gates to the CPU. A failure auto-breaks everything downstream of it"),
 "doer": ("indigo", "raster build <task>  →  the LLM implements ONE task against its frozen unit test, in a bounded repair loop that escalates worker → strong. Green means commit and push; it cannot edit the test"),
 "gatecode": ("indigo", "raster test <id>  →  NO model: the task's unit test, then the module gate. Regression keeps the already-frozen tree green. A NO PASS does not loop back — it breaks every task downstream"),
 "handoff": ("indigo", "raster handoff  →  emits the Methods Digest for raconteur from DESIGN.md + tasks.yaml + the frozen tests"),
 "comm": ("amber", "comment: the Human redlines the methods digest (accept and resolve are human-only)"),
 "gate": ("purple", "haarpi next  →  reads the markup. Clean MINTS the methods digest; any unresolved comment re-opens the build"),
 "rel": ("mint", "build release: the methods digest, over a green frozen tree"),
}
LANE = {   # the tools `build_session` reaches for — chosen by WHAT the comment is about
 "l_plan": "raster plan  →  re-open the design session when the comment is about WHAT was built: the modules, the contracts, the tests",
 "l_build": "raster build  →  re-run a task when the comment is about HOW it was built. The frozen test still stands; the code moves to it",
 "l_hand": "raster handoff  →  re-emit the digest when the comment is about the WRITE-UP rather than the code underneath it",
}
ARTS = {
 "a_design": ["code/designdocs/DESIGN.md"],
 "a_tasks":  ["tasks.yaml", "(modules, tasks, contracts)"],
 "a_tests":  ["the frozen test suite", "(unit tests, gates, goldens)"],
 "a_code":   ["the built code", "(committed and pushed)"],
 "a_graph":  ["the module graph", "(into the project figure pool)"],
 "a_meth":   ["the methods digest"],
 "a_mint":   ["the minted methods digest"],
}
MAKES = [("plan","a_design"), ("plan","a_tasks"), ("plan","a_tests"),
         ("doer","a_code"), ("gatecode","a_graph"), ("handoff","a_meth"), ("rel","a_mint")]

OPTS = {"gate_label": "digest accepted",
        "hop_labels": {"gatecode": ("PASS · all modules green", GREEN)},
        "loops": [("gatecode", "doer", ["PASS ·", "next module"])]}

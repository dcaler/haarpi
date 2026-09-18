"""`ramus design` — the SECOND design session: bind the methods, plan the study.

The first session (`ramus init`) settles the framework and writes METHODS_SCOPE.md, naming
the methodological families the analytical approach has to be able to defend. rabbitHole then
gathers that literature. This is what happens once it is in hand: the working choices become
bound ones, with a source behind each, and the study is planned against them.

WHY IT IS A SEPARATE SESSION rather than a re-run of init. One project's prereg carried twenty
[FIXED]/[PROVISIONAL] markers and a "methods addendum" that was going to bind them later —
because the design had to be written before the methodology was read. That improvisation is
what this rung formalises, and the difference is that the sources are HERE now. Nothing should
still be provisional when this session ends.

It mints the prereg, which is raster's build target.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from haarpi import project as hproject
from ramus.config import load_config

DESIGN_PROMPT = (
    "You are running the `ramus design` session — the SECOND design conversation, and the one "
    "that finishes the preregistration. "
    "WHAT HAS ALREADY HAPPENED: `ramus init` settled the research questions and the analytical "
    "approach, and wrote design/designdocs/METHODS_SCOPE.md naming the methodological families "
    "that approach has to be able to defend. rabbitHole has since gathered exactly that "
    "literature and minted a METHODS REVIEW. "
    "Read first, in order: design/designdocs/EXPERIMENTS.md (the framework you are binding), "
    "design/designdocs/METHODS_SCOPE.md (what you said you needed), and the minted methods "
    "review in methodsReview/output/ (what was found). "
    "YOUR JOB: bind every methodological choice the framework left open, and plan the study "
    "against them. For each one — the distance measure, the cost scheme, the clustering and "
    "its validity index, the estimator, the validation procedure — state the choice, cite the "
    "source it rests on from the methods review, and say what the standing objection to it is "
    "and why it does not sink this design. A choice with no citation is not bound; a citation "
    "with no objection named has not been read carefully. "
    "NOTHING MAY REMAIN PROVISIONAL. The whole point of the rung you are on is that the "
    "sources are now in hand: a design that still says 'working choice, to be decided later' "
    "is the failure this sequence exists to prevent. If the methods review genuinely does not "
    "settle something, say so EXPLICITLY, say what would, and we decide together — do not "
    "leave it implicit. "
    "Then write the bound design back into design/designdocs/EXPERIMENTS.md, replacing the "
    "provisional language rather than annotating it, and re-render the prereg. "
    "Three hard rules, unchanged from init: (1) cite what each decision rests on; (2) SURFACE "
    "EVERY SCOPE/COMPUTE DECISION for me to confirm — never set one silently; (3) YOU DESIGN "
    "AND SPECIFY ONLY — building the code is raster's job, running the experiments is "
    "rayleigh's, and you never state an un-run number as a result. "
    "When we are done I review the prereg and commit it with `haarpi next`, which mints the "
    "design and hands off to raster. Start by reading the three documents above."
)


def log(msg: str) -> None:
    print(f"[ramus design] {msg}", flush=True)


def run_design(args) -> int:
    root = Path(args.dir).resolve() if getattr(args, "dir", None) else Path.cwd()
    cfg = load_config()

    try:
        m = hproject.load_manifest(root)
    except Exception:  # noqa: BLE001
        log("no haarpi.yaml here — run this from the project root.")
        return 1

    designdocs = root / (m.stages.get("design", {}).get("dir") or "design") / "designdocs"
    framework = designdocs / "EXPERIMENTS.md"
    if not framework.is_file():
        log(f"no {framework.relative_to(root)} — run `haarpi ramus init` first; this session "
            f"binds a framework that already exists.")
        return 1

    # The methods review is this session's whole reason for existing. Refuse rather than let
    # it bind choices against literature that was never gathered.
    if hproject.methods_review_pending(root, m):
        log("this project's METHODS REVIEW has not been released yet, and binding the "
            "methodological choices without it is exactly what this rung exists to prevent.")
        log("  Run the methods chain first: `haarpi rabbithole gather methods`, then "
            "collect/build/report, then gate it.")
        return 1

    scope = hproject.methods_scope_brief(root, m)
    log(f"binding the framework in {framework.relative_to(root)}"
        + (f" against {scope.relative_to(root)}" if scope else ""))

    if getattr(args, "no_launch", False) or shutil.which("claude") is None:
        why = ("--no-launch" if getattr(args, "no_launch", False)
               else "`claude` is not on PATH")
        log(f"{why}; open a session in {root} and follow the three documents above.")
        return 0

    model = getattr(args, "model", "") or cfg.design_model
    use_model = model if model and model.lower() not in ("claude", "default") else ""
    cmd = ["claude"] + (["--model", use_model] if use_model else []) + [DESIGN_PROMPT]
    log(f"launching the binding session ({use_model or 'default'}) in {root} …")
    return subprocess.run(cmd, cwd=str(root)).returncode

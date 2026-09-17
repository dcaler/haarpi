"""Shared design-stage bindings that `rayleigh plan` still needs.

`rayleigh init` MOVED — it is `ramus init` now. Rayleigh's name belongs to the far side of
raster: measuring a thing two ways, finding a discrepancy and refusing to write it off is
interpretation, and the design session has no results to interpret. See packages/ramus/.

What remains here is what the BACK half uses: the helper re-exports (now living in
haarpi.designdocs) and PRIOR_SOURCES, which `plan.py` still references when indexing what
earlier stages left.
"""
from __future__ import annotations

from pathlib import Path

from haarpi.designdocs import (  # noqa: F401
    PRUNE_DIRS as _PRUNE_DIRS, derive_brief as _derive_brief, detect_package,
    discover_priors as _discover_priors, project_name_from_dir, render as _render,
    render_priors_md as _render_priors_md, slugify,
)


def render(template_name: str, ctx: dict) -> str:
    """rayleigh's own templates — experiments.yaml, PROGRESS.md, and the results_* set."""
    return _render("rayleigh", template_name, ctx)


def discover_priors(root, sources=None):
    return _discover_priors(root, sources if sources is not None else PRIOR_SOURCES)


def render_priors_md(root, priors, project: str, cycle: str) -> str:
    return _render_priors_md(root, priors, project, cycle, written_by="rayleigh plan")


def log(msg: str) -> None:
    print(f"[rayleigh] {msg}", flush=True)


PRIOR_SOURCES = [
    # Literature is now the PRIMARY prior: the design (research questions + analytical approach)
    # is derived from the minted litReview + the brief, upstream of any code. Read these first.
    ("Literature (rabbitHole) — PRIMARY", [
        ("litReview/output/*.docx", "the MINTED literature review — the ground for the questions"),
        ("litReview/*.docx", "review drafts / annotations — expected directions, prior findings"),
        ("litReview/*.yaml", "review config — topics + snowball seeds"),
    ]),
    # The prior rayleigh cycle's OWN feedback — on a re-init (a `re-init` verdict from
    # `rayleigh review`) it is the mandate for the redesign. On `--new-cycle` the prior REVIEW.md
    # has just been archived, so glob both live and archived locations.
    ("Prior rayleigh cycle (review + report)", [
        ("design/designdocs/REVIEW.md", "last review — my per-experiment verdicts + next actions (WHY re-init)"),
        ("design/archive/*/designdocs/REVIEW.md", "archived reviews from earlier cycles"),
        ("results/RESULTS.md", "last cycle's report — what was found"),
    ]),
    ("Paper (raconteur)", [
        ("paper/*.md", "paper draft / venue analysis — which questions matter"),
        ("paper/*.yaml", "outline / venue config"),
    ]),
    # A reference / prior-art codebase, IF one exists (e.g. a model being re-implemented). This
    # is prior art to design from — NOT the build target; raster builds code/ AFTER this design.
    ("Reference codebase, if any (prior art — not the build target)", [
        ("code/raster.yaml", "build config — the project brief + package"),
        ("code/README.md", "what the codebase is"),
        ("code/designdocs/DESIGN.md", "the model's design + architecture"),
        ("code/configs/**/*.yaml", "parameter configs — candidate axes + baselines"),
        ("code/**/CLAUDE.md", "codebase agent notes (invariants, known limits)"),
    ]),
]


# Heavy dirs a recursive prior-artifact scan must never descend into — a virtualenv or
# .git sitting inside code/ is tens of thousands of files, and a plain root.glob("**") walks
# every one of them, stalling `init` for many seconds. os.walk lets us prune them by name.

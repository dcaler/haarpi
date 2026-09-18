"""`ramus init` — scaffold design/, open or roll a research cycle, and run the interactive
preregistration session.

RAMUS IS UPSTREAM OF THE CODE. From the minted litReview and the project brief it co-designs
the research questions and the analytical approach, states what would confirm or disconfirm
each, and specifies the data infrastructure raster must then build. It renders a `prereg` docx
that the haarpi gate mints, and that mint is the handoff.

Named for Petrus Ramus, whose project was METHOD — laying a subject out systematically before
reasoning from it, in diagrams that branch from the general to the particular. That is what
this session writes: `framework.dot` is a digraph running research questions -> analytical
approach -> data infrastructure. The Latin sense of the word, a branch, says the same thing.

It was `rayleigh init` until the split. Rayleigh's name belongs to the far side of raster —
his reputation rests on refusing to explain away a 0.5% discrepancy in a nitrogen measurement,
which is interpretation, and there are no results to interpret here. Run from the PROJECT ROOT;
authors into design/ and leaves the root alone.
"""

import json
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path

import yaml

from haarpi.designdocs import (
    derive_brief, detect_package, discover_priors, project_name_from_dir,
    render as _render, render_priors_md as _render_priors_md, slugify,
)
from ramus.config import Config, load_config


def render(template_name: str, ctx: dict) -> str:
    """ramus's own templates — EXPERIMENTS.md, PLANNING.md, ramus.yaml, gitignore."""
    return _render("ramus", template_name, ctx)


def render_priors_md(root: Path, priors, project: str, cycle: str) -> str:
    return _render_priors_md(root, priors, project, cycle, written_by="ramus init")


def log(msg: str) -> None:
    print(f"[ramus init] {msg}", flush=True)


RAMUS_PROMPT = (
    "You are running the `ramus init` design session — an interactive research-design "
    "conversation with me (Cale), not a form-filler. Your job this session: turn the literature "
    "and the project brief into (1) a set of TARGETED RESEARCH QUESTIONS and (2) an ANALYTICAL "
    "APPROACH that answers them. "
    "You are UPSTREAM of the code. The codebase does not exist yet — raster builds it AFTER this "
    "design is committed, to the specification your analytical approach implies. So you SPECIFY "
    "what the analysis needs; you never read or assume finished code, and you never state an "
    "un-run number as a result. "
    "Read first, in order: design/designdocs/PLANNING.md (the playbook — follow it), then "
    "design/designdocs/PRIORS.md (the index of what the earlier tools left — above all the MINTED "
    "litReview and the project brief). Ground everything in those. If PRIORS.md indexes a prior "
    "REVIEW.md (my verdicts from a previous cycle), read it FIRST and treat it as the mandate for "
    "this redesign. "
    "Then run the INTAKE with me: the brief may be thin, so ask me what I want to find out this "
    "cycle and draw it out. From the literature + the brief, propose targeted research questions, "
    "refine them with me, then design the analytical approach to each — the methods, the "
    "estimands, what would confirm or disconfirm each question, and what DATA INFRASTRUCTURE the "
    "approach requires of raster. Write the finalized design into design/designdocs/EXPERIMENTS.md "
    "(the framework: research questions + analytical approach + the data infrastructure raster must "
    "build) and the finalized brief into design/ramus.yaml. You author the FRAMEWORK only — the "
    "executable experiments come later, in `rayleigh plan`, once raster has built the tooling. "
    "METHODOLOGICAL SOURCES. As you settle the analytical approach, ask whether it rests on "
    "methods this project has not read the literature for — a distance measure, an estimator, "
    "a validation procedure whose parameter choices have a published debate behind them. If it "
    "does, write design/designdocs/METHODS_SCOPE.md naming those methodological FAMILIES (not "
    "specific papers): what the approach needs to be able to defend at review. That file opens "
    "a second literature review, gathered against those families rather than against this "
    "project's subject matter — and the preregistration will not mint until it has been "
    "delivered, because binding a method to sources nobody has read is the thing this exists "
    "to prevent. Write nothing if the approach rests only on methods already covered by the "
    "substantive review. "
    "Also draw the framework as a Graphviz DOT diagram in design/designdocs/framework.dot (a `digraph`: "
    "research questions -> analytical approach -> data infrastructure) — ramus renders it onto the "
    "project's figure pool for the paper and the deck. "
    "Three hard rules, and I should not have to remind you of them: (1) READ THE PRIORS FIRST — "
    "the minted litReview and the brief — before proposing any question or approach, and name the "
    "prior each rests on; (2) SURFACE EVERY SCOPE/COMPUTE DECISION for me to confirm — never set "
    "one silently; (3) YOU DESIGN AND SPECIFY ONLY — you don't build code (that is raster) and you "
    "don't run experiments (that is `rayleigh conduct`); never present an un-run number as a "
    "result. "
    "When we're done, I review the design doc and commit it with `haarpi next` — which locks the "
    "preregistration and hands off to raster to build the data infrastructure for your approach. "
    "Start by reading PLANNING.md and PRIORS.md, then talk to me."
)


def archive_cycle(design_dir: Path, prior_cycle: str) -> None:
    """--new-cycle: move the prior cycle's designdocs/ into archive/<cycle>/."""
    dest = design_dir / "archive" / (prior_cycle or "unknown")
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("designdocs",):
        src = design_dir / name
        if src.is_dir() and any(src.iterdir()):
            target = dest / name
            if target.exists():
                shutil.rmtree(target)
            shutil.move(str(src), str(target))
            log(f"archived {name}/ -> {target.relative_to(design_dir.parent)}")


# ----------------------------------------------- prior ra* artifacts (design seed)
# By the time ramus runs, earlier ra* tools have usually left rich context in the
# project. `init` indexes it so the design session can PROPOSE a starting experiment set
# instead of a blank skeleton. (group -> [(glob, what-it-gives-you)])


PRIOR_SOURCES = [
    # Literature is now the PRIMARY prior: the design (research questions + analytical approach)
    # is derived from the minted litReview + the brief, upstream of any code. Read these first.
    ("Literature (rabbitHole) — PRIMARY", [
        ("litReview/output/*.docx", "the MINTED literature review — the ground for the questions"),
        ("litReview/*.docx", "review drafts / annotations — expected directions, prior findings"),
        ("litReview/*.yaml", "review config — topics + snowball seeds"),
    ]),
    # The prior design cycle's OWN feedback — on a re-init (a `re-init` verdict from
    # `rayleigh review`) it is the mandate for the redesign. On `--new-cycle` the prior REVIEW.md
    # has just been archived, so glob both live and archived locations.
    ("Prior design cycle (review + report)", [
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


def render_prereg(design_dir: Path, cfg, short_title: str, cycle: str) -> Path | None:
    """Render the human-facing design doc (designdocs/EXPERIMENTS.md) to the prereg docx the
    gate mints. Named on the revision chain so `haarpi next` recognises it as design-stage
    markup: `{cycle}_{short}_prereg_{ra}.docx` in design/ (infix `prereg`). Track-changes on for
    the OPTIONAL margin review — a clean doc mints in one `haarpi next`. Returns the docx path,
    or None if the source or pandoc is unavailable."""
    src = design_dir / "designdocs" / "EXPERIMENTS.md"
    if not src.is_file():
        return None
    try:
        from haarpi import render as hrender
    except Exception:
        return None
    if not hrender.check_pandoc():
        return None
    dst = design_dir / f"{cycle}_{short_title}_prereg_{cfg.tool_initials}.docx"
    if not hrender.pandoc_convert(src, dst):
        return None
    hrender.enable_track_changes(dst)
    return dst


def launch_session(root: Path, no_launch: bool, model: str = "") -> int:
    playbook = root / "design" / "designdocs" / "PLANNING.md"

    def manual(reason: str) -> int:
        print(reason)
        print(f"  {playbook}")
        print("It reads the minted litReview + the project brief, then co-designs the research")
        print("questions and analytical approach (EXPERIMENTS.md) with you interactively.")
        return 0

    if no_launch:
        return manual("Open a Claude session in this folder and follow:")
    if shutil.which("claude") is None:
        return manual("`claude` is not on PATH — open a session yourself and follow:")
    # The design session is the strong-reasoning step; launch it on the configured model
    # (default Opus) rather than inheriting the CLI default. ("claude" is not a valid
    # --model alias — an older config default — so treat it as "use the CLI default".)
    use_model = model if model and model.lower() not in ("claude", "default") else ""
    cmd = ["claude"] + (["--model", use_model] if use_model else []) + [RAMUS_PROMPT]
    model = use_model
    print(f"[ramus init] launching an interactive Claude design session "
          f"({model or 'default'}) in {root} …")
    # Run from the project root so the session sees design/, the minted litReview/, and any
    # paper/ or reference code/. Inherits this terminal's stdio (fully interactive).
    return subprocess.run(cmd, cwd=str(root)).returncode


def run_init(args) -> int:
    cfg = load_config()
    root = Path(args.dir).resolve() if args.dir else Path.cwd()
    # The DESIGN stage authors into its OWN directory (design/), upstream of build. conduct/
    # process run later in results/ — a different stage. See DESIGN_experiment_split.md.
    design = root / "design"
    designdocs = design / "designdocs"
    existing = design / "ramus.yaml"

    prior = {}
    if existing.is_file():
        try:
            prior = yaml.safe_load(existing.read_text()) or {}
        except Exception:
            prior = {}

    log(f"project root: {root}")
    today = date.today().strftime("%y%m%d")
    prior_cycle = str(prior.get("cycle") or "")

    if getattr(args, "new_cycle", False) and design.exists():
        archive_cycle(design, prior_cycle)
        cycle = today
        prior = {}                       # fresh cycle: don't inherit the archived spec
    else:
        cycle = prior_cycle or today

    # No interactive prompting here — the intake ("what do you want to find out?") is the
    # intellectual work, so the launched Claude session does it, grounded in the priors it
    # reads. init only resolves deterministic defaults; the session refines them with the user.
    name = (args.name or prior.get("project") or project_name_from_dir(root.name)).strip()
    brief = (args.brief or prior.get("brief") or derive_brief(root) or "").strip()
    if brief:
        log(f"starting brief: {brief[:70]}{'…' if len(brief) > 70 else ''}")
    else:
        log("no brief yet — the design session will elicit it from you + the priors")

    code_dir = root / "code"
    package = detect_package(code_dir, slugify(name))
    code_path = (prior.get("code", {}) or {}).get("path") or "../code"

    ctx = {
        "PROJECT": name,
        "PACKAGE": package,
        "CYCLE": cycle,
        "CODE_PATH": code_path,
        "BRIEF": brief or "(not provided at init — clarify with the user during the session)",
        "BRIEF_YAML": json.dumps(brief or "(not provided at init)"),
        "AUTHOR": cfg.author_name,
        "TOOL_INITIALS": cfg.tool_initials,
        "USER_INITIALS": cfg.user_initials,
        "TRUNDLR_API": cfg.trundlr_api,
        "GPU_RES": cfg.gpu_resource,
        "CPU": cfg.cpu_resource,
        "DATE": date.today().isoformat(),
    }

    # ---- scaffold design/ (idempotent; never clobber authored design docs) ----
    designdocs.mkdir(parents=True, exist_ok=True)
    (design / "output").mkdir(exist_ok=True)      # where the minted prereg release lands

    def write(path: Path, template: str, protect: bool = False):
        if protect and path.exists() and path.read_text().strip():
            log(f"kept existing {path.relative_to(root)} (not overwritten)")
            return
        path.write_text(render(template, ctx))
        log(f"wrote {path.relative_to(root)}")

    write(design / "ramus.yaml", "ramus.yaml.tmpl")
    write(design / ".gitignore", "gitignore.tmpl")
    write(designdocs / "PLANNING.md", "PLANNING.md.tmpl")           # refreshed each run
    write(designdocs / "EXPERIMENTS.md", "EXPERIMENTS.md.tmpl", protect=True)
    # The EXECUTABLE experiments.yaml (+ PROGRESS) belong to the experiments stage — authored by
    # `rayleigh plan` against the raster-built code, not here. init produces the FRAMEWORK only.

    # Index the prior ra* artifacts so the design session can propose from them (refreshed each run).
    priors = discover_priors(root, PRIOR_SOURCES)
    (designdocs / "PRIORS.md").write_text(render_priors_md(root, priors, name, cycle))
    n_priors = sum(len(matches) for _, items in priors for _, matches in items)
    log(f"wrote design/designdocs/PRIORS.md ({n_priors} prior artifact(s) indexed)")

    log(f"cycle {cycle} · analytical target: {package} ({code_path}, to be BUILT by raster)")
    log("done.")
    print()
    print(f"  Scaffolded design/ for {name} (cycle {cycle}) in {design}")
    # design/ is a working folder, not a repo — no git init here (unlike raster).
    interactive = not getattr(args, "no_launch", False) and shutil.which("claude") is not None
    rc = launch_session(root, getattr(args, "no_launch", False), model=cfg.design_model)
    if interactive and rc == 0:
        # The design doc is now authored. Render it to the prereg docx the gate mints — review
        # it, then `haarpi next` commits the preregistration and hands off to raster.
        doc = render_prereg(design, cfg, name, cycle)
        if doc is not None:
            print(f"\n  Rendered the preregistration for review: {doc.relative_to(root)}")
            print("  Review it, then `haarpi next` to commit the design → hands off to raster.")
        else:
            print("\n  (Render the prereg for review once the design doc is ready.)")
        # If the session authored a framework schematic (Graphviz DOT), render + chain it onto the
        # project's figure pool — a conceptual figure both the paper and the deck will want.
        from haarpi import figure as hfigure
        short = hfigure.project_short_title(root, slugify(name))
        fw = hfigure.chain_authored_dot(root, short, "analyticalFramework",
                                         designdocs / "framework.dot", "The analytical framework.")
        if fw and fw.get("svg"):
            print(f"  Rendered the framework schematic: {fw['svg'].relative_to(root)}")
    return rc

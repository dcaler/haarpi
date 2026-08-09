"""`rayleigh plan` — the EXPERIMENTS stage: design the executable experiments that use the
raster-built tooling to fulfil the committed analytical framework, then hand off to conduct.

The SECOND interactive Cale+Claude session. `rayleigh init` (the design stage) designed the
analytical FRAMEWORK upstream of the code; `plan` runs AFTER raster has built the tooling. It
reads the minted prereg (the framework) + the real, built `code/` (its entrypoint, config surface,
output format) and authors the EXECUTABLE `results/designdocs/experiments.yaml` — sweeps/cells,
metrics, and a `run_adapter` bound to the actual code — which `conduct`/`process` then run.

Works in `results/` (the experiments stage's directory), which is exactly where `conduct` and
`queue` already read the spec + config from — so no repoint is needed.
"""

import shutil
import subprocess
from datetime import date
from pathlib import Path

from rayleigh.config import load_config
from rayleigh.init import (_derive_brief, detect_package, discover_priors,
                           project_name_from_dir, render, render_priors_md, slugify)


def log(msg: str) -> None:
    print(f"[rayleigh plan] {msg}", flush=True)


PLAN_PROMPT = (
    "You are running the `rayleigh plan` session — an interactive experiment-design conversation "
    "with me (Cale), not a form-filler. raster has now BUILT the tooling; your job is to design "
    "the EXECUTABLE experiments that USE it to fulfil the committed analytical framework, then hand "
    "off to conduct. "
    "Read first, in order: results/designdocs/PLANNING.md (the playbook — follow it); then the "
    "committed PREREG — design/output/…_prereg + design/designdocs/EXPERIMENTS.md — which holds the "
    "research questions + analytical approach you must fulfil; then the BUILT code/ (its "
    "README/CLAUDE.md, its single-run entrypoint, its config surface, its output format — these "
    "become the run_adapter). "
    "Then design the executable experiments WITH me and write them to "
    "results/designdocs/experiments.yaml (sweeps/conditions, seeds, metrics, planned outputs, and the "
    "run_adapter bound to the REAL entrypoint), mirrored in results/designdocs/EXPERIMENTS.md. "
    "Hard rules, and I should not have to remind you of them: (1) EVERY experiment traces to a prereg "
    "question — build nothing the framework didn't ask for; (2) SURFACE the cell count and compute "
    "(workers, resource cpu|gpu) for me to confirm BEFORE you queue anything — never silently; (3) you "
    "DESIGN and QUEUE only — you never run silently and never state an un-run number as a result. "
    "When the design is settled and I've confirmed the compute, hand off to conduct: `rayleigh queue` "
    "(fan the experiments out to trundlr) or `rayleigh conduct <E>` locally, then `rayleigh process`. "
    "Start by reading PLANNING.md."
)

# Priors for the experiments session: the committed prereg (the framework it must fulfil) and the
# now-BUILT code/ (the tooling it designs experiments against) come first; then the literature.
PLAN_PRIOR_SOURCES = [
    ("Committed design / prereg (the framework to fulfil) — PRIMARY", [
        ("design/output/*_prereg.docx", "the MINTED preregistration — questions + analytical approach"),
        ("design/output/*_prereg.md", "the minted prereg (markdown)"),
        ("design/designdocs/EXPERIMENTS.md", "the design doc — incl. 'Data infrastructure required'"),
    ]),
    ("Built tooling (raster) — the code the experiments run against", [
        ("code/README.md", "what the codebase is"),
        ("code/**/CLAUDE.md", "codebase agent notes (invariants, entrypoint, known limits)"),
        ("code/output/*_methods*.md", "the minted methods digest — what the build provides"),
        ("code/designdocs/DESIGN.md", "the model's design + architecture"),
        ("code/configs/**/*.yaml", "parameter configs — candidate sweep axes + baselines"),
    ]),
    ("Literature (rabbitHole)", [
        ("litReview/output/*.docx", "the minted literature review"),
    ]),
]


def run_plan(args) -> int:
    cfg = load_config()
    root = Path(args.dir).resolve() if getattr(args, "dir", None) else Path.cwd()
    results = root / "results"
    designdocs = results / "designdocs"
    existing = results / "rayleigh.yaml"

    prior = {}
    if existing.is_file():
        try:
            import yaml
            prior = yaml.safe_load(existing.read_text()) or {}
        except Exception:
            prior = {}

    log(f"project root: {root}")
    today = date.today().strftime("%y%m%d")
    cycle = str(prior.get("cycle") or today)

    name = (getattr(args, "name", None) or prior.get("project")
            or project_name_from_dir(root.name)).strip()
    brief = (getattr(args, "brief", None) or prior.get("brief") or _derive_brief(root) or "").strip()

    code_dir = root / "code"
    package = detect_package(code_dir, slugify(name))
    code_path = (prior.get("code", {}) or {}).get("path") or "../code"

    ctx = {
        "PROJECT": name, "PACKAGE": package, "CYCLE": cycle, "CODE_PATH": code_path,
        "BRIEF": brief or "(from the committed prereg — read design/designdocs/EXPERIMENTS.md)",
        "BRIEF_YAML": __import__("json").dumps(brief or "(see the prereg)"),
        "AUTHOR": cfg.author_name, "TOOL_INITIALS": cfg.tool_initials,
        "USER_INITIALS": cfg.user_initials, "TRUNDLR_API": cfg.trundlr_api,
        "GPU_RES": cfg.gpu_resource, "CPU": cfg.cpu_resource, "DATE": date.today().isoformat(),
    }

    # ---- scaffold results/ (idempotent; never clobber authored experiments) ----
    designdocs.mkdir(parents=True, exist_ok=True)
    (results / "data").mkdir(exist_ok=True)
    (results / "figures").mkdir(exist_ok=True)

    def write(path: Path, template: str, protect: bool = False):
        if protect and path.exists() and path.read_text().strip():
            log(f"kept existing {path.relative_to(root)} (not overwritten)")
            return
        path.write_text(render(template, ctx))
        log(f"wrote {path.relative_to(root)}")

    write(results / "rayleigh.yaml", "rayleigh.yaml.tmpl")
    write(results / ".gitignore", "results_gitignore.tmpl")
    write(designdocs / "PLANNING.md", "results_PLANNING.md.tmpl")          # refreshed each run
    write(designdocs / "EXPERIMENTS.md", "results_EXPERIMENTS.md.tmpl", protect=True)
    write(designdocs / "experiments.yaml", "experiments.yaml.tmpl", protect=True)
    write(designdocs / "PROGRESS.md", "PROGRESS.md.tmpl", protect=True)

    priors = discover_priors(root, PLAN_PRIOR_SOURCES)
    (designdocs / "PRIORS.md").write_text(render_priors_md(root, priors, name, cycle))
    n_priors = sum(len(matches) for _, items in priors for _, matches in items)
    log(f"wrote results/designdocs/PRIORS.md ({n_priors} prior artifact(s) indexed)")

    if not list((root / "design" / "output").glob("*_prereg*")):
        log("note: no committed prereg found under design/ — plan expects the design stage to have "
            "minted first. Design against the brief, but confirm the framework with me.")

    log(f"cycle {cycle} · experiments run against: {package} ({code_path})")
    log("done.")
    print()
    print(f"  Scaffolded results/ for {name} (cycle {cycle}) in {results}")
    interactive = not getattr(args, "no_launch", False) and shutil.which("claude") is not None
    rc = launch_session(root, getattr(args, "no_launch", False), model=cfg.design_model)
    if interactive and rc == 0:
        # The session has authored experiments.yaml — render the experiment DAG onto the pool
        # (deterministic, no model) for the paper and the deck.
        from rayleigh import figures as rfigures
        short = rfigures.short_title(root, slugify(name))
        dag = rfigures.emit_experiment_dag(root, short)
        if dag and dag.get("svg"):
            print(f"\n  Rendered the experiment DAG: {dag['svg'].relative_to(root)}")
    return rc


def launch_session(root: Path, no_launch: bool, model: str = "") -> int:
    playbook = root / "results" / "designdocs" / "PLANNING.md"

    def manual(reason: str) -> int:
        print(reason)
        print(f"  {playbook}")
        print("It reads the committed prereg + the built code/, then co-designs the executable")
        print("experiments (experiments.yaml) with you, and hands off to `rayleigh queue`/`conduct`.")
        return 0

    if no_launch:
        return manual("Open a Claude session in this folder and follow:")
    if shutil.which("claude") is None:
        return manual("`claude` is not on PATH — open a session yourself and follow:")
    use_model = model if model and model.lower() not in ("claude", "default") else ""
    cmd = ["claude"] + (["--model", use_model] if use_model else []) + [PLAN_PROMPT]
    print(f"[rayleigh plan] launching an interactive Claude experiment-design session "
          f"({use_model or 'default'}) in {root} …")
    # Run from the project root so the session sees results/, the committed design/, and the
    # built code/. Inherits this terminal's stdio (fully interactive).
    return subprocess.run(cmd, cwd=str(root)).returncode

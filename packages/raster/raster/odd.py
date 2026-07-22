"""`raster odd` — the ODD protocol for an agent-based model, written at the end of the build.

A SUMMARY OF WHAT WAS BUILT, not a design document. The ODD (Overview, Design concepts,
Details — Grimm et al. 2006, 2010, 2020) is the standard by which an ABM is described well
enough that another modeller could rebuild it, and it is the appendix a journal expects. It
is written here, after the build, because a design document written before the build
describes what was intended; the appendix has to describe what exists.

Its seven elements come from the artefact that actually knows:

  1 Purpose and patterns          DESIGN.md's aim + the init brief
  2 Entities, state variables     the implemented classes and their attributes; configs
  3 Process overview, scheduling  DESIGN.md's spine + the step/tick entrypoint
  4 Design concepts               the planning conversation — DESIGN.md is where the
                                  modeller already said what adapts, what emerges, what is
                                  stochastic. Where it did not, this says so rather than
                                  inventing a claim about the model.
  5 Initialisation                code/configs/
  6 Input data                    code/inputs/ — its absence is itself the answer, and the
                                  standard wording for it ("the model uses no input data")
  7 Submodels                     tasks.yaml contracts + the frozen suite's pinned constants

THE EVIDENCE IS GATHERED MECHANICALLY AND THE PROSE IS WRITTEN BY THE MODEL, in three passes
over three disjoint slices — the model never sees the implementation tree, only the digest of
it, for the reason `handoff` gives: the tree is the incidental implementation of the design,
and the worst of the available sources. Each pass gets the evidence for its own elements and
nothing else, so no prompt carries the whole project.

ABM-ONLY, and it is asked rather than inferred: `kind: abm` in raster.yaml. An ODD is a
commitment about what a model IS, and guessing that from whether the code says "agent"
would put a protocol on a project that has no business carrying one.
"""

import ast
import re
from datetime import date as _date
from pathlib import Path

from raster import ollama
from raster.init import slugify
from raster.report import design_sections, frozen_claims, module_contracts
from raster.runlog import log
from raster.spec import load_project

# The canonical element titles. Reproduced verbatim because a reader of an ODD navigates by
# them, and a paraphrased heading is a protocol the reader has to re-derive.
ELEMENTS = [
    ("1. Purpose and patterns", "overview"),
    ("2. Entities, state variables, and scales", "overview"),
    ("3. Process overview and scheduling", "overview"),
    ("4. Design concepts", "concepts"),
    ("5. Initialisation", "details"),
    ("6. Input data", "details"),
    ("7. Submodels", "details"),
]

# The eleven design concepts, in the protocol's order. Named explicitly so the pass writing
# element 4 answers all of them or says which the design docs never settled.
DESIGN_CONCEPTS = [
    "Basic principles", "Emergence", "Adaptation", "Objectives", "Learning",
    "Prediction", "Sensing", "Interaction", "Stochasticity", "Collectives", "Observation",
]

_UNSTATED = "_(not settled in the design documents — the modeller should state this.)_"


def is_abm(project) -> bool:
    """Whether this project declared itself an agent-based model at init."""
    return str((project.ry or {}).get("kind", "")).strip().lower() == "abm"


# ── mechanical evidence ──────────────────────────────────────────────────────

def _py_files(code: Path, package: str) -> list:
    pkg = code / package
    return sorted(p for p in pkg.rglob("*.py") if p.name != "__init__.py") if pkg.is_dir() else []


def entities(code: Path, package: str) -> list:
    """Classes and the state variables they carry: [(qualname, docline, [attributes])].

    An ODD's element 2 is exactly this — what kinds of thing exist and what each remembers.
    Attributes are read from assignments to `self.x` in __init__, which is where a state
    variable is declared in practice; reading them from the class body alone would miss
    every one that is configured per instance.
    """
    out = []
    for p in _py_files(code, package):
        try:
            tree = ast.parse(p.read_text())
        except (SyntaxError, OSError):
            continue
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            doc = (ast.get_docstring(node) or "").strip().splitlines()
            attrs = []
            for sub in ast.walk(node):
                if not (isinstance(sub, ast.FunctionDef) and sub.name == "__init__"):
                    continue
                for stmt in ast.walk(sub):
                    for tgt in (stmt.targets if isinstance(stmt, ast.Assign) else
                                [stmt.target] if isinstance(stmt, ast.AnnAssign) else []):
                        if (isinstance(tgt, ast.Attribute) and isinstance(tgt.value, ast.Name)
                                and tgt.value.id == "self" and tgt.attr not in attrs):
                            attrs.append(tgt.attr)
            out.append((f"{p.stem}.{node.name}", doc[0] if doc else "", attrs))
    return out


_STEP_NAMES = ("step", "tick", "update", "run", "advance", "schedule", "iterate")


def scheduling(code: Path, package: str) -> list:
    """Functions and methods whose names say they advance the model: [(qualname, docline)].

    Element 3 is about the order things happen in, and the step function is where that order
    is written down. Matched by name because a schedule is a naming convention in every ABM
    framework there is.
    """
    out = []
    for p in _py_files(code, package):
        try:
            tree = ast.parse(p.read_text())
        except (SyntaxError, OSError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                    node.name.lower().lstrip("_") in _STEP_NAMES:
                doc = (ast.get_docstring(node) or "").strip().splitlines()
                out.append((f"{p.stem}.{node.name}", doc[0] if doc else ""))
    return out


_RNG_RE = re.compile(r"\b(?:random\.\w+|np\.random\.\w+|numpy\.random\.\w+|"
                     r"rng\.\w+|default_rng|Generator|shuffle|choice|seed)\b")


def stochasticity(code: Path, package: str) -> list:
    """Where randomness enters: [(file, line, source)].

    A design concept the code answers better than any document: the model is stochastic
    exactly where it draws. Reported as sites so element 4 can say what is random and what
    is not, instead of asserting either.
    """
    out = []
    for p in _py_files(code, package):
        try:
            lines = p.read_text().splitlines()
        except OSError:
            continue
        for i, ln in enumerate(lines, 1):
            if _RNG_RE.search(ln) and not ln.lstrip().startswith("#"):
                out.append((p.name, i, ln.strip()[:120]))
    return out[:40]


def configs(code: Path) -> list:
    """Config files and their top-level keys — the scales and initial conditions."""
    d = code / "configs"
    out = []
    for p in sorted(d.glob("*")) if d.is_dir() else []:
        if p.suffix.lower() not in (".yaml", ".yml", ".json", ".toml"):
            continue
        keys = []
        try:
            if p.suffix.lower() in (".yaml", ".yml"):
                import yaml
                data = yaml.safe_load(p.read_text())
            elif p.suffix.lower() == ".json":
                import json
                data = json.loads(p.read_text())
            else:
                data = None
            if isinstance(data, dict):
                keys = [f"{k}: {v!r}"[:80] for k, v in data.items()]
        except Exception:                                # noqa: BLE001 — evidence, not a gate
            keys = []
        out.append((p.name, keys))
    return out


def input_data(code: Path) -> list:
    """Files under code/inputs/ — element 6, where its ABSENCE is the answer.

    "The model uses no input data" is a real and common ODD answer, and one the protocol
    asks for explicitly. An empty list here means exactly that, and is not a gap.
    """
    d = code / "inputs"
    if not d.is_dir():
        return []
    return [f"{p.relative_to(d)} ({p.stat().st_size} bytes)"
            for p in sorted(d.rglob("*")) if p.is_file()]


# ── the three passes ─────────────────────────────────────────────────────────

_SYSTEM = (
    "You write the ODD protocol (Overview, Design concepts, Details) for an agent-based "
    "model that has ALREADY BEEN BUILT. You are describing what exists, from evidence "
    "gathered out of its design documents, its code and its tests. Write precise, plain "
    "scholarly prose. Never invent a mechanism, a parameter, a value or a source of "
    "randomness that the evidence does not show; where the evidence does not settle "
    "something, say so in one short sentence and move on."
)

_OVERVIEW_PROMPT = """\
Write elements 1-3 of the ODD protocol for this agent-based model.

Model: {name}
{description}
Purpose stated at the outset:
{brief}

DESIGN DOCUMENT (the modeller's own narrative):
{design}

ENTITIES FOUND IN THE BUILT CODE — class, its docstring, and the state variables it \
carries:
{entities}

FUNCTIONS THAT ADVANCE THE MODEL (name and docstring):
{scheduling}

CONFIGURATION FILES (the scales and parameter values the model actually runs at):
{configs}

Write exactly these three sections and nothing else:

## 1. Purpose and patterns
What question the model was built to answer, and the patterns or phenomena against which \
it is judged.

## 2. Entities, state variables, and scales
The kinds of entity the model contains, the state variables each carries, and the \
model's spatial and temporal scale and extent. Name the variables as the code names them.

## 3. Process overview and scheduling
What happens in one time step, in order, and what is updated when. Say whether updating \
is synchronous or asynchronous only if the evidence shows it.

Output only those three sections, as Markdown, starting with "## 1.". No preamble.
"""

_CONCEPTS_PROMPT = """\
Write element 4 of the ODD protocol — the design concepts — for this agent-based model.

Model: {name}

DESIGN DOCUMENT (where the modeller said what the model does and why — this is the \
primary source for these concepts):
{design}

ENTITIES AND THEIR STATE VARIABLES:
{entities}

WHERE RANDOMNESS ENTERS THE BUILT CODE (file, line, source):
{stochasticity}

WHAT THE MODEL RECORDS (its outputs and tests):
{observation}

Write one subsection per concept, in this order, each a "### " heading:
{concepts}

Rules:
- Each concept gets a short paragraph, or the single sentence that it does not apply to \
this model (many do not — "There is no learning in this model" is a complete and correct \
answer, and a better one than a paragraph invented to fill the space).
- Stochasticity must be grounded in the sites listed above: say what is drawn at random \
and what is deterministic. If nothing is drawn, say the model is deterministic.
- Never claim an agent predicts, learns, or optimises unless the evidence shows the \
mechanism that does it.
- Do not restate the model's purpose here; that is element 1.

Output only the "### " subsections, as Markdown. No preamble.
"""

_DETAILS_PROMPT = """\
Write elements 5-7 of the ODD protocol for this agent-based model.

Model: {name}

CONFIGURATION FILES (the initial state and parameter values):
{configs}

INPUT DATA FILES FOUND UNDER code/inputs/:
{inputs}

MODULE AND TASK CONTRACTS (what each part of the model was built to do):
{contracts}

THE FROZEN TEST SUITE — each test's intent and the constants it pins. These are the \
calibrated quantitative claims the build had to satisfy:
{frozen}

Write exactly these three sections and nothing else:

## 5. Initialisation
The state the model starts in, the parameter values it starts from, and whether \
initialisation varies between runs.

## 6. Input data
What external data the model reads. IF THE INPUT LIST ABOVE IS EMPTY, this section is \
the single sentence "The model does not use input data from external sources." — that is \
the protocol's standard answer and it is correct.

## 7. Submodels
Each submodel that implements a process named in element 3: what it does, and the \
parameters and pinned values it uses. Ground every number in the frozen suite above.

Output only those three sections, as Markdown, starting with "## 5.". No preamble.
"""


def _fmt(rows, empty="_(none found)_") -> str:
    return "\n".join(rows) if rows else empty


def _evidence(project) -> dict:
    """Everything the passes draw on, gathered once."""
    code, pkg = project.code, project.package
    ents = entities(code, pkg)
    return {
        "design": _fmt([f"## {h}\n{b}" for h, b in design_sections(code) if b],
                       "_(no DESIGN.md authored)_"),
        "entities": _fmt([f"- {q}" + (f" — {d}" if d else "")
                          + (f"\n  state: {', '.join(a)}" if a else "")
                          for q, d, a in ents]),
        "scheduling": _fmt([f"- {q}" + (f" — {d}" if d else "")
                            for q, d in scheduling(code, pkg)]),
        "stochasticity": _fmt([f"- {f}:{n}  {s}" for f, n, s in stochasticity(code, pkg)],
                              "_(no randomness found — the model appears deterministic)_"),
        "configs": _fmt([f"- {n}\n  " + "\n  ".join(k) if k else f"- {n}"
                         for n, k in configs(code)]),
        "inputs": _fmt(input_data(code), "_(no code/inputs/ directory — no input data)_"),
        "contracts": _fmt([f"- {m['id']} {m['name']}: " + "; ".join(
            f"{t['id']} {t['title']}" + (f" ({t['spec']})" if t["spec"] else "")
            for t in m["tasks"]) for m in module_contracts(project.spec)]),
        "frozen": _fmt([f"- `{rel}`" + (f" — {doc}" if doc else "")
                        + ("".join(f"\n  - {c}" for c in consts) if consts else "")
                        for rel, doc, consts in frozen_claims(code)]),
    }


def _ask(project, prompt: str, label: str) -> str:
    """One pass. Strong model: an ODD is read as a specification of the model."""
    reply = ollama.chat(project.ollama_host(), project.strong_model(),
                        [{"role": "system", "content": _SYSTEM},
                         {"role": "user", "content": prompt}],
                        label=f"odd:{label}")
    return (reply or "").strip()


def build_odd(project, today=None) -> str:
    today = today or _date.today()
    ev = _evidence(project)
    brief = str((project.ry or {}).get("brief") or "").strip() or "_(none recorded)_"
    desc = f"Description: {project.description}\n" if project.description else ""

    log("odd: writing elements 1-3 (overview)…")
    overview = _ask(project, _OVERVIEW_PROMPT.format(
        name=project.name, description=desc, brief=brief, design=ev["design"],
        entities=ev["entities"], scheduling=ev["scheduling"], configs=ev["configs"]),
        "overview")

    log("odd: writing element 4 (design concepts)…")
    concepts = _ask(project, _CONCEPTS_PROMPT.format(
        name=project.name, design=ev["design"], entities=ev["entities"],
        stochasticity=ev["stochasticity"], observation=ev["frozen"],
        concepts="\n".join(f"### {c}" for c in DESIGN_CONCEPTS)), "concepts")

    log("odd: writing elements 5-7 (details)…")
    details = _ask(project, _DETAILS_PROMPT.format(
        name=project.name, configs=ev["configs"], inputs=ev["inputs"],
        contracts=ev["contracts"], frozen=ev["frozen"]), "details")

    text = "\n\n".join([
        f"# {project.name} — ODD Protocol",
        f"*Overview, Design concepts, Details (Grimm et al. 2006; 2010; 2020). "
        f"Written by `raster odd` on {today:%Y-%m-%d} from the model AS BUILT: its design "
        f"documents, its implemented code, its configuration and its frozen test suite. "
        f"Intended as the paper's appendix — review it before it goes out.*",
        overview or _UNSTATED,
        "## 4. Design concepts",
        concepts or _UNSTATED,
        details or _UNSTATED,
    ]).rstrip() + "\n"
    for missing in check_complete(text):
        log(f"[warn] odd: element missing from the protocol — {missing}")
    return text


def check_complete(text: str) -> list:
    """Which of the seven elements and eleven concepts the written protocol does not carry.

    An ODD is read as a specification, and a reader navigates it by these headings — so an
    element the model quietly merged into its neighbour is one the reader concludes the
    modeller never considered. Whether a section is GOOD is a judgement; whether it is
    THERE is arithmetic, and this is the arithmetic.
    """
    missing = [title for title, _group in ELEMENTS if f"## {title}" not in text]
    # Only worth asking once element 4 is actually present to look inside.
    if "## 4. Design concepts" in text:
        missing += [f"design concept: {c}" for c in DESIGN_CONCEPTS
                    if f"### {c}" not in text]
    return missing


def default_odd_path(project, today=None) -> Path:
    """Beside the methods digest, in the build stage's output dir, revision-chain named."""
    today = today or _date.today()
    return project.code / "output" / f"{today:%y%m%d}_{slugify(project.name)}_odd_ra.md"


def run_odd(args) -> int:
    project = load_project(getattr(args, "dir", None))
    if not is_abm(project) and not getattr(args, "force", False):
        log("odd: this project is not declared an agent-based model "
            "(raster.yaml has no `kind: abm`) — nothing written.")
        log("     An ODD describes an ABM; set `kind: abm` if that is what this is, "
            "or re-run with --force.")
        return 0
    text = build_odd(project)
    if getattr(args, "dry_run", False):
        print(text)
        return 0
    out = Path(args.out).resolve() if getattr(args, "out", None) else default_odd_path(project)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    log(f"odd: wrote ODD protocol -> {out}")
    log(f"  {len(text)} chars. It is a SUMMARY OF WHAT WAS BUILT and the paper's appendix — "
        f"read it before it ships.")
    return 0

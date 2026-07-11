"""`raster handoff` — emit a Methods Digest for the raconteur handoff.

raster already writes down, precisely, everything a Methods section needs — but scattered
across build-facing artifacts a paper tool would have to reverse-engineer:

  * DESIGN.md           — the human narrative (aim, the spine, domain model, contracts).
  * designdocs/tasks.yaml — the module/task decomposition and per-deliverable contract.
  * code/tests/         — the FROZEN suite: the calibrated, reviewed quantitative claims
                          (golden constants + invariants) the build had to satisfy.
  * raster.yaml + meta  — provenance: the brief, and WHICH local models built it.

The raw `code/{package}/` tree is the WORST source of the four — it's the incidental
implementation of the above. This command distills the four into one methods-facing
Markdown digest, filtering the doer-pipeline machinery, and drops it in the shared ra*
workspace (the project ROOT, alongside paper/ and litReview/) under the revision-chain
name `{YYMMDD}_{slug}_methods_ra.md` — so raconteur reads ONE document, not the tree.

This is a MECHANICAL command (no LLM, like `test`/`lint`/`freeze-review`): raster assembles
faithful, structured source material; raconteur is the writer that turns it into prose.
"""

import ast
import re
from datetime import date as _date
from pathlib import Path

from raster.init import slugify
from raster.runlog import log
from raster.spec import load_project

_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
# DESIGN sections most load-bearing for a Methods section, in the order a paper wants them.
# Matched loosely against the authored heading text (the template numbers them 1..5).
_METHODS_SECTIONS = ["aim", "spine", "concept", "domain", "architecture", "interface", "contract"]


def _strip_comments(text: str) -> str:
    return _HTML_COMMENT.sub("", text).strip()


def design_sections(code: Path) -> list:
    """Parse code/designdocs/DESIGN.md into [(heading, body)] top-level (`## `) sections,
    HTML-comment stripped. Empty bodies (an unauthored skeleton section) survive as '' so
    the digest can flag them rather than silently drop the aim/contract the paper needs."""
    p = code / "designdocs" / "DESIGN.md"
    if not p.is_file():
        return []
    lines = p.read_text().splitlines()
    sections, heading, buf = [], None, []
    for ln in lines:
        if ln.startswith("## "):
            if heading is not None:
                sections.append((heading, _strip_comments("\n".join(buf))))
            heading, buf = ln[3:].strip(), []
        elif heading is not None:
            buf.append(ln)
    if heading is not None:
        sections.append((heading, _strip_comments("\n".join(buf))))
    return sections


def _is_methods_heading(heading: str) -> bool:
    h = heading.lower()
    return any(k in h for k in _METHODS_SECTIONS)


def module_contracts(spec: dict) -> list:
    """Structured architecture from tasks.yaml: one dict per module with its gate and the
    per-task contract (deliverable file + unit test + the spec/checkpoint the doer built to).
    The P0.* freeze-authoring tasks are build machinery, not deliverables — excluded."""
    out = []
    for m in spec.get("modules", []) or []:
        mid = str(m.get("id", ""))
        if mid.startswith("P0"):
            continue
        tasks = []
        for t in m.get("tasks", []) or []:
            if str(t.get("id", "")).startswith("P0"):
                continue
            ut = t.get("unit_test") or {}
            deliv = [d for d in (t.get("deliverables") or []) if not str(d).lstrip("/").startswith("tests/")]
            tasks.append({
                "id": str(t.get("id", "")),
                "title": t.get("title", ""),
                "deliverables": deliv,
                "spec": (t.get("spec") or t.get("description") or "").strip(),
                "checkpoint": (t.get("checkpoint") or "").strip(),
                "unit_test": ut.get("file", ""),
                "worker": t.get("worker", ""),
            })
        g = m.get("gate") or {}
        it = g.get("integration_test") or {}
        out.append({
            "id": mid,
            "name": m.get("name") or m.get("title") or "",
            "gate_spec": (g.get("spec") or g.get("description") or "").strip(),
            "gate_test": it.get("file", ""),
            "tasks": tasks,
        })
    return out


def _top_constants(tree: ast.Module) -> list:
    """Module-level UPPER_SNAKE assignments = the calibrated goldens/thresholds a frozen test
    pins. Value shown literal-eval'd, else source-unparsed (truncated), else the bare name."""
    consts = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        names = [t.id for t in node.targets
                 if isinstance(t, ast.Name) and t.id.isupper() and len(t.id) > 1]
        if not names:
            continue
        try:
            val = repr(ast.literal_eval(node.value))
        except Exception:
            try:
                val = ast.unparse(node.value)
            except Exception:
                val = "…"
        if len(val) > 120:
            val = val[:117] + "…"
        for n in names:
            consts.append(f"{n} = {val}")
    return consts


def frozen_claims(code: Path) -> list:
    """Walk code/tests/ for the frozen suite: [(relpath, docline, [const strings])]. The
    module docstring's first line states each test's intent; the UPPER_SNAKE constants are the
    quantitative contract the build had to satisfy. This is the calibrated INTENT — a far better
    Methods source than the code that happens to satisfy it."""
    tests = code / "tests"
    if not tests.is_dir():
        return []
    out = []
    for p in sorted(tests.rglob("*.py")):
        if p.name == "__init__.py":
            continue
        try:
            tree = ast.parse(p.read_text())
        except SyntaxError:
            continue
        doc = ast.get_docstring(tree) or ""
        docline = doc.strip().splitlines()[0].strip() if doc.strip() else ""
        out.append((str(p.relative_to(tests)), docline, _top_constants(tree)))
    return out


def _emit_design(sections: list) -> list:
    lines = []
    methods = [(h, b) for h, b in sections if _is_methods_heading(h)]
    if not methods:
        # No authored narrative yet — pass through whatever headings exist, or say so.
        methods = sections
    for heading, body in methods:
        lines.append(f"## {heading}")
        lines.append(body if body else "_(section present in DESIGN.md but not yet authored)_")
        lines.append("")
    if not methods:
        lines += ["## Design narrative", "_(no DESIGN.md authored yet — run `raster plan`.)_", ""]
    return lines


def build_report(project, today=None) -> str:
    today = today or _date.today()
    spec, ry = project.spec, project.ry
    meta = project.meta
    L = [
        f"# {project.name} — Methods Digest",
        "",
        f"*Generated by `raster handoff` on {today:%Y-%m-%d} for the raconteur handoff. "
        f"Source of truth: `code/designdocs/` + `code/tests/` — NOT the implementation tree. "
        f"This is structured source material; raconteur turns it into Methods prose.*",
        "",
    ]
    if project.description:
        L += ["> " + project.description, ""]

    # --- design narrative (aim / spine / domain / contracts) ---
    L += _emit_design(design_sections(project.code))

    # --- architecture & contracts (from tasks.yaml) ---
    L += ["## Architecture & contracts (from tasks.yaml)", ""]
    mods = module_contracts(spec)
    if not mods:
        L += ["_(no modules authored yet — run `raster plan`.)_", ""]
    for m in mods:
        head = f"### {m['id']} — {m['name']}" if m["name"] else f"### {m['id']}"
        L.append(head)
        if m["gate_spec"] or m["gate_test"]:
            L.append(f"*Module gate (integration test `{m['gate_test'] or '—'}`): "
                     f"{m['gate_spec'] or '—'}*")
            L.append("")
        for t in m["tasks"]:
            deliv = ", ".join(f"`{d}`" for d in t["deliverables"]) or "—"
            L.append(f"- **{t['id']} {t['title']}** — deliverable {deliv}"
                     + (f"; unit test `{t['unit_test']}`" if t["unit_test"] else ""))
            if t["spec"]:
                L.append(f"  - contract: {t['spec']}")
            if t["checkpoint"]:
                L.append(f"  - freeze→impl checkpoint: {t['checkpoint']}")
        L.append("")

    # --- frozen quantitative contract (from code/tests/) ---
    L += ["## Quantitative contract — the frozen suite (from `code/tests/`)", "",
          "The calibrated, reviewed claims every build had to satisfy. Each test's intent is its "
          "module docstring; the constants are the golden values/thresholds it pins.", ""]
    claims = frozen_claims(project.code)
    if not claims:
        L += ["_(no frozen tests authored yet.)_", ""]
    for rel, docline, consts in claims:
        L.append(f"- **`{rel}`**" + (f" — {docline}" if docline else ""))
        for c in consts:
            L.append(f"  - `{c}`")
    L.append("")

    # --- build provenance ---
    L += ["## Build provenance", ""]
    lang = meta.get("language") or ry.get("language") or "python"
    workers = meta.get("workers", {}) or {}
    model_bits = ", ".join(f"{k}=`{v}`" for k, v in workers.items()) or "—"
    L += [
        f"- Package `{project.package}` ({lang}); built offline by local models via the "
        f"raster doer pipeline (worker→strong escalation ladder).",
        f"- Models: {model_bits}.",
    ]
    if project.trundlr_project_id():
        L.append(f"- Orchestrated as trundlr project `{project.trundlr_project_id()}`.")
    brief = (ry.get("brief") or "").strip()
    if brief:
        L += ["", "### Original brief (from `raster init`)", "", "> " + brief.replace("\n", "\n> ")]
    L.append("")
    return "\n".join(L).rstrip() + "\n"


def default_report_path(project, today=None) -> Path:
    """The build stage's output dir (`code/output/`), revision-chain name
    `{YYMMDD}_{slug}_methods_ra.md` — the stage-contract home every other ra*
    deliverable already uses. (raconteur still finds legacy root copies.)"""
    today = today or _date.today()
    slug = slugify(project.name)
    return project.code / "output" / f"{today:%y%m%d}_{slug}_methods_ra.md"


def run_report(args) -> int:
    project = load_project(getattr(args, "dir", None))
    out = Path(args.out).resolve() if getattr(args, "out", None) else default_report_path(project)
    text = build_report(project)
    if getattr(args, "dry_run", False):
        print(text)
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)   # code/output/ predates only haarpi-scaffolded projects
    out.write_text(text)
    log(f"report: wrote Methods Digest -> {out}")
    log(f"  {len(text)} chars; hand to raconteur (it reads the newest ra* methods digest).")
    return 0

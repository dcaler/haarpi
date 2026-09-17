"""Design-docs machinery shared by the two halves of the experiment workflow.

`ramus init` writes the analytical framework BEFORE raster builds anything; `rayleigh plan`
writes the executable experiments AFTER. They are separate agents with separate configs and
separate templates, but they need the same four things: a way to guess a project's name, to
find the package raster built, to index what the earlier tools left on disk, and to fall back
to a brief when no one has stated one this cycle.

Those lived in `rayleigh/init.py` and `plan.py` imported seven of them across the seam. That
import is what made the split look expensive, and it was the wrong direction anyway: ramus
runs FIRST, so it must not import rayleigh. They belong here, in the package both depend on.

`discover_priors` is the reason this is HAARPi-level rather than either tool's. What it
indexes — the minted litReview, the project brief, a prior cycle's REVIEW.md, the paper
drafts — is the whole pipeline's output, not one stage's.
"""
from __future__ import annotations

import fnmatch
import os
import re
from importlib.resources import files
from pathlib import Path

import yaml

# Heavy dirs a recursive prior-artifact scan must never descend into — a virtualenv or
# .git sitting inside code/ is tens of thousands of files, and a plain root.glob("**") walks
# every one of them, stalling the session for many seconds. os.walk lets us prune them by name.
PRUNE_DIRS = {
    ".venv", "venv", "env", ".git", "__pycache__", "node_modules", "site-packages",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".ipynb_checkpoints",
    ".eggs", "build", "dist",
}


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower()) or "package"


def project_name_from_dir(dirname: str) -> str:
    """Guess a project name the way the ra* family does: strip a leading {YYMMDD}_ (or
    {YYYYMMDD}_) datestamp prefix. e.g. '260623_rayleigh' -> 'rayleigh'."""
    return re.sub(r"^\d{6}(?:\d\d)?_", "", dirname) or dirname


def detect_package(code_dir: Path, fallback: str) -> str:
    """Find the import package under code/: a child dir with an __init__.py. Prefer one
    matching the slug fallback; else the first; else the fallback slug."""
    if not code_dir.is_dir():
        return fallback
    pkgs = sorted(p.name for p in code_dir.iterdir()
                  if p.is_dir() and (p / "__init__.py").is_file()
                  and not p.name.startswith((".", "_")) and p.name != "tests")
    if fallback in pkgs:
        return fallback
    return pkgs[0] if pkgs else fallback


def render(package: str, template_name: str, ctx: dict) -> str:
    """Fill a `{{key}}` template from `<package>/templates/`.

    Takes the package by name because ramus and rayleigh each ship their own templates —
    the front half writes EXPERIMENTS.md and PLANNING.md, the back half writes
    experiments.yaml and everything prefixed `results_`.
    """
    text = (files(package) / "templates" / template_name).read_text()
    for key, val in ctx.items():
        text = text.replace("{{" + key + "}}", str(val))
    return text


def _iter_matches(root: Path, pattern: str):
    """Yield files under `root` matching a glob `pattern`, pruning PRUNE_DIRS on the way.

    For non-recursive patterns this is just Path.glob. For a recursive `PREFIX/**/SUFFIX`
    pattern (SUFFIX a single filename glob, e.g. `code/**/CLAUDE.md`), walk PREFIX with
    os.walk so heavy directories are skipped instead of crawled."""
    if "/**/" not in pattern:
        yield from (p for p in root.glob(pattern) if p.is_file())
        return
    prefix, suffix = pattern.split("/**/", 1)
    base = root / prefix
    if not base.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in PRUNE_DIRS]
        for name in fnmatch.filter(filenames, suffix):
            yield Path(dirpath) / name


def discover_priors(root: Path, sources):
    """Find prior ra* artifacts. Returns [(group, [(label, [relpaths]), ...]), ...],
    groups with no matches omitted.

    `sources` is required rather than defaulted: ramus and rayleigh index different things
    (the framework session reads the minted litReview; the experiments session reads the
    prereg and the built code), and a default here would quietly give one of them the
    other's view.
    """
    out = []
    for group, patterns in sources:
        items = []
        for pattern, label in patterns:
            matches = sorted(str(p.relative_to(root)) for p in _iter_matches(root, pattern)
                             if p.is_file())
            if matches:
                items.append((label, matches))
        if items:
            out.append((group, items))
    return out


def derive_brief(root: Path) -> str:
    """Fall back to the HAARPi manifest brief (answered once at `haarpi init`),
    then the raster build brief/description — the closest statements of research
    intent already on disk."""
    from haarpi.project import header_defaults
    hdr_brief = (header_defaults(root).get("brief") or "").strip()
    if hdr_brief:
        return hdr_brief
    ry = root / "code" / "raster.yaml"
    if not ry.is_file():
        return ""
    try:
        d = yaml.safe_load(ry.read_text()) or {}
    except Exception:  # noqa: BLE001
        return ""
    for k in ("brief", "description"):
        v = d.get(k)
        if isinstance(v, str) and v.strip() and "not provided" not in v \
                and "to be generated" not in v:
            return v.strip()
    return ""


def render_priors_md(root: Path, priors, project: str, cycle: str, *,
                     written_by: str = "ramus init") -> str:
    """The PRIORS.md index the session reads first."""
    L = [f"# {project} — Prior artifacts (cycle {cycle})", "",
         f"*Index written by `{written_by}`. Read these — above all the minted litReview — and",
         "PROPOSE targeted research questions + an analytical approach from them (see PLANNING.md),",
         "rather than starting from a blank skeleton.*", ""]
    if not priors:
        L.append("_No prior artifacts found — design from the brief alone._")
        return "\n".join(L) + "\n"
    for group, items in priors:
        L.append(f"## {group}")
        for label, matches in items:
            shown = matches[:6]
            more = f"  (+{len(matches) - 6} more)" if len(matches) > 6 else ""
            if len(shown) == 1:
                L.append(f"- **{label}** — `{shown[0]}`")
            else:
                L.append(f"- **{label}** — {', '.join(f'`{m}`' for m in shown)}{more}")
        L.append("")
    return "\n".join(L) + "\n"

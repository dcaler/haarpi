"""The project manifest (haarpi.yaml), stage contracts, and the planner ledger.

A HAARPi project root holds exactly one committed-to-nothing `haarpi.yaml`
(identity + stage contracts) and a `.haarpi/` state dir (the planner ledger +
unified runlog). Each stage owns a subdirectory; its human-facing documents
live in `<stage>/output/` under the revision naming chain, and its gate-passed
consolidations are RELEASES (bare-chain names — see haarpi.naming).

Stage contracts implement the agreed edge rule: presence of a RELEASE unlocks,
the latest release binds, a newer release than the one a consumer recorded
means stale. Unattended consumers bind only releases; attended ones (design
sessions) may bind in-flight work, recorded as ungated provenance.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

MANIFEST = "haarpi.yaml"
STATE_DIR = ".haarpi"

# The default pipeline. `inputs` are soft edges (presence-of-release unlocks);
# `infix` names the deliverable in release filenames; `attended` marks stages
# whose opening move is an interactive design session rather than a queued run.
DEFAULT_STAGES: dict[str, dict] = {
    "litreview": {
        "dir": "litReview", "tool": "rabbithole", "inputs": [],
        "infix": "litreview", "attended": False,
    },
    "build": {
        "dir": "code", "tool": "raster", "inputs": ["litreview"],
        "infix": "methods", "attended": True,       # opens with `raster plan`
    },
    "experiments": {
        "dir": "results", "tool": "rayleigh", "inputs": ["build", "litreview"],
        "infix": "results", "attended": True,       # opens with `rayleigh init`
    },
    "paper": {
        "dir": "paper", "tool": "raconteur",
        "inputs": ["litreview", "build", "experiments"],
        "infix": "", "attended": False,
    },
}


@dataclass
class Manifest:
    name: str = ""
    short_title: str = ""
    brief: str = ""
    initials: str = "DCR"              # the human reviewer's chain suffix
    trundlr_project_id: int | None = None
    stages: dict = field(default_factory=lambda: {k: dict(v) for k, v in DEFAULT_STAGES.items()})

    def stage_dir(self, root: Path, stage: str) -> Path:
        return root / self.stages[stage]["dir"]

    def output_dir(self, root: Path, stage: str) -> Path:
        return self.stage_dir(root, stage) / "output"


def find_root(start: Path | None = None) -> Path | None:
    """Walk up from `start` (default cwd) to the directory holding haarpi.yaml."""
    p = (start or Path.cwd()).resolve()
    for d in (p, *p.parents):
        if (d / MANIFEST).is_file():
            return d
    return None


def load_manifest(root: Path) -> Manifest:
    raw = yaml.safe_load((root / MANIFEST).read_text(encoding="utf-8")) or {}
    stages = {k: dict(v) for k, v in DEFAULT_STAGES.items()}
    for k, v in (raw.pop("stages", {}) or {}).items():
        stages.setdefault(k, {}).update(v or {})
    m = Manifest(**{k: v for k, v in raw.items() if k in Manifest.__dataclass_fields__})
    m.stages = stages
    return m


def save_manifest(m: Manifest, root: Path) -> Path:
    data = {"name": m.name, "short_title": m.short_title, "brief": m.brief,
            "initials": m.initials, "trundlr_project_id": m.trundlr_project_id,
            "stages": m.stages}
    fp = root / MANIFEST
    fp.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
                  encoding="utf-8")
    return fp


def scaffold(root: Path, m: Manifest) -> list[Path]:
    """The minimal skeleton: stage output dirs + the state dir. Deep scaffolding
    (designdocs, work trees, git repos) stays with each stage tool's init."""
    made = []
    for stage in m.stages:
        out = m.output_dir(root, stage)
        out.mkdir(parents=True, exist_ok=True)
        made.append(out)
    for sub in ("plans", "runlog"):
        d = root / STATE_DIR / sub
        d.mkdir(parents=True, exist_ok=True)
        made.append(d)
    return made


def seed_tool_configs(root: Path, m: Manifest) -> list[str]:
    """Materialise the UNATTENDED stages' tool configs from the one interview.

    Attended stages (raster, rayleigh) init inside their own design sessions,
    but nothing ever attends litreview or paper — and their tools hard-require
    a project file before the first queued task can run. rabbithole's gather
    extracts topic/focus from the research prompt itself, and raconteur's
    onepager extracts title/topic/focus from the description, so the manifest
    brief is a sufficient seed for both. Existing files are never touched;
    re-running `haarpi init` repairs a project that predates this seeding."""
    seeded = []

    from rabbithole import config as rh
    lit_dir = root / m.stages["litreview"]["dir"]
    if rh.latest_project_file(root) is None:
        cfg = rh.ProjectConfig(project_name=m.name, research_prompt=m.brief,
                               trundlr_project_id=m.trundlr_project_id)
        lit_dir.mkdir(parents=True, exist_ok=True)
        fp = rh.save_project_to(cfg, lit_dir / rh.PROJECT_FILE)
        seeded.append(str(fp.relative_to(root)))

    from raconteur.config import ProjectConfig as RaconteurConfig
    if not RaconteurConfig.exists(root):
        rcfg = RaconteurConfig(
            short_title=m.short_title,
            description=m.brief,
            litrev_dir=m.stages["litreview"]["dir"],
            use_methods=True,
            results_dir=m.stages["experiments"]["dir"],
        )
        rcfg.save(root)
        seeded.append(str((root / "paper" / "raconteur.yaml").relative_to(root)))

    return seeded


# ── releases and in-flight work ──────────────────────────────────────────────

def latest_release(root: Path, m: Manifest, stage: str) -> Path | None:
    """The stage's consumable. Searches output/, then the stage dir, then the
    project root (raster's methods digest historically lands at the root)."""
    from . import naming
    infix = m.stages[stage].get("infix") or None
    for d in (m.output_dir(root, stage), m.stage_dir(root, stage), root):
        if not d.is_dir():
            continue
        for ext in ("docx", "md"):
            got = naming.find_latest_release(d, m.short_title, ext=ext,
                                             chain_includes=infix)
            if got:
                return got
    return None


def in_flight(root: Path, m: Manifest, stage: str) -> Path | None:
    """Newest chain file still carrying author tokens — the work in play."""
    from . import naming
    d = m.output_dir(root, stage)
    if not d.is_dir():
        return None
    candidates = []
    for p in list(d.glob("*.docx")) + list(d.glob("*.md")):
        parsed = naming.parse(p, m.short_title)
        if parsed and not naming.is_release(parsed[1]):
            candidates.append(p)
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


def unlocked(root: Path, m: Manifest, stage: str) -> bool:
    """Presence rule: every input stage has at least one release."""
    return all(latest_release(root, m, s) is not None
               for s in m.stages[stage].get("inputs", []))


# ── the planner ledger ───────────────────────────────────────────────────────

def ledger_dir(root: Path) -> Path:
    return root / STATE_DIR / "plans"


def annotation_hash(unresolved: list[dict], reviewer_changes: int) -> str:
    """The loop guard: one annotation set must never be planned twice."""
    blob = json.dumps({"u": unresolved, "rc": reviewer_changes}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def record_plan(root: Path, entry: dict) -> Path:
    d = ledger_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    n = len(list(d.glob("*.yaml"))) + 1
    entry = {"seq": n, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), **entry}
    fp = d / f"{n:04d}_{entry.get('stage', 'pipeline')}.yaml"
    fp.write_text(yaml.safe_dump(entry, sort_keys=False, allow_unicode=True),
                  encoding="utf-8")
    return fp


def list_plans(root: Path) -> list[dict]:
    d = ledger_dir(root)
    if not d.is_dir():
        return []
    return [yaml.safe_load(fp.read_text(encoding="utf-8"))
            for fp in sorted(d.glob("*.yaml"))]


def already_planned(root: Path, ahash: str) -> bool:
    return any(e.get("annotation_hash") == ahash for e in list_plans(root))


def stage_bindings(root: Path, stage: str) -> dict:
    """Latest recorded input bindings for a stage's outputs (for staleness)."""
    for e in reversed(list_plans(root)):
        if e.get("stage") == stage and e.get("bindings"):
            return e["bindings"]
    return {}


def stale_inputs(root: Path, m: Manifest, stage: str) -> list[str]:
    """Inputs whose current release is newer than what this stage last bound."""
    bound = stage_bindings(root, stage)
    out = []
    for s in m.stages[stage].get("inputs", []):
        rel = latest_release(root, m, s)
        if rel is None:
            continue
        if bound.get(s) and bound[s] != rel.name:
            out.append(s)
    return out


def header_defaults(start: Path | None = None) -> dict:
    """The identity a stage tool's init should not re-ask: answered once at
    `haarpi init`, read from the manifest found at or above `start`.
    Returns {} outside a HAARPi project — tools stay fully standalone."""
    root = find_root(start)
    if root is None:
        return {}
    m = load_manifest(root)
    return {"name": m.name, "short_title": m.short_title, "brief": m.brief,
            "initials": m.initials, "root": root}

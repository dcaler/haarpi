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
    initials: str = "DCR"              # the chain suffix of whoever drives the pipeline
    # Who the paper is BY. Project-level, not paper-level: a co-author who comes aboard
    # after the one-pager is circulated may trigger a re-think that regenerates the
    # litreview, the build, the experiments and every downstream document — the one fact
    # that must survive all of it. Held here rather than in prose because a name and an
    # affiliation are facts the tools must never invent, and because a venue's `anonymized`
    # flag can only strip an author block that exists as data.
    # Each entry: {name, initials?, affiliation?, email?, orcid?}.
    authors: list = field(default_factory=list)
    trundlr_project_id: int | None = None
    trundlr_priority: int = 3          # trundlr's own default band (1 highest .. 4 lowest)
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
    # Enumerated, not asdict() — a new field that is not listed here loads correctly and
    # then silently fails to persist, which looks like the edit never happened.
    data = {"name": m.name, "short_title": m.short_title, "brief": m.brief,
            "initials": m.initials, "authors": m.authors,
            "trundlr_project_id": m.trundlr_project_id,
            "trundlr_priority": m.trundlr_priority, "stages": m.stages}
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

# Spent or reference material — never live work, never a stage's consumable. `@eaDir` is
# Synology's metadata sidecar: thousands of files, none of them ours.
_ARCHIVE_DIRS = {"old", "archive", "templates", "figures", "@eaDir", ".git",
                 "__pycache__", ".venv", "venv", "node_modules", "data"}


def live_dirs(base: Path, skip: set[str] = _ARCHIVE_DIRS) -> list[Path]:
    """`base` and every live directory beneath it, PRUNING as it walks.

    Not rglob-then-filter: rglob enumerates the whole tree first, so a paper/ holding a
    LaTeX template package (and its Synology @eaDir sidecars) took long enough to look like
    a hang. Pruning never descends into what it is going to discard.
    """
    out, stack = [], [base]
    while stack:
        d = stack.pop()
        out.append(d)
        try:
            for c in d.iterdir():
                if c.is_dir() and c.name not in skip:
                    stack.append(c)
        except OSError:
            continue
    return out


def stage_search_dirs(root: Path, m: Manifest, stage: str) -> list[Path]:
    """Every live directory under a stage, deepest layout included.

    raconteur gives each paper deliverable its own folder (paper/onepager/,
    paper/css2026/outline/, …), so looking only at the stage root and its output/ misses
    the work entirely — and a stage that reports no release when it has one stalls the
    ladder silently."""
    base = m.stage_dir(root, stage)
    if not base.is_dir():
        return []
    # Only the paper stage nests: raconteur gives each deliverable a folder. `code/` and
    # `results/` are a source repo and a data tree — walking them took long enough to look
    # like a hang, and there is nothing down there a naming chain would match anyway.
    if m.stages[stage].get("tool") != "raconteur":
        return [d for d in (base, base / "output") if d.is_dir()]
    out = live_dirs(base)
    seen, uniq = set(), []
    for d in out:
        if d not in seen and d.is_dir():
            seen.add(d)
            uniq.append(d)
    return uniq


def latest_release(root: Path, m: Manifest, stage: str) -> Path | None:
    """The stage's consumable. Searches every live directory under the stage, then the
    project root (raster's methods digest historically lands at the root)."""
    from . import naming
    infix = m.stages[stage].get("infix") or None
    best: tuple[float, Path] | None = None
    for d in stage_search_dirs(root, m, stage):
        for ext in ("docx", "md"):
            got = naming.find_latest_release(d, m.short_title, ext=ext,
                                             chain_includes=infix)
            if got and (best is None or got.stat().st_mtime > best[0]):
                best = (got.stat().st_mtime, got)
    if best:
        return best[1]
    for d in (root,):
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
    candidates = []
    for d in stage_search_dirs(root, m, stage):
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


def annotation_hash(unresolved: list[dict], reviewer_changes: int,
                    markup: str = "") -> str:
    """The loop guard: one markup's annotation set must never be planned twice.

    Keyed on the markup's identity as well as its asks. A clean approval carries
    NO annotations, so hashing the asks alone makes every clean gate on the
    ladder collide on the empty set — the first approval poisons every later one,
    and the next clean rung (a venue slate, an approved outline) wedges on a false
    "already planned". The file a gate was passed on is what tells two clean
    approvals apart; re-firing on the same file still reproduces its hash, so the
    genuine double-plan the guard exists for is still caught."""
    blob = json.dumps({"m": markup, "u": unresolved, "rc": reviewer_changes},
                      sort_keys=True)
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


AUTHOR_FIELDS = ("name", "initials", "affiliations", "email", "orcid", "corresponding")

# Written as `affiliations`, read from either. A joint appointment is ordinary — and an
# author who moves institution mid-project may need both for the duration.
_AFFIL_KEYS = ("affiliations", "affiliation")


def author_affiliations(entry: dict) -> list[str]:
    """An author's affiliations, in the order they should be printed.

    Accepts a list, a single string, or the older singular ``affiliation`` key, so a
    manifest written before multiple affiliations existed still loads. Duplicates are
    dropped: the same institution twice on one author is a typo, not a joint appointment.
    """
    raw = next((entry[k] for k in _AFFIL_KEYS if entry.get(k) not in (None, "", [])), None)
    if raw is None:
        return []
    items = [raw] if isinstance(raw, str) else list(raw)
    out: list[str] = []
    for a in items:
        s = str(a).strip()
        if s and s not in out:
            out.append(s)
    return out


def normalize_author(entry: dict | str) -> dict:
    """One author record, from a dict or a bare name. Unknown keys are dropped."""
    if isinstance(entry, str):
        return {"name": entry.strip()}
    out = {k: str(entry[k]).strip() for k in AUTHOR_FIELDS
           if k not in ("corresponding", "affiliations") and entry.get(k) not in (None, "")}
    affils = author_affiliations(entry)
    if affils:
        out["affiliations"] = affils
    if entry.get("corresponding"):
        out["corresponding"] = True
    # Key order follows AUTHOR_FIELDS so the manifest reads the same way every save.
    return {k: out[k] for k in AUTHOR_FIELDS if k in out}


def authors(m: Manifest) -> list[dict]:
    """The author list, normalized and in order. Order is authorship order — it is the
    author's to set and no tool's to sort."""
    return [normalize_author(a) for a in (m.authors or []) if a]


def corresponding_authors(m: Manifest) -> list[dict]:
    return [a for a in authors(m) if a.get("corresponding")]


def authors_block(m: Manifest, anonymized: bool = False) -> str:
    """The authors-and-affiliations block that sits under the title.

    ``anonymized`` returns the empty string: a double-blind venue's submission must not
    carry identity, and that is decided by the venue's flag rather than by whoever is
    drafting. This is the whole reason the author list is data — prose cannot be stripped
    on a venue's say-so.

    An email appears only for a corresponding author; it is a contact address, not a
    credential every co-author wants printed. One corresponding author is "Corresponding
    author"; several are "Co-corresponding authors", which is the convention and also the
    honest description — "Corresponding author: A, B" reads as one person with two names.
    """
    people = authors(m)
    if anonymized or not people:
        return ""
    lines = [", ".join(a["name"] for a in people)]
    # Distinct affiliations, numbered in the order the author list first mentions them —
    # the convention, and stable across edits that do not change the ordering.
    affils: list[str] = []
    for a in people:
        for aff in a.get("affiliations", []):
            if aff not in affils:
                affils.append(aff)
    if affils:
        # Markers only earn their keep once there is something to disambiguate: one
        # affiliation shared by everyone reads better unmarked. An author with a joint
        # appointment carries several markers ("A. One^1,2^").
        shared_single = len(affils) == 1 and all(
            a.get("affiliations") == affils for a in people)
        if shared_single:
            lines.append(affils[0])
        else:
            lines[0] = ", ".join(
                f"{a['name']}^{','.join(str(affils.index(x) + 1) for x in a['affiliations'])}^"
                if a.get("affiliations") else a["name"] for a in people)
            lines += [f"^{i + 1}^ {aff}" for i, aff in enumerate(affils)]
    corr = corresponding_authors(m)
    if corr:
        label = "Corresponding author" if len(corr) == 1 else "Co-corresponding authors"
        contacts = ", ".join(
            f"{a['name']} ({a['email']})" if a.get("email") else a["name"] for a in corr)
        lines.append(f"{label}: {contacts}")
    return "\n\n".join(lines)


def reviewer_initials(m: Manifest) -> list[str]:
    """Every human who may annotate: the authors' initials, plus the pipeline driver's.

    Not used to decide whose turn it is — ``find_finished_markup`` asks only whether the
    last token is the tool's, so an unlisted collaborator's markup is still seen. This is
    for reporting and for catching a chain token nobody recognizes.
    """
    out = [a["initials"] for a in authors(m) if a.get("initials")]
    if m.initials and m.initials not in out:
        out.append(m.initials)
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

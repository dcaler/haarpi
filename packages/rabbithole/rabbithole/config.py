"""Configuration: per-project litrev.yaml + machine-level global config + secrets.

Two layers:

  * Project config  ./litrev.yaml          (created by `rabbitHole init`, per topic)
  * Global config   ~/.config/haarpi/config.toml  (unified; the legacy
    ~/.config/rabbithole/config.toml is still honored underneath it)

Environment variables always override the global config file:
  OLLAMA_URL, RABBITHOLE_CONTACT_EMAIL,
  ZOTERO_API_KEY, ZOTERO_LIBRARY_ID, ZOTERO_LIBRARY_TYPE,
  ANTHROPIC_API_KEY, S2_API_KEY
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

import yaml

from haarpi import config as haarpi_config

PROJECT_FILE = "litrev.yaml"          # first/active project config (version 1)
PROJECT_STEM = "litrev"               # later iterations: litrev_2.yaml, litrev_3.yaml, ...
_PROJECT_RE = re.compile(r"^litrev(?:_(\d+))?\.yaml$")
LITREVIEW_DIR = "litReview"           # the DEFAULT review's folder — see REVIEW_KINDS
GLOBAL_CONFIG_PATH = haarpi_config.legacy_path("rabbithole")


# ── review kinds ──────────────────────────────────────────────────────────
# A project can hold more than one literature review, because one anchor cannot serve
# two questions. The substantive review is anchored on the DOMAIN; a methods review is
# anchored on the methodological families and usually EXCLUDES that domain. FirmPathways
# is the worked case: five rounds of steering could not make a review anchored on
# "public innovation policy" return sequence-analysis methodology, because the
# methodology lives in life-course sociology and demography — outside the anchor and
# adjacent to an excluded field.
#
# This registry is the ONE place that knows a kind's folder, config stem and deliverable
# infix. The infix matters: `methods` is already the BUILD stage's infix (raster's
# implementation writeup), and raconteur's find_methods_file() searches the project root
# for it, so a methods REVIEW minted as `..._methods_ra.docx` would be read as raster's
# writeup. Hence `methodsreview`.
@dataclass(frozen=True)
class ReviewKind:
    name: str       # role recorded in the corpus ledger
    dir: str        # folder under the project root
    stem: str       # config filename stem: <stem>.yaml, <stem>_2.yaml, …
    infix: str      # deliverable infix in the naming chain


REVIEW_KINDS: dict[str, ReviewKind] = {
    "literature": ReviewKind("literature", LITREVIEW_DIR, "litrev", "litreview"),
    "methods": ReviewKind("methods", "litReviewMethods", "methodsreview", "methodsreview"),
}
DEFAULT_KIND = "literature"


def _kind_re(stem: str) -> re.Pattern:
    return re.compile(rf"^{re.escape(stem)}(?:_(\d+))?\.yaml$")


def kind_of(path: str | Path = ".") -> ReviewKind:
    """Which review a directory IS, by what it holds and then by what it is called.

    Contents win over the folder name: a directory holding `methodsreview.yaml` is a
    methods review whatever it is called, which keeps the name a convention rather than
    a requirement. Falls back to the default so every existing project is unaffected.
    """
    p = Path(path)
    d = p.parent if p.is_file() else p
    for kind in REVIEW_KINDS.values():
        if any(_kind_re(kind.stem).match(fp.name) for fp in d.glob(f"{kind.stem}*.yaml")):
            return kind
    for kind in REVIEW_KINDS.values():
        if d.name == kind.dir:
            return kind
    return REVIEW_KINDS[DEFAULT_KIND]

# Default model assignments — change to match what you have in Ollama.
DEFAULT_COORDINATOR_MODEL = "qwen3.6:27b-16k"
DEFAULT_WORKER_MODEL = "llama3.1:8b"
DEFAULT_EMBED_MODEL = "mxbai-embed-large"
DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-6"
DEFAULT_OLLAMA_URL = "http://localhost:11434"


# ──────────────────────────────────────────────────────────────────────────
# Project config
# ──────────────────────────────────────────────────────────────────────────
@dataclass
class BrainConfig:
    backend: str = "ollama"            # "ollama" | "claude"
    coordinator_model: str = DEFAULT_COORDINATOR_MODEL
    worker_model: str = DEFAULT_WORKER_MODEL
    embed_model: str = DEFAULT_EMBED_MODEL
    claude_model: str = DEFAULT_CLAUDE_MODEL
    worker_parallel: int = 1           # serial: concurrency is sub-1x on Maxwell (Tesla M60)
                                       # — a model split across cards or batched gives no
                                       # speedup, so run worker calls back-to-back instead.
    critique_rounds: int = 2           # synthesis critique→revise rounds (lint + peer
                                       # review per round, early-exit when both pass).


@dataclass
class ProjectConfig:
    project_name: str = "untitled"
    topic: str = ""
    focus: str = ""
    # THIS cycle's specific asks, replaced (never appended to) each time the planner writes a
    # new numbered config. `focus` is the review's STANDING scope and is fed whole to query
    # generation, which returns a fixed 8-10 queries however long it grows — so an ask parked
    # in `focus` competes with every earlier cycle's ask for a slot, and the newest ones lose.
    # elephantRoom's fourth gather searched a 900-character focus and returned nothing at all
    # on the two topics that cycle was FOR. Topics here get their own guaranteed queries and
    # their own per-topic yield report, so a gather that finds nothing for an ask says so.
    gather_topics: list = field(default_factory=list)
    target_min: int = 20
    target_max: int = 50
    date_from: int | None = None
    date_to: int | None = None
    # Domain steering (set by the wizard; feed query-gen and the relevance gate).
    domain_anchor: str = ""             # one line: what a paper MUST be about to count
    exclude_topics: str = ""            # one line: adjacent disciplines to keep OUT
    # Source-type policy (the wizard's 4-way question -> two flags).
    include_preprints: bool = False     # arXiv / working papers
    include_news: bool = False          # news / trade press
    sources: dict = field(default_factory=lambda: {
        "openalex": True, "crossref": True,
        "semantic_scholar": True, "arxiv": True,
    })
    ranking: dict = field(default_factory=lambda: {
        "method": "llm",                # "embedding" | "citations" | "llm"
        "rerank_top_n": 0,              # 0 = re-rank a sensible default-sized head
        "min_score": 6.0,              # LLM relevance floor (0-10); drops off-domain hits
        "max_arxiv_fraction": 0.25,     # cap on arXiv/preprint share of final list
    })
    brain: BrainConfig = field(default_factory=BrainConfig)
    zotero: dict = field(default_factory=lambda: {"collection_key": ""})
    # Raw research prompt from init — topic/focus are extracted from this by gather.
    research_prompt: str = ""
    # MDPI is always excluded in code; extra publisher names to drop go here.
    exclude_publishers: list = field(default_factory=list)
    # Style emulation — uses the shared, neutral ~/.config/haarpi/style_profile.md.
    use_style: bool = False
    style_author: str = ""
    style_paper_keys: list = field(default_factory=list)  # Zotero item keys confirmed in init
    # trundlr task-queue binding (resolved by `haarpi next`; matched by project_name).
    trundlr_project_id: int | None = None

    def to_yaml(self) -> str:
        d = asdict(self)
        return yaml.safe_dump(d, sort_keys=False, allow_unicode=True)


# ── versioned project files: litrev.yaml (1), litrev_2.yaml, litrev_3.yaml ──
def _project_number(name: str) -> int | None:
    m = _PROJECT_RE.match(name)
    if not m:
        return None
    return int(m.group(1)) if m.group(1) else 1


def _is_review_root(p: Path) -> bool:
    """Does this directory hold a review's config, or carry a review's folder name?

    Either mark makes it the root. Without this, `work_root` appended `litReview` to
    anything not literally named `litReview` — so pointing a verb at a methods review
    resolved to `litReviewMethods/litReview/`, and its config was never found.
    """
    # next(...) not any(p.glob(...)): a glob GENERATOR is truthy even when it yields
    # nothing, which would make every directory look like a review root.
    if any(next(p.glob(f"{k.stem}*.yaml"), None) for k in REVIEW_KINDS.values()):
        return True
    return any(p.name == k.dir for k in REVIEW_KINDS.values())


def work_root(path: str | Path = ".") -> Path:
    """The review folder inside a project directory. Idempotent once inside one.

    A path that already IS a review root is returned untouched; anything else gets the
    default review's folder appended, which is what every single-review project has
    always done.
    """
    p = Path(path)
    return p if _is_review_root(p) else p / LITREVIEW_DIR


def _project_dir(path: str | Path) -> Path:
    """Directory that holds this review's config files."""
    p = Path(path)
    return p.parent if p.is_file() else work_root(p)


def list_project_files(path: str | Path = ".") -> list[Path]:
    """All of THIS review's config files in the dir, ascending by version number.

    Scoped to the directory's own kind: a methods review globs `methodsreview*.yaml` and
    never `litrev*.yaml`, so two reviews could sit in one folder without either reading
    the other's cycles.
    """
    d = _project_dir(path)
    stem = kind_of(d).stem
    rx = _kind_re(stem)
    numbered = []
    for fp in d.glob(f"{stem}*.yaml"):
        m = rx.match(fp.name)
        if m:
            numbered.append((int(m.group(1)) if m.group(1) else 1, fp))
    return [fp for _, fp in sorted(numbered, key=lambda t: t[0])]


def latest_project_file(path: str | Path = ".") -> Path | None:
    files = list_project_files(path)
    return files[-1] if files else None


def next_project_file(path: str | Path = ".") -> Path:
    """Path for a new iteration of THIS review: <stem>.yaml, else <stem>_<N+1>.yaml."""
    d = _project_dir(path)
    stem = kind_of(d).stem
    files = list_project_files(path)
    if not files:
        return d / f"{stem}.yaml"
    m = _kind_re(stem).match(files[-1].name)
    n = int(m.group(1)) if m and m.group(1) else 1
    return d / f"{stem}_{n + 1}.yaml"


def load_project(path: str | Path = ".") -> ProjectConfig:
    """Load the latest numbered project config from <dir>/litReview (or a given file)."""
    p = Path(path)
    if p.is_file():
        fp = p
    else:
        fp = latest_project_file(p)
        if fp is None:
            raise FileNotFoundError(
                f"No {PROJECT_FILE} found in {work_root(p)}. Run `rabbitHole init` first."
            )
    raw = yaml.safe_load(fp.read_text(encoding="utf-8")) or {}
    brain = BrainConfig(**(raw.pop("brain", {}) or {}))
    cfg = ProjectConfig(**raw)
    cfg.brain = brain
    _adopt_manifest(cfg, p if p.is_dir() else fp.parent.parent)
    return cfg


# The three facts the umbrella owns. `haarpi init` used to COPY them into a new litrev config
# and nothing ever resynced, so they drifted: on an adopted project they diverge from birth,
# because seeding deliberately never touches an existing file. DigiPros ended up with a 3371-
# character brief describing a three-model journal paper and a 646-character research_prompt
# describing the single-model conference paper — two documents about different work, and the
# audit judging against the wrong one. One owner per fact; the local copy survives only as the
# standalone fallback, since each tool stays individually usable without the umbrella.
_MANIFEST_OWNED = {"project_name": "name", "research_prompt": "brief",
                   "trundlr_project_id": "trundlr_project_id"}


def _adopt_manifest(cfg: "ProjectConfig", start: Path) -> None:
    """Overlay the umbrella-owned fields from haarpi.yaml, when a project has one."""
    try:
        from haarpi import project as _hproject
        root = _hproject.find_root(start)
        if root is None:
            return
        m = _hproject.load_manifest(root)
    except Exception:  # noqa: BLE001 — no umbrella, or an unreadable manifest: keep the copy
        return
    for ours, theirs in _MANIFEST_OWNED.items():
        v = getattr(m, theirs, None)
        if v:
            setattr(cfg, ours, v)


def save_project(cfg: ProjectConfig, path: str | Path = ".") -> Path:
    """Write back to the latest existing project file in-place (or litrev.yaml if
    none yet). Used to update a project, e.g. gather saving collection_key. To
    start a new numbered iteration, use next_project_file() + save_project_to()."""
    p = Path(path)
    if p.is_file():
        fp = p
    else:
        fp = latest_project_file(p) or (work_root(p) / PROJECT_FILE)
    return save_project_to(cfg, fp)


def save_project_to(cfg: ProjectConfig, fp: Path) -> Path:
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(cfg.to_yaml(), encoding="utf-8")
    return fp


# ──────────────────────────────────────────────────────────────────────────
# Project directory layout
# ──────────────────────────────────────────────────────────────────────────
@dataclass
class Paths:
    root: Path

    @property
    def pdfs(self) -> Path: return self.root / "pdfs"
    @property
    def work(self) -> Path: return self.root / "work"
    @property
    def output(self) -> Path: return self.root / "output"
    @property
    def candidates_md(self) -> Path: return self.root / "candidates.md"
    @property
    def candidates_json(self) -> Path: return self.work / "candidates.json"
    @property
    def corpus_json(self) -> Path: return self.work / "corpus.json"
    @property
    def annotations_dir(self) -> Path: return self.work / "annotations"

    def ensure(self) -> "Paths":
        for d in (self.pdfs, self.work, self.output, self.annotations_dir):
            d.mkdir(parents=True, exist_ok=True)
        return self


def project_paths(path: str | Path = ".") -> Paths:
    return Paths(work_root(path).resolve())


# ──────────────────────────────────────────────────────────────────────────
# Global config + secrets
# ──────────────────────────────────────────────────────────────────────────
@dataclass
class GlobalConfig:
    ollama_url: str = DEFAULT_OLLAMA_URL
    contact_email: str = ""            # for OpenAlex/Crossref/Unpaywall polite pools
    zotero_api_key: str = ""
    zotero_library_id: str = ""
    zotero_library_type: str = "user"  # "user" | "group"
    anthropic_api_key: str = ""
    s2_api_key: str = ""               # optional Semantic Scholar key
    # trundlr task queue (`haarpi next` submits gather/collect/audit/build/revise chains here).
    trundlr_url: str = ""             # e.g. http://100.87.86.57:8251
    trundlr_runner_resource_id: int | None = None  # cpu/gpu resource the runner polls
    trundlr_human_resource_id: int | None = None   # human resource for collect/comment/init

    @property
    def have_zotero(self) -> bool:
        return bool(self.zotero_api_key and self.zotero_library_id)

    @property
    def have_trundlr(self) -> bool:
        return bool(self.trundlr_url)

    @property
    def have_anthropic(self) -> bool:
        return bool(self.anthropic_api_key)


def _legacy_normalized() -> dict:
    """rabbitHole's old ~/.config/rabbithole/config.toml, translated to the
    unified haarpi schema (top-level ollama_url becomes [ollama] url, trundlr
    resource ids drop their _id suffix)."""
    data = haarpi_config.load_toml(GLOBAL_CONFIG_PATH)
    if not data:
        return {}
    tr = data.get("trundlr", {})
    out = {k: v for k, v in data.items()
           if k in ("contact_email", "zotero", "anthropic", "semantic_scholar")}
    if "ollama_url" in data:
        out["ollama"] = {"url": data["ollama_url"]}
    if tr:
        out["trundlr"] = {"url": tr.get("url", "")}
        if tr.get("runner_resource_id") is not None:
            out["trundlr"]["runner_resource"] = tr["runner_resource_id"]
        if tr.get("human_resource_id") is not None:
            out["trundlr"]["human_resource"] = tr["human_resource_id"]
    return out


def load_global() -> GlobalConfig:
    data = haarpi_config.merged_config("rabbithole", _legacy_normalized())

    o = data.get("ollama", {})
    z = data.get("zotero", {})
    a = data.get("anthropic", {})
    s2 = data.get("semantic_scholar", {})
    tr = data.get("trundlr", {})

    gc = GlobalConfig(
        ollama_url=o.get("url", DEFAULT_OLLAMA_URL),
        contact_email=data.get("contact_email", ""),
        zotero_api_key=z.get("api_key", ""),
        zotero_library_id=str(z.get("library_id", "")),
        zotero_library_type=z.get("library_type", "user"),
        anthropic_api_key=a.get("api_key", ""),
        s2_api_key=s2.get("api_key", ""),
        trundlr_url=tr.get("url", ""),
        trundlr_runner_resource_id=tr.get("runner_resource") or None,
        trundlr_human_resource_id=tr.get("human_resource") or None,
    )

    # Env overrides.
    gc.ollama_url = os.environ.get("OLLAMA_URL", gc.ollama_url)
    gc.contact_email = os.environ.get("RABBITHOLE_CONTACT_EMAIL", gc.contact_email)
    gc.zotero_api_key = os.environ.get("ZOTERO_API_KEY", gc.zotero_api_key)
    gc.zotero_library_id = os.environ.get("ZOTERO_LIBRARY_ID", gc.zotero_library_id)
    gc.zotero_library_type = os.environ.get("ZOTERO_LIBRARY_TYPE", gc.zotero_library_type)
    gc.anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", gc.anthropic_api_key)
    gc.s2_api_key = os.environ.get("S2_API_KEY", gc.s2_api_key)
    gc.trundlr_url = os.environ.get("TRUNDLR_URL", gc.trundlr_url)
    _rid = os.environ.get("TRUNDLR_RUNNER_RESOURCE_ID")
    if _rid:
        gc.trundlr_runner_resource_id = int(_rid)
    _hid = os.environ.get("TRUNDLR_HUMAN_RESOURCE_ID")
    if _hid:
        gc.trundlr_human_resource_id = int(_hid)
    return gc

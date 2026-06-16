"""Configuration: per-project litrev.yaml + machine-level global config + secrets.

Two layers:

  * Project config  ./litrev.yaml          (created by `rabbitHole init`, per topic)
  * Global config   ~/.config/rabbithole/config.toml  (secrets + machine settings, once)

Environment variables always override the global config file:
  OLLAMA_URL, RABBITHOLE_CONTACT_EMAIL,
  ZOTERO_API_KEY, ZOTERO_LIBRARY_ID, ZOTERO_LIBRARY_TYPE,
  ANTHROPIC_API_KEY, S2_API_KEY
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field, asdict
from pathlib import Path

import yaml

PROJECT_FILE = "litrev.yaml"          # first/active project config (version 1)
PROJECT_STEM = "litrev"               # later iterations: litrev_2.yaml, litrev_3.yaml, ...
_PROJECT_RE = re.compile(r"^litrev(?:_(\d+))?\.yaml$")
LITREVIEW_DIR = "litReview"           # all rabbitHole files live under <project>/litReview/
GLOBAL_CONFIG_PATH = Path.home() / ".config" / "rabbithole" / "config.toml"

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


@dataclass
class ProjectConfig:
    project_name: str = "untitled"
    topic: str = ""
    focus: str = ""
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
    # Style emulation — uses the shared ~/.config/raconteur/style_profile.md.
    use_style: bool = False
    style_author: str = ""

    def to_yaml(self) -> str:
        d = asdict(self)
        return yaml.safe_dump(d, sort_keys=False, allow_unicode=True)


# ── versioned project files: litrev.yaml (1), litrev_2.yaml, litrev_3.yaml ──
def _project_number(name: str) -> int | None:
    m = _PROJECT_RE.match(name)
    if not m:
        return None
    return int(m.group(1)) if m.group(1) else 1


def work_root(path: str | Path = ".") -> Path:
    """The rabbitHole working subfolder (litReview/) inside a project directory.
    Idempotent if already pointed at the litReview dir."""
    p = Path(path)
    return p if p.name == LITREVIEW_DIR else p / LITREVIEW_DIR


def _project_dir(path: str | Path) -> Path:
    """Directory that holds the litrev*.yaml files (the litReview subfolder)."""
    p = Path(path)
    return p.parent if p.is_file() else work_root(p)


def list_project_files(path: str | Path = ".") -> list[Path]:
    """All litrev*.yaml in the dir, sorted ascending by version number."""
    d = _project_dir(path)
    numbered = []
    for fp in d.glob("litrev*.yaml"):
        n = _project_number(fp.name)
        if n is not None:
            numbered.append((n, fp))
    return [fp for _, fp in sorted(numbered, key=lambda t: t[0])]


def latest_project_file(path: str | Path = ".") -> Path | None:
    files = list_project_files(path)
    return files[-1] if files else None


def next_project_file(path: str | Path = ".") -> Path:
    """Path for a new iteration: litrev.yaml if none yet, else litrev_<N+1>.yaml."""
    d = _project_dir(path)
    files = list_project_files(path)
    if not files:
        return d / PROJECT_FILE
    n = _project_number(files[-1].name) or 1
    return d / f"{PROJECT_STEM}_{n + 1}.yaml"


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
    return cfg


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
    # Optional email notifications — sent via the local mail program (the same one
    # SLURM uses on HPC systems), so no SMTP credentials are needed.
    notify_to: str = ""               # recipient; defaults to contact_email
    mail_prog: str = ""               # override; else SLURM MailProg / `mail` is auto-detected

    @property
    def have_zotero(self) -> bool:
        return bool(self.zotero_api_key and self.zotero_library_id)

    @property
    def have_anthropic(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def notify_recipient(self) -> str:
        return self.notify_to or self.contact_email


def load_global() -> GlobalConfig:
    data: dict = {}
    if GLOBAL_CONFIG_PATH.exists():
        with open(GLOBAL_CONFIG_PATH, "rb") as fh:
            data = tomllib.load(fh)

    z = data.get("zotero", {})
    a = data.get("anthropic", {})
    s2 = data.get("semantic_scholar", {})
    nt = data.get("notify", {})

    gc = GlobalConfig(
        ollama_url=data.get("ollama_url", DEFAULT_OLLAMA_URL),
        contact_email=data.get("contact_email", ""),
        zotero_api_key=z.get("api_key", ""),
        zotero_library_id=str(z.get("library_id", "")),
        zotero_library_type=z.get("library_type", "user"),
        anthropic_api_key=a.get("api_key", ""),
        s2_api_key=s2.get("api_key", ""),
        notify_to=nt.get("to", "") or data.get("notify_to", ""),
        mail_prog=nt.get("mail_prog", ""),
    )

    # Env overrides.
    gc.ollama_url = os.environ.get("OLLAMA_URL", gc.ollama_url)
    gc.contact_email = os.environ.get("RABBITHOLE_CONTACT_EMAIL", gc.contact_email)
    gc.zotero_api_key = os.environ.get("ZOTERO_API_KEY", gc.zotero_api_key)
    gc.zotero_library_id = os.environ.get("ZOTERO_LIBRARY_ID", gc.zotero_library_id)
    gc.zotero_library_type = os.environ.get("ZOTERO_LIBRARY_TYPE", gc.zotero_library_type)
    gc.anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", gc.anthropic_api_key)
    gc.s2_api_key = os.environ.get("S2_API_KEY", gc.s2_api_key)
    gc.notify_to = os.environ.get("RABBITHOLE_NOTIFY_TO", gc.notify_to)
    gc.mail_prog = os.environ.get("RABBITHOLE_MAIL_PROG", gc.mail_prog)
    return gc

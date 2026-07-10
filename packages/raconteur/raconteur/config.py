from __future__ import annotations
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
import yaml

from haarpi import config as haarpi_config

GLOBAL_CONFIG_PATH = haarpi_config.legacy_path("raconteur")
PROJECT_CONFIG_FILE = Path("paper") / "raconteur.yaml"


@dataclass
class BrainConfig:
    coordinator_model: str = "qwen3.6:27b-16k"
    worker_model: str = "llama3.1:8b"


@dataclass
class VenueConfig:
    name: str = ""
    page_limit: int | None = None
    word_limit: int | None = None
    citation_style: str = ""
    columns: int = 1
    abstract_limit: int | None = None
    format_notes: str = ""


@dataclass
class ZoteroConfig:
    api_key: str = ""
    library_id: str = ""
    library_type: str = "user"

    @classmethod
    def from_env(cls) -> "ZoteroConfig":
        return cls(
            api_key=os.environ.get("ZOTERO_API_KEY", ""),
            library_id=os.environ.get("ZOTERO_LIBRARY_ID", ""),
            library_type=os.environ.get("ZOTERO_LIBRARY_TYPE", "user"),
        )

    @property
    def available(self) -> bool:
        return bool(self.api_key and self.library_id)

    @property
    def have_zotero(self) -> bool:
        return self.available


@dataclass
class ProjectConfig:
    short_title: str = ""
    title: str = ""
    description: str = ""
    topic: str = ""
    focus: str = ""
    litrev_dir: str = ""
    use_methods: bool = False
    results_dir: str = ""
    methods_drafted: bool = False
    results_dir_drafted: bool = False
    style_author: str = ""
    use_style: bool = False
    style_paper_keys: list = field(default_factory=list)
    venue: VenueConfig = field(default_factory=VenueConfig)
    brain: BrainConfig = field(default_factory=BrainConfig)

    def save(self, project_dir: Path) -> None:
        data = asdict(self)
        path = project_dir / PROJECT_CONFIG_FILE
        path.parent.mkdir(exist_ok=True)
        with open(path, "w") as f:
            yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    @classmethod
    def load(cls, project_dir: Path) -> "ProjectConfig":
        path = project_dir / PROJECT_CONFIG_FILE
        with open(path) as f:
            data = yaml.safe_load(f)
        data.pop("scope", None)
        data.pop("author_initials", None)
        # backward compat: raster methods moved from a code/ dir to a root file
        if "methods_dir" in data:
            data["use_methods"] = bool(data.pop("methods_dir"))
        if "methods_dir_drafted" in data:
            data["methods_drafted"] = bool(data.pop("methods_dir_drafted"))
        brain_data = data.pop("brain", {})
        # backward compat: old field names
        if "coordinator" in brain_data:
            brain_data["coordinator_model"] = brain_data.pop("coordinator")
        if "worker" in brain_data:
            brain_data["worker_model"] = brain_data.pop("worker")
        venue_data = data.pop("venue", {})
        return cls(
            **data,
            brain=BrainConfig(**brain_data),
            venue=VenueConfig(**venue_data),
        )

    @classmethod
    def exists(cls, project_dir: Path) -> bool:
        return (project_dir / PROJECT_CONFIG_FILE).exists()


@dataclass
class GlobalConfig:
    ollama_url: str = "http://localhost:11434"
    coordinator_model: str = "qwen3.6:27b-16k"
    worker_model: str = "llama3.1:8b"
    notify_to: str = ""
    mail_prog: str = ""

    @property
    def notify_recipient(self) -> str:
        return self.notify_to

    @classmethod
    def load(cls) -> "GlobalConfig":
        # raconteur's legacy file already uses the unified [ollama]/[notify] shape.
        legacy = haarpi_config.load_toml(GLOBAL_CONFIG_PATH)
        data = haarpi_config.merged_config("raconteur", legacy)
        cfg = cls()
        ollama = data.get("ollama", {})
        cfg.ollama_url = ollama.get("url", cfg.ollama_url)
        cfg.coordinator_model = ollama.get("coordinator", cfg.coordinator_model)
        cfg.worker_model = ollama.get("worker", cfg.worker_model)
        notify = data.get("notify", {})
        cfg.notify_to = notify.get("to", "")
        cfg.mail_prog = notify.get("mail_prog", "")
        cfg.ollama_url = os.environ.get("OLLAMA_URL", cfg.ollama_url)
        cfg.notify_to = os.environ.get("RACONTEUR_NOTIFY_TO", cfg.notify_to)
        cfg.mail_prog = os.environ.get("RACONTEUR_MAIL_PROG", cfg.mail_prog)
        return cfg

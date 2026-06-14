from __future__ import annotations
import os
import sys
import tomllib
from dataclasses import dataclass, field, asdict
from pathlib import Path
import yaml

GLOBAL_CONFIG_PATH = Path.home() / ".config" / "raconteur" / "config.toml"
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
class ProjectConfig:
    short_title: str = ""
    title: str = ""
    description: str = ""
    topic: str = ""
    focus: str = ""
    litrev_dir: str = ""
    methods_dir: str = ""
    results_dir: str = ""
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
        cfg = cls()
        if GLOBAL_CONFIG_PATH.exists():
            try:
                with open(GLOBAL_CONFIG_PATH, "rb") as f:
                    data = tomllib.load(f)
                ollama = data.get("ollama", {})
                cfg.ollama_url = ollama.get("url", cfg.ollama_url)
                cfg.coordinator_model = ollama.get("coordinator", cfg.coordinator_model)
                cfg.worker_model = ollama.get("worker", cfg.worker_model)
                notify = data.get("notify", {})
                cfg.notify_to = notify.get("to", "")
                cfg.mail_prog = notify.get("mail_prog", "")
            except Exception as e:
                print(f"[warn] could not read global config: {e}", file=sys.stderr)
        cfg.ollama_url = os.environ.get("OLLAMA_URL", cfg.ollama_url)
        cfg.notify_to = os.environ.get("RACONTEUR_NOTIFY_TO", cfg.notify_to)
        cfg.mail_prog = os.environ.get("RACONTEUR_MAIL_PROG", cfg.mail_prog)
        return cfg

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
    coordinator: str = "qwen3.6:27b-16k"
    worker: str = "qwen3.5:9b-q4_K_M"


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
    title: str = ""
    short_title: str = ""
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
            yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True)

    @classmethod
    def load(cls, project_dir: Path) -> "ProjectConfig":
        path = project_dir / PROJECT_CONFIG_FILE
        with open(path) as f:
            data = yaml.safe_load(f)
        data.pop("scope", None)
        data.pop("author_initials", None)
        brain_data = data.pop("brain", {})
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
    worker_model: str = "qwen3.5:9b-q4_K_M"

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
            except Exception as e:
                print(f"[warn] could not read global config: {e}", file=sys.stderr)
        cfg.ollama_url = os.environ.get("OLLAMA_URL", cfg.ollama_url)
        return cfg

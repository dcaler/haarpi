"""raster's machine-config binding — the unified ~/.config/haarpi/config.toml.

This is the PII boundary: personal/account details live there and never travel
into a project's committed files. Repos that raster builds are committed under a
deliberately non-PII identity (git.author_name/email) with no co-authorship.
The legacy ~/.config/raster/config.toml is still honored underneath the unified
file; first run writes the unified template.
"""

import os
from dataclasses import dataclass

from haarpi import config as haarpi_config


def config_path():
    """raster's legacy per-tool config path (still honored as a fallback)."""
    return haarpi_config.legacy_path("raster")


@dataclass
class Config:
    ollama_url: str = "http://localhost:11434"
    strong_model: str = "qwen3.6:27b-16k"
    worker_model: str = "llama3.1:8b"
    trundlr_api: str = "http://100.87.86.57:8251"
    gpu_resource: int = 2
    cpu_resource: int = 3
    human_resource: int = 0
    claude_resource: int = 0
    git_host: str = "github.com"
    git_owner: str = "dcaler"
    git_author_name: str = "raster"
    git_author_email: str = "raster@localhost"
    co_authorship: bool = False


def _legacy_normalized() -> dict:
    """raster's old config.toml translated to the unified schema
    (ollama.strong -> ollama.coordinator, trundlr.api_url -> trundlr.url)."""
    data = haarpi_config.load_toml(config_path())
    if not data:
        return {}
    out: dict = {}
    o = data.get("ollama", {})
    if o:
        out["ollama"] = {k: v for k, v in o.items() if k in ("url", "worker")}
        if "strong" in o:
            out["ollama"]["coordinator"] = o["strong"]
    t = data.get("trundlr", {})
    if t:
        out["trundlr"] = {k: v for k, v in t.items() if k != "api_url"}
        if "api_url" in t:
            out["trundlr"]["url"] = t["api_url"]
    if data.get("git"):
        out["git"] = data["git"]
    return out


def load_config(create: bool = True) -> Config:
    """Load machine config, writing the unified template on first run (when
    neither the unified nor the legacy file exists). Env vars override:
    OLLAMA_URL, RASTER_TRUNDLR_API."""
    if create and not haarpi_config.unified_path().exists() and not config_path().exists():
        haarpi_config.write_default_unified()

    data = haarpi_config.merged_config("raster", _legacy_normalized())
    o = data.get("ollama", {})
    t = data.get("trundlr", {})
    g = data.get("git", {})
    cfg = Config(
        ollama_url=o.get("url", Config.ollama_url),
        strong_model=o.get("coordinator", Config.strong_model),
        worker_model=o.get("worker", Config.worker_model),
        trundlr_api=t.get("url", Config.trundlr_api),
        gpu_resource=int(t.get("gpu_resource", Config.gpu_resource)),
        cpu_resource=int(t.get("cpu_resource", Config.cpu_resource)),
        human_resource=int(t.get("human_resource", Config.human_resource)),
        claude_resource=int(t.get("claude_resource", Config.claude_resource)),
        git_host=g.get("host", Config.git_host),
        git_owner=g.get("owner", Config.git_owner),
        git_author_name=g.get("author_name", Config.git_author_name),
        git_author_email=g.get("author_email", Config.git_author_email),
        co_authorship=bool(g.get("co_authorship", Config.co_authorship)),
    )
    # env overrides
    cfg.ollama_url = os.environ.get("OLLAMA_URL", cfg.ollama_url)
    cfg.trundlr_api = os.environ.get("RASTER_TRUNDLR_API", cfg.trundlr_api)
    return cfg

"""rayleigh's machine-config binding — the unified ~/.config/haarpi/config.toml.

This is the PII boundary: personal/account details live there and never travel
into a project's committed files. What rayleigh reads is the author identity
stamped into the .docx write-up, the initials used by the document-revision
naming chain (tool = `ra`, human reviewer = e.g. `DCR`), the design-session
model, and the trundlr binding. The legacy ~/.config/rayleigh/config.toml is
still honored underneath the unified file; first run writes the unified
template.
"""

import os
from dataclasses import dataclass

from haarpi import config as haarpi_config


def config_path():
    """rayleigh's legacy per-tool config path (still honored as a fallback)."""
    return haarpi_config.legacy_path("rayleigh")


@dataclass
class Config:
    author_name: str = "rayleigh"
    tool_initials: str = "ra"
    user_initials: str = "DCR"
    design_model: str = "opus"
    trundlr_api: str = "http://100.87.86.57:8251"
    gpu_resource: int = 2
    cpu_resource: int = 3
    human_resource: int = 0          # 0 = unset (no human review-gate task queued)


def _legacy_normalized() -> dict:
    """rayleigh's old config.toml translated to the unified schema
    ([models] design -> [anthropic] design, trundlr.api_url -> trundlr.url)."""
    data = haarpi_config.load_toml(config_path())
    if not data:
        return {}
    out: dict = {}
    if data.get("author"):
        out["author"] = data["author"]
    m = data.get("models", {})
    if m.get("design"):
        out["anthropic"] = {"design": m["design"]}
    t = data.get("trundlr", {})
    if t:
        out["trundlr"] = {k: v for k, v in t.items() if k != "api_url"}
        if "api_url" in t:
            out["trundlr"]["url"] = t["api_url"]
    return out


def load_config(create: bool = True) -> Config:
    """Load machine config, writing the unified template on first run (when
    neither the unified nor the legacy file exists). Env vars override:
    RAYLEIGH_TRUNDLR_API."""
    if create and not haarpi_config.unified_path().exists() and not config_path().exists():
        haarpi_config.write_default_unified()

    data = haarpi_config.merged_config("rayleigh", _legacy_normalized())
    a = data.get("author", {})
    an = data.get("anthropic", {})
    t = data.get("trundlr", {})
    cfg = Config(
        author_name=a.get("name", Config.author_name),
        tool_initials=a.get("tool_initials", Config.tool_initials),
        user_initials=a.get("user_initials", Config.user_initials),
        design_model=an.get("design", Config.design_model),
        trundlr_api=t.get("url", Config.trundlr_api),
        gpu_resource=int(t.get("gpu_resource", Config.gpu_resource)),
        cpu_resource=int(t.get("cpu_resource", Config.cpu_resource)),
        human_resource=int(t.get("human_resource", Config.human_resource) or 0),
    )
    cfg.trundlr_api = os.environ.get("RAYLEIGH_TRUNDLR_API", cfg.trundlr_api)
    return cfg

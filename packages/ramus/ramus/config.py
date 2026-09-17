"""ramus's machine-config binding — the unified ~/.config/haarpi/config.toml.

This is the PII boundary: personal and account details live there and never travel into a
project's committed files. What ramus reads is the author identity stamped into the prereg
.docx, the initials the document-revision naming chain uses (tool = `ra`, human reviewer =
e.g. `DCR`), the design-session model, and the trundlr binding.

Deliberately its own module rather than an import of `rayleigh.config`. ramus runs BEFORE
raster and rayleigh runs after, so a dependency in that direction would invert the pipeline's
own order — and the shared surface is a dataclass over `haarpi.config`, not logic worth
sharing. Unlike rayleigh there is no legacy per-tool TOML to honour: ramus never had one, and
the values it wants are the unified file's shared sections.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from haarpi import config as haarpi_config


@dataclass
class Config:
    author_name: str = "ramus"
    tool_initials: str = "ra"
    user_initials: str = "DCR"
    design_model: str = "opus"
    trundlr_api: str = "http://100.87.86.57:8251"
    gpu_resource: int = 2
    cpu_resource: int = 3
    human_resource: int = 0          # 0 = unset (no human review-gate task queued)


def load_config(create: bool = True) -> Config:
    """Load machine config, writing the unified template on first run.
    Env override: RAMUS_TRUNDLR_API."""
    if create and not haarpi_config.unified_path().exists():
        haarpi_config.write_default_unified()

    data = haarpi_config.merged_config("ramus")
    a = data.get("author", {})
    an = data.get("anthropic", {})
    t = data.get("trundlr", {})
    return Config(
        author_name=a.get("name", Config.author_name),
        tool_initials=a.get("tool_initials", Config.tool_initials),
        user_initials=a.get("user_initials", Config.user_initials),
        design_model=an.get("design", Config.design_model),
        trundlr_api=os.environ.get("RAMUS_TRUNDLR_API",
                                   t.get("url", Config.trundlr_api)),
        gpu_resource=int(t.get("gpu_resource", Config.gpu_resource)),
        cpu_resource=int(t.get("cpu_resource", Config.cpu_resource)),
        human_resource=int(t.get("human_resource", Config.human_resource)),
    )

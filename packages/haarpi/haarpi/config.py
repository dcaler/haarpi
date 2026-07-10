"""The unified machine config: ~/.config/haarpi/config.toml.

This is the PII boundary for the whole pipeline: personal/account details live
here and never travel into a project's committed files. One file replaces the
four per-tool configs (~/.config/{rabbithole,raconteur,raster,rayleigh}/), which
remain honored as fallbacks during the transition.

Precedence, resolved per section by merged_config():

    [tools.<tool>] overrides  >  unified shared sections  >  the tool's legacy
    file (normalized to this schema by the tool's binding)  >  code defaults

Environment-variable overrides stay in each tool's binding (the var names are
tool-specific and documented there).

Schema (all sections optional):

    contact_email = ""              # API "polite pools" (OpenAlex/Crossref)

    [ollama]                        # url, coordinator, worker, embed
    [anthropic]                     # api_key, design (claude session model)
    [zotero]                        # api_key, library_id, library_type
    [semantic_scholar]              # api_key
    [notify]                        # to, mail_prog
    [trundlr]                       # url, gpu_resource, cpu_resource,
                                    # human_resource, claude_resource, runner_resource
    [git]                           # host, owner, author_name, author_email, co_authorship
    [author]                        # name, tool_initials, user_initials
    [tools.<tool>]                  # per-tool override of any of the above
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

TOOLS = ("rabbithole", "raconteur", "raster", "rayleigh")

DEFAULT_UNIFIED_TOML = """\
# HAARPi machine config — shared by rabbitHole, raconteur, raster, and rayleigh.
# Personal details stay here: never committed into a project.

contact_email = ""           # used for API "polite pools" — recommended

[ollama]
url         = "http://localhost:11434"
coordinator = "qwen3.6:27b-16k"   # synthesis, reading, gate/test authoring
worker      = "llama3.1:8b"       # scaffolding / boilerplate / fallback tasks
embed       = "mxbai-embed-large"

[anthropic]
api_key = ""                 # only for the Claude brain
design  = "opus"             # model for interactive design sessions (claude --model)

[zotero]
api_key      = ""
library_id   = ""
library_type = "user"        # "user" | "group"

[semantic_scholar]
api_key = ""

[notify]
to        = ""               # recipient; falls back to contact_email
mail_prog = ""               # override; else SLURM MailProg / `mail` is auto-detected

[trundlr]
url             = "http://100.87.86.57:8251"
gpu_resource    = 2          # LLM/build tasks
cpu_resource    = 3          # gates, process_outputs
human_resource  = 0          # your trundlr resource id (0 = unset)
claude_resource = 0          # Claude agent resource id (0 = unset)
runner_resource = 0          # resource the rabbitHole runner polls (0 = unset)

[git]
host          = "github.com"
owner         = "dcaler"
author_name   = "raster"          # non-PII identity used for commits in BUILT repos
author_email  = "raster@localhost"
co_authorship = false             # never emit Co-Authored-By trailers

[author]
name          = "rayleigh"   # stamped into .docx write-up metadata
tool_initials = "ra"         # trailing suffix on tool-authored files (revision chain)
user_initials = "DCR"        # the human reviewer's initials

[planner]
# Tiers that insert an "approve plan" human task at the head of the rework
# chain (mark it done in trundlr to release the chain). Cheap tiers run
# hands-free; a redirection re-aims the whole stage, so it asks first.
confirm_tiers = ["redirection"]

# Per-tool overrides of any section above, e.g.:
# [tools.raster.ollama]
# coordinator = "qwen3.6:27b-32k"
"""


def config_root() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base)


def unified_path() -> Path:
    return config_root() / "haarpi" / "config.toml"


def legacy_path(tool: str) -> Path:
    return config_root() / tool / "config.toml"


def load_toml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except Exception as e:  # noqa: BLE001 — a broken config must not kill a run
        print(f"[warn] could not read {path}: {e}")
        return {}


def write_default_unified() -> Path:
    """First-run: write the commented template (only if absent)."""
    p = unified_path()
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(DEFAULT_UNIFIED_TOML)
    return p


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def merged_config(tool: str, legacy_normalized: dict | None = None) -> dict:
    """The tool's effective config in the unified schema.

    `legacy_normalized` is the tool's old per-tool file already translated to
    this schema by its binding (the old files use renamed keys: api_url/strong/
    [models] and so on). Unified values win over legacy; [tools.<tool>] wins
    over both.
    """
    unified = load_toml(unified_path())
    tool_over = (unified.get("tools") or {}).get(tool, {})
    merged = _deep_merge(legacy_normalized or {}, unified)
    merged.pop("tools", None)
    return _deep_merge(merged, tool_over)

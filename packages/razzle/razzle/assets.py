"""razzle.assets — the neutral, never-in-repo branding: masters, layout descriptors, and the
affiliation/funder logo registries. These live in `~/.config/haarpi/razzle/` (the same PII boundary
as the style profiles) — razzle's CODE ships in the repo; the branding never does.

Layout:
    ~/.config/haarpi/razzle/
      masters/<name>.pptx      the master deck (layouts + theme)
      masters/<name>.yaml      the layout descriptor (roles -> layouts/placeholders)
      affiliations.yaml        affiliation name -> {logo: logos/...}
      funders.yaml             funder name      -> {logo: logos/...}
      logos/                   the logo image files
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from haarpi import config as _hconfig


def home() -> Path:
    """The neutral razzle asset dir (override with RAZZLE_HOME)."""
    return Path(os.environ.get("RAZZLE_HOME") or (_hconfig.config_root() / "haarpi" / "razzle"))


def master_pptx(name: str = "default") -> Path | None:
    p = home() / "masters" / f"{name}.pptx"
    return p if p.is_file() else None


def descriptor(name: str = "default") -> dict | None:
    """The layout descriptor for a master, with its `master` resolved to an absolute `master_path`."""
    y = home() / "masters" / f"{name}.yaml"
    if not y.is_file():
        return None
    d = yaml.safe_load(y.read_text()) or {}
    if d.get("master"):
        d["master_path"] = str(home() / "masters" / d["master"])
    return d


def _registry(kind: str) -> dict:
    y = home() / f"{kind}.yaml"
    return (yaml.safe_load(y.read_text()) or {}) if y.is_file() else {}


def logos_for(affiliations: list[str] | None = None,
              funders: list[str] | None = None) -> list[Path]:
    """Ordered, existing logo paths for the named affiliations + funders, from the registries.
    Unmatched names (or missing files) are skipped — they degrade to text where they'd be placed."""
    affs, fnd = _registry("affiliations"), _registry("funders")
    out: list[Path] = []
    for name in list(affiliations or []) + list(funders or []):
        entry = affs.get(name) or fnd.get(name)
        if entry and entry.get("logo"):
            p = home() / entry["logo"]
            if p.is_file():
                out.append(p)
    return out

"""razzle.assets — the neutral, never-in-repo branding: masters, layout descriptors, and the
affiliation/funder logo registries. These live in `~/.config/haarpi/razzle/` (the same PII boundary
as the style profiles) — razzle's CODE ships in the repo; the branding never does.

Layout:
    ~/.config/haarpi/razzle/
      masters/<name>.pptx      the master deck (layouts + theme)
      masters/<name>.yaml      the layout descriptor (roles -> layouts/placeholders)
      affiliations.yaml        affiliation name -> {logo: logos/..., aliases: [other spellings]}
      funders.yaml             funder name      -> {logo: logos/..., aliases: [...]}
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


def _norm(s: str) -> str:
    return " ".join(str(s).split()).casefold()


def _lookup(reg: dict, name: str) -> dict | None:
    """Find a registry entry for `name`: the exact key, else an entry listing it in `aliases`, else
    either of those compared loosely (case and internal whitespace).

    A manifest and this registry are written by different hands at different times, so the same
    institution arrives as "VCUarts Qatar" in one and "Virginia Commonwealth University School of
    the Arts, Qatar" in the other. An alias is how the official long name reaches the logo filed
    under the short one, instead of the name silently resolving to nothing.
    """
    if name in reg:
        return reg[name]
    for entry in reg.values():
        if isinstance(entry, dict) and name in (entry.get("aliases") or []):
            return entry
    target = _norm(name)
    for key, entry in reg.items():
        if not isinstance(entry, dict):
            continue
        if _norm(key) == target or any(_norm(a) == target for a in entry.get("aliases") or []):
            return entry
    return None


def logo_entries(affiliations: list[str] | None = None,
                 funders: list[str] | None = None) -> list[dict]:
    """Ordered [{name, logo: Path|None}] for the named affiliations + funders.

    An unmatched name — or one whose registered file is missing — KEEPS ITS PLACE with logo=None,
    so the caller can degrade it to text. That degradation is what these registries have always
    documented ("a missing/unmatched affiliation degrades to text"); dropping the name silently
    meant an affiliation the author explicitly chose in the interview simply vanished.
    """
    affs, fnd = _registry("affiliations"), _registry("funders")
    out: list[dict] = []
    for name in list(affiliations or []) + list(funders or []):
        entry = _lookup(affs, name) or _lookup(fnd, name)
        path = None
        if entry and entry.get("logo"):
            p = home() / entry["logo"]
            path = p if p.is_file() else None
        out.append({"name": name, "logo": path})
    return out


def logos_for(affiliations: list[str] | None = None,
              funders: list[str] | None = None) -> list[Path]:
    """Just the resolved logo FILES, in order — for callers that cannot render a text fallback."""
    return [e["logo"] for e in logo_entries(affiliations, funders) if e["logo"]]

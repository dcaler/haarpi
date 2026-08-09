"""razzle.gather — pull the real deck inputs from a project. razzle feeds ON the paper stage.

 - narrative: raconteur's one-pager (the talk's spine) — the gate-minted release, else the newest draft;
 - figures: the shared figure pool (haarpi.figure), by id + caption;
 - claims: rayleigh's findings.json — each experiment's observed `finding` (the real numbers, verbatim);
 - logos: the author affiliations + project funders, resolved to logo files via the neutral registries.

Everything is best-effort: an absent one-pager / findings / manifest yields "" or [], never a crash.
"""

from __future__ import annotations

import json
from pathlib import Path

from haarpi import figure as _figure
from haarpi import naming as _naming
from haarpi import project as _hproject

from razzle import assets


def short_title(root: Path) -> str:
    try:
        return _hproject.load_manifest(root).short_title or root.name
    except Exception:
        return root.name


def narrative(root: Path, short: str) -> str:
    """The one-pager — raconteur's loader (release > working chain) if available, else the newest
    `paper/output/*_onepager*.md` by the naming chain."""
    try:
        from raconteur import context as _ctx
        n = _ctx.load_onepager(root, short)
        if n:
            return n
    except Exception:
        pass
    pdir = root / "paper" / "output"
    if not pdir.is_dir():
        return ""
    p = (_naming.find_latest_release(pdir, short, "md", chain_includes="onepager")
         or _naming.find_latest(pdir, short, "md", chain_includes="onepager"))
    return p.read_text(encoding="utf-8") if p and p.is_file() else ""


def figures(root: Path, short: str) -> list[dict]:
    return [{"id": fid, "caption": _figure.caption_of(root, short, fid)}
            for fid in _figure.list_ids(root, short)]


def claims(root: Path, results_dir: str = "results") -> str:
    """The real results, verbatim: each experiment's observed `finding` from findings.json."""
    f = root / results_dir / "findings.json"
    if not f.is_file():
        return ""
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return ""
    exps = data.get("experiments", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
    out = []
    for e in exps:
        if isinstance(e, dict) and e.get("finding"):
            q = e.get("question") or e.get("title") or e.get("id", "")
            out.append(f"- {q}: {e['finding']}" if q else f"- {e['finding']}")
    return "\n".join(out)


def logos(root: Path) -> list[Path]:
    """Author-affiliation + funder logos for this project, from the manifest + the neutral registries."""
    try:
        m = _hproject.load_manifest(root)
    except Exception:
        return []
    affs: list[str] = []
    for a in getattr(m, "authors", []) or []:
        if isinstance(a, dict):
            affs += a.get("affiliations", []) or []
    funders = [f.get("name") for f in getattr(m, "funders", []) or [] if isinstance(f, dict) and f.get("name")]
    return assets.logos_for(affiliations=affs, funders=funders)


def bundle(root: Path) -> dict:
    """Everything compose needs from a project, in one call."""
    short = short_title(root)
    return {"short_title": short, "narrative": narrative(root, short),
            "figures": figures(root, short), "claims": claims(root), "logos": logos(root)}

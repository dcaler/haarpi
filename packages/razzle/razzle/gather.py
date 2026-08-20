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
    """The one-pager — the talk's SPINE. raconteur's loader (release > working chain); it resolves
    `paper/onepager/` itself, so there is no separate fallback path to keep in sync here."""
    try:
        from raconteur import context as _ctx
        return _ctx.load_onepager(root, short) or ""
    except Exception:
        return ""


def _venue_folder(root: Path, cfg: dict) -> str:
    """The venue whose manuscript backs this deck. The deck config's venue (from the interview /
    the triggering submission) is authoritative; absent that, auto-detect the sole `paper/<venue>/`
    that holds a manuscript (deliverable_dir lower-cases, so 'CSS2026' and 'css2026' both resolve)."""
    if cfg.get("venue"):
        return str(cfg["venue"])
    paper = root / "paper"
    if not paper.is_dir():
        return ""
    venues = [d.name for d in paper.iterdir()
              if d.is_dir() and (d / "manuscript").is_dir()]
    return venues[0] if len(venues) == 1 else ""


def manuscript(root: Path, short: str, venue: str = "") -> str:
    """The full paper — the deck's SUBSTANCE (the real claims, framing, citations, secondary results
    the one-pager compresses away). raconteur's `load_manuscript` (minted release > newest draft),
    scoped to `venue`. Best-effort: absent → "" (the deck still composes from the one-pager)."""
    try:
        from raconteur import context as _ctx
        return _ctx.load_manuscript(root, short, venue) or ""
    except Exception:
        return ""


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


def deck_config(root: Path, fmt: str | None) -> dict:
    """The per-format deck config `razzle interview` wrote (`manifest.decks[fmt]`), or {}."""
    if not fmt:
        return {}
    try:
        return (getattr(_hproject.load_manifest(root), "decks", {}) or {}).get(fmt, {}) or {}
    except Exception:
        return {}


def logos(root: Path, fmt: str | None = None) -> list[Path]:
    """Affiliation + funder logos, from the neutral registries. When a deck config exists for `fmt`,
    only its SELECTED affiliations/funders (the interview's choices); otherwise every author's
    affiliations + every project funder (the global fallback)."""
    try:
        m = _hproject.load_manifest(root)
    except Exception:
        return []
    cfg = deck_config(root, fmt)
    if cfg:
        return assets.logos_for(affiliations=cfg.get("affiliations", []), funders=cfg.get("funders", []))
    affs: list[str] = []
    for a in getattr(m, "authors", []) or []:
        if isinstance(a, dict):
            affs += a.get("affiliations", []) or []
    funders = [f.get("name") for f in getattr(m, "funders", []) or [] if isinstance(f, dict) and f.get("name")]
    return assets.logos_for(affiliations=affs, funders=funders)


def byline(root: Path, fmt: str | None = None) -> str:
    """The presenting authors as a comma list (the title-slide subtitle) — the deck config's chosen
    authors, else all manifest authors. A deterministic fact, never the LLM's to write."""
    try:
        m = _hproject.load_manifest(root)
    except Exception:
        return ""
    names = deck_config(root, fmt).get("authors") or [
        a.get("name") for a in getattr(m, "authors", []) or [] if isinstance(a, dict) and a.get("name")]
    return ", ".join(n for n in names if n)


def apply_byline(spec: list[dict], line: str) -> list[dict]:
    """Stamp the presenting authors onto the title slide's subtitle (facts over the LLM's guess)."""
    if line and spec and spec[0].get("role") == "title":
        spec[0]["subtitle"] = line
    return spec


def bundle(root: Path, fmt: str | None = None) -> dict:
    """Everything compose + render need from a project, in one call. `fmt` scopes the logos/byline/
    venue/date to that format's deck config when the interview has set one."""
    short = short_title(root)
    cfg = deck_config(root, fmt)
    venue = _venue_folder(root, cfg)
    return {"short_title": short, "narrative": narrative(root, short),
            "manuscript": manuscript(root, short, venue),
            "figures": figures(root, short), "claims": claims(root),
            "logos": logos(root, fmt), "byline": byline(root, fmt),
            "venue": cfg.get("venue", ""), "date": cfg.get("date", "")}

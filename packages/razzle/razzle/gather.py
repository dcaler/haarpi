"""razzle.gather — pull the real deck inputs from a project. razzle feeds ON the paper stage.

 - narrative: raconteur's one-pager (the talk's spine) — the gate-minted release, else the newest draft;
 - figures: the shared figure pool (haarpi.figure), by id + caption;
 - claims: rayleigh's findings.json — each experiment's observed `finding` (the real numbers, verbatim);
 - title: the PAPER's title — the talk is the paper, so its name is not the composer's to invent;
 - logos: the author affiliations + project funders, resolved to logo files via the neutral registries;
 - byline: every author, in authorship order — with ONE contact address, the presenter's.

Everything is best-effort: an absent one-pager / findings / manifest yields "" or [], never a crash.
"""

from __future__ import annotations

import json
import re
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


def paper_title(manuscript_md: str) -> str:
    """The paper's title: the manuscript's first level-1 heading.

    A talk IS the paper, so the title slide carries the paper's name. Left to the composer it
    invents one — a live deck went out titled "Local Preferences Generate Emergent Tonal Clusters"
    for a paper called "A New Sense of Schelling Segregation" — and the invented title then
    propagated into the running footer on every slide after it. It is a fact, so it is read, not
    written.
    """
    for line in (manuscript_md or "").splitlines():
        line = line.strip()
        if line.startswith("# "):
            return re.sub(r"\s+", " ", line[2:]).strip()
        if line.startswith("#"):        # a deeper heading first means there is no title heading
            break
    return ""


def talk_title(root: Path, fmt: str | None = None, manuscript_md: str | None = None) -> str:
    """The title this deck opens on: the paper's, else the project's short title. `manuscript_md`
    lets a caller that has already loaded the manuscript hand it in rather than read it twice."""
    if manuscript_md is None:
        short = short_title(root)
        manuscript_md = manuscript(root, short, _venue_folder(root, deck_config(root, fmt)))
    return paper_title(manuscript_md) or short_title(root)


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


def deck_dir(root: Path, fmt: str | None) -> Path:
    """Where this deck lives: `slides/<venue>/`.

    The VENUE is what a deck is browsed by — "which talk is the CSS2026 one" is the question
    anyone actually asks; the format is a property of that talk, not a way to find it. Falls back
    to the format when no venue is configured, so a deck authored before the interview has named
    one still lands somewhere sensible instead of in `slides/`.
    """
    cfg = deck_config(root, fmt)
    return root / "slides" / (str(cfg.get("venue") or "").strip() or (fmt or "deck"))


def format_for_venue(root: Path, venue: str) -> str:
    """Which configured format targets `venue`. The board and the queued commands are
    venue-scoped, but razzle still authors ONE format, so the two have to meet somewhere."""
    try:
        m = _hproject.load_manifest(root)
    except Exception:
        return ""
    want = " ".join(str(venue).split()).casefold()
    for fmt, cfg in (getattr(m, "decks", {}) or {}).items():
        if " ".join(str(cfg.get("venue", "")).split()).casefold() == want:
            return fmt
    return ""


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


def logo_entries(root: Path, fmt: str | None = None) -> list[dict]:
    """[{name, logo}] for the deck's affiliations + funders — the name is kept so a mark with no
    registered logo can be set in text instead of vanishing."""
    try:
        m = _hproject.load_manifest(root)
    except Exception:
        return []
    cfg = deck_config(root, fmt)
    if cfg:
        return assets.logo_entries(affiliations=cfg.get("affiliations", []),
                                   funders=cfg.get("funders", []))
    affs: list[str] = []
    for a in getattr(m, "authors", []) or []:
        if isinstance(a, dict):
            affs += a.get("affiliations", []) or []
    funders = [f.get("name") for f in getattr(m, "funders", []) or [] if isinstance(f, dict) and f.get("name")]
    return assets.logo_entries(affiliations=affs, funders=funders)


def furniture(root: Path, fmt: str | None, spec: list[dict] | None = None) -> dict:
    """The deck-level RUNNING TEXT: the venue/date line on the title slide, and the footer +
    contact address that repeat on every slide after it.

    None of this is per-slide, so none of it belongs in the spec: these are facts from the deck
    config, and keeping them out of the composer's hands is what stops a model inventing a venue.
    The footer follows the house convention of `venue | talk title`.
    """
    cfg = deck_config(root, fmt)
    venue, date = str(cfg.get("venue", "")), str(cfg.get("date", ""))
    talk = (spec[0].get("title", "") if spec else "") or short_title(root)
    return {"venue": " · ".join(x for x in (venue, date) if x),
            "footer": " | ".join(x for x in (venue, talk) if x),
            "contact": presenter_email(root, fmt)}


def byline(root: Path) -> str:
    """EVERY author, in authorship order — the title-slide subtitle. Authorship is a fact about the
    work, not about who happens to be at the podium, so the byline is NOT scoped to the deck config's
    presenting authors: a co-author is credited whether or not they travel. This matches the logo
    question, which already offers every co-author's affiliation for the same reason. A deterministic
    fact, never the LLM's to write."""
    try:
        m = _hproject.load_manifest(root)
    except Exception:
        return ""
    return ", ".join(a["name"] for a in _hproject.authors(m) if a.get("name"))


def presenter(root: Path, fmt: str | None = None) -> dict:
    """The ONE author at the podium — the deck config's first presenting author, else the
    corresponding author, else the first author. Whoever it is owns the contact address on the
    title slide."""
    try:
        m = _hproject.load_manifest(root)
    except Exception:
        return {}
    people = _hproject.authors(m)
    by_name = {a.get("name"): a for a in people}
    for n in deck_config(root, fmt).get("authors") or []:
        if n in by_name:
            return by_name[n]
    corr = _hproject.corresponding_authors(m)
    return corr[0] if corr else (people[0] if people else {})


def presenter_email(root: Path, fmt: str | None = None) -> str:
    """The single contact address for the deck: the presenting author's. One email, not one per
    author — it is a contact address for this talk, not a credential every co-author wants printed
    (the same doctrine haarpi.project.authors_block applies to a manuscript, resolved to the podium
    rather than to correspondence)."""
    return presenter(root, fmt).get("email", "")


def apply_byline(spec: list[dict], line: str, email: str = "") -> list[dict]:
    """Stamp the author list — and the one contact address — onto the title slide (facts over the
    LLM's guess). Layout 0 offers a title and a subtitle and nothing else, so the two share the
    subtitle as separate paragraphs: authors, then the presenter's email beneath them."""
    if not (spec and spec[0].get("role") == "title" and line):
        return spec
    spec[0]["subtitle"] = [line, email] if email else line
    return spec


def apply_title(spec: list[dict], title: str) -> list[dict]:
    """Stamp the PAPER's title onto the title slide. Must run before `furniture`, which builds the
    running footer out of `spec[0]["title"]` — otherwise an invented title propagates onto every
    slide in the deck."""
    if spec and spec[0].get("role") == "title" and title:
        spec[0]["title"] = title
    return spec


def acknowledgements(root: Path, fmt: str | None = None) -> dict:
    """The closing slide, built from facts: the funders to thank in text, and the affiliation +
    funder marks in the logo strip.

    This is where the logos belong. The title slide was showing them, squeezed into a band under
    the byline where two 0.7" marks were all that fit — and a title slide's job is the title, the
    authors and the venue. Acknowledgement is its own slide because acknowledging is its own act.
    """
    cfg = deck_config(root, fmt)
    funders = [f for f in (cfg.get("funders") or []) if str(f).strip()]
    slide: dict = {"role": "acknowledgements", "title": "Acknowledgements"}
    if funders:
        slide["body"] = [f"Supported by {f}" for f in funders]
    return slide


def apply_acknowledgements(spec: list[dict], slide: dict) -> list[dict]:
    """Put the acknowledgements slide last, exactly once.

    Idempotent by construction: any existing acknowledgements slide is removed first, so
    re-rendering an edited `spec.json` refreshes it instead of stacking a second one.
    """
    out = [s for s in spec if s.get("role") != "acknowledgements"]
    out.append(slide)
    return out


def bundle(root: Path, fmt: str | None = None) -> dict:
    """Everything compose + render need from a project, in one call. `fmt` scopes the logos/byline/
    venue/date to that format's deck config when the interview has set one."""
    short = short_title(root)
    cfg = deck_config(root, fmt)
    venue = _venue_folder(root, cfg)
    ms = manuscript(root, short, venue)
    return {"short_title": short, "narrative": narrative(root, short),
            "manuscript": ms, "title": paper_title(ms) or short,
            "figures": figures(root, short), "claims": claims(root),
            "logos": logo_entries(root, fmt), "byline": byline(root),
            "email": presenter_email(root, fmt),
            "venue": cfg.get("venue", ""), "date": cfg.get("date", "")}

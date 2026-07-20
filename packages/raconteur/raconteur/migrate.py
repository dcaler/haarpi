"""One-time move from the flat `paper/` layout to a folder per deliverable.

    paper/260719_Chords_css2026_ra.docx   ->  paper/css2026/manuscript/
    paper/260719_Chords_css2026_outline.md -> paper/css2026/outline/output/
    paper/260715_Chords_onepager_ra.docx  ->  paper/onepager/
    paper/venue_analysis.md               ->  paper/venue/

A release (no `ra` in its chain) goes to the deliverable's `output/`; anything still
carrying author tokens is live work and stays at the deliverable's root. `old/` keeps its
contents but moves under the deliverable it belongs to, so a discard stays a discard.

Moves, never copies or deletes — and never over an existing file. A clobbered document is
unrecoverable, and the naming chain exists precisely so two generations can coexist.
"""

from __future__ import annotations

from pathlib import Path

from haarpi import naming as hnaming

from .log import log
from .naming import MANUSCRIPT, deliverable_dir

# Files that belong to the stage, not to any one deliverable.
_STAY = {"raconteur.yaml"}
_STAY_DIRS = {"figures"}
_DELIVERABLE_WORDS = ("onepager", "venue", "skeleton", "outline")


def classify(path: Path, short_title: str, known_venues: list[str]) -> tuple[str, str, bool]:
    """(deliverable, venue, is_release) for one file.

    Unparseable names are left where they are — a file that does not follow the chain is
    not ours to move, and guessing at it is how a hand-made note ends up filed as a draft.
    """
    if path.name == "venue_analysis.md":
        return ("venue", "", False)
    parsed = hnaming.parse(path, short_title)
    if parsed is None:
        return ("", "", False)
    _, chain, _ = parsed
    lower = [c.lower() for c in chain]
    deliverable = next((w for w in _DELIVERABLE_WORDS if w in lower), MANUSCRIPT)
    # `known=[]` matches nothing, so a project whose slate is empty or unreadable would file
    # every venue's work as venue-free and collapse two venues into one folder. Fall back to
    # the heuristic (a lowercase token that is neither `ra` nor a deliverable word).
    venue = hnaming.venue_of(path, short_title, known=known_venues or None)
    return (deliverable, venue, hnaming.is_release(chain))


def plan(paper_dir: Path, short_title: str, known_venues: list[str]) -> list[tuple[Path, Path]]:
    """Every (src, dst) this migration would perform. Pure — makes no changes."""
    moves: list[tuple[Path, Path]] = []
    sources: list[Path] = []
    for p in paper_dir.iterdir():
        if p.is_file() and p.name not in _STAY:
            sources.append(p)
        elif p.is_dir() and p.name in ("output", "old", "archive"):
            sources += [q for q in p.rglob("*") if q.is_file()]
    for src in sources:
        deliverable, venue, is_release = classify(src, short_title, known_venues)
        if not deliverable:
            continue
        home = deliverable_dir(paper_dir, deliverable, venue)
        # An archived discard stays archived, under the deliverable it belonged to.
        if "old" in src.parts or "archive" in src.parts:
            dst = home / src.parent.name / src.name
        elif is_release or src.parent.name == "output":
            dst = home / "output" / src.name
        else:
            dst = home / src.name
        if dst != src:
            moves.append((src, dst))
    return moves


def template_moves(paper_dir: Path, known_venues: list[str]) -> list[tuple[Path, Path]]:
    """`paper/templates/<venue>/` -> `paper/<venue>/templates/`.

    A template is the venue's house style, so it belongs with that venue's work; the old
    layout named the venue twice, once in a shared folder and once inside it.
    """
    src_root = paper_dir / "templates"
    if not src_root.is_dir():
        return []
    out = []
    for d in src_root.iterdir():
        # Strictly a KNOWN venue: filing templates/<x>/ under <x>/ when the slate has
        # never heard of <x> invents a venue folder out of a directory name.
        if d.is_dir() and d.name in known_venues:
            out.append((d, paper_dir / d.name / "templates"))
    return out


def run(project_dir: Path, dry_run: bool = False) -> int:
    from .config import ProjectConfig
    paper_dir = project_dir / "paper"
    if not paper_dir.is_dir():
        log("[migrate] no paper/ directory — nothing to do")
        return 0
    cfg = ProjectConfig.load(project_dir) if ProjectConfig.exists(project_dir) else None
    short_title = cfg.short_title if cfg else project_dir.name
    venues = list(cfg.venues) if cfg else []

    moves = plan(paper_dir, short_title, venues)
    tmoves = template_moves(paper_dir, venues)
    if not moves and not tmoves:
        log("[migrate] already organised by deliverable — nothing to move")
        return 0
    clashes = [(s, d) for s, d in moves if d.exists()]
    if clashes:
        for s, d in clashes:
            log(f"[error] {d.relative_to(paper_dir)} already exists — refusing to clobber "
                f"(from {s.relative_to(paper_dir)})")
        log("[error] migration aborted; nothing moved")
        return 1
    for src, dst in tmoves:
        rel_s, rel_d = src.relative_to(paper_dir), dst.relative_to(paper_dir)
        if dst.exists():
            log(f"[error] {rel_d} already exists — refusing to clobber")
            return 1
        if dry_run:
            log(f"[dry-run] {rel_s}/  ->  {rel_d}/")
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.rename(dst)
            log(f"[migrate] {rel_s}/  ->  {rel_d}/")

    for src, dst in moves:
        rel_s, rel_d = src.relative_to(paper_dir), dst.relative_to(paper_dir)
        if dry_run:
            log(f"[dry-run] {rel_s}  ->  {rel_d}")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
        log(f"[migrate] {rel_s}  ->  {rel_d}")
    log(f"[migrate] {len(moves)} file(s){' would move' if dry_run else ' moved'}")
    return 0

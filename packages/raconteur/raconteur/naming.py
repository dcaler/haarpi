from __future__ import annotations
import re
from datetime import date
from pathlib import Path


def today() -> str:
    return date.today().strftime("%y%m%d")


def _pattern(short_title: str) -> re.Pattern:
    return re.compile(
        rf"^(\d{{6}})_{re.escape(short_title)}((?:_[A-Za-z]+)+)\.(md|docx)$"
    )


def parse(path: Path, short_title: str) -> tuple[str, list[str], str] | None:
    """Returns (datestamp, initials_chain, ext) or None if filename doesn't match."""
    m = _pattern(short_title).match(path.name)
    if not m:
        return None
    datestamp = m.group(1)
    chain = [x for x in m.group(2).split("_") if x]
    ext = m.group(3)
    return datestamp, chain, ext


def major_name(short_title: str, ext: str) -> str:
    """Fresh raconteur file — resets chain to ra, updates date stamp."""
    return f"{today()}_{short_title}_ra.{ext}"


def major_outline_name(short_title: str, ext: str) -> str:
    """Fresh outline file — chain is outline_ra."""
    return f"{today()}_{short_title}_outline_ra.{ext}"


def minor_name(short_title: str, current_chain: list[str], ext: str) -> str:
    """Minor update (focus) — appends ra to the existing chain."""
    chain = "_".join(current_chain + ["ra"])
    return f"{today()}_{short_title}_{chain}.{ext}"


def find_latest(
    paper_dir: Path,
    short_title: str,
    ext: str,
    last_initials: str | None = None,
    chain_includes: str | None = None,
    chain_excludes: str | list[str] | None = None,
) -> Path | None:
    """Newest file matching the naming convention.

    chain_includes: only files whose chain contains this element.
    chain_excludes: skip files whose chain contains any of these elements.
    """
    excludes = (
        [chain_excludes] if isinstance(chain_excludes, str) else (chain_excludes or [])
    )
    candidates = []
    for p in paper_dir.glob(f"*.{ext}"):
        result = parse(p, short_title)
        if result is None:
            continue
        _, chain, _ = result
        chain_lower = [c.lower() for c in chain]
        if last_initials is not None:
            if not chain or chain[-1].lower() != last_initials.lower():
                continue
        if chain_includes is not None:
            if chain_includes.lower() not in chain_lower:
                continue
        if any(exc.lower() in chain_lower for exc in excludes):
            continue
        candidates.append(p)
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def find_user_revision(
    paper_dir: Path,
    short_title: str,
    chain_includes: str | None = None,
    chain_excludes: str | list[str] | None = None,
) -> Path | None:
    """Newest .docx whose last initials are not 'ra' (i.e. the researcher's revision)."""
    excludes = (
        [chain_excludes] if isinstance(chain_excludes, str) else (chain_excludes or [])
    )
    candidates = []
    for p in paper_dir.glob("*.docx"):
        result = parse(p, short_title)
        if result is None:
            continue
        _, chain, _ = result
        if not chain or chain[-1].lower() == "ra":
            continue
        chain_lower = [c.lower() for c in chain]
        if chain_includes is not None:
            if chain_includes.lower() not in chain_lower:
                continue
        if any(exc.lower() in chain_lower for exc in excludes):
            continue
        candidates.append(p)
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)

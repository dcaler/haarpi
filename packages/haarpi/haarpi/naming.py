"""The document revision naming chain: `260710_title[_infix]_ra_DCR.docx`.

Every human-gate document in the pipeline follows it: `_ra` is a tool's draft,
a reviewer appends their initials, the tool's answer appends `_ra` again. A
major version (fresh draft) resets the chain and takes today's datestamp; a
minor version (redline/focus) keeps the source file's datestamp.

The chain records whose court the ball is in — so a RELEASE (a gate-passed,
consolidated document) carries no author tokens at all: nobody's turn.
`260715_title_litreview.docx` is stable; anything with `ra` in its chain is in
play. Since every working file starts life as a tool draft, the mechanical
rule is: release ⟺ `ra` not in the chain (deliverable words like `litreview`
or `onepager` are chain elements by convention and don't count as authors).
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path


def today() -> str:
    return date.today().strftime("%y%m%d")


def _tokens(value: str | list[str] | None) -> list[str]:
    """Chain tokens to match against, lowercased. One, several, or none."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value.lower()]
    return [v.lower() for v in value if v]


# Chain tokens that name a KIND of document. Everything else in a chain is either an author
# (initials, or `ra`) or a VENUE — which is how a venue rides in a filename without the
# parser, the gate, the redline or the release logic needing to know venues exist.
DELIVERABLE_WORDS = ("onepager", "venue", "outline", "litreview", "methods", "results")


def venue_of(path: Path, short_title: str, known: list[str] | None = None) -> str:
    """The venue a document is FOR — '' for the shared ones (the one-pager, the litreview).

    `260714_Chords_ismir_outline_ra_DCR.docx` is ISMIR's outline; the JASSS one sits beside
    it, and each redline finds its own.
    """
    parsed = parse(path, short_title)
    if not parsed:
        return ""
    known_lower = [k.lower() for k in known] if known is not None else None
    for token in parsed[1]:
        t = token.lower()
        if t in DELIVERABLE_WORDS or t == "ra":
            continue
        if known_lower is not None:
            if t in known_lower:
                return t
            continue
        if token != t:          # initials are written as initials (DCR); a slug is lower
            continue
        return t
    return ""


def _pattern(short_title: str) -> re.Pattern:
    # chain is * not + : a fully bare release (`260715_title.docx`) has no chain at all
    return re.compile(
        rf"^(\d{{6}})_{re.escape(short_title)}((?:_[A-Za-z]+)*)\.(md|docx)$"
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


def major_name(short_title: str, ext: str, infix: str = "") -> str:
    """Fresh tool draft — resets the chain to ra, updates the datestamp.
    `infix` names the deliverable kind (e.g. 'outline', 'onepager')."""
    mid = f"_{infix}" if infix else ""
    return f"{today()}_{short_title}{mid}_ra.{ext}"


def release_name(short_title: str, ext: str, infix: str = "") -> str:
    """A gate-passed consolidation — fresh datestamp, NO author tokens.
    The deliverable word (`litreview`, `onepager`, …) survives; the chain doesn't."""
    mid = f"_{infix}" if infix else ""
    return f"{today()}_{short_title}{mid}.{ext}"


def is_release(chain: list[str], tool_initials: str = "ra") -> bool:
    """Nobody's turn: no tool token in the chain (see module docstring)."""
    return tool_initials.lower() not in [c.lower() for c in chain]


def find_latest_release(
    doc_dir: Path,
    short_title: str,
    ext: str = "docx",
    chain_includes: str | list[str] | None = None,
) -> Path | None:
    """Newest release — what an unattended consumer binds. `chain_includes`
    narrows to a deliverable kind (e.g. 'litreview', or ['ismir', 'outline']) when a
    stage emits several."""
    includes = _tokens(chain_includes)
    candidates = []
    for p in doc_dir.glob(f"*.{ext}"):
        result = parse(p, short_title)
        if result is None:
            continue
        _, chain, _ = result
        if not is_release(chain):
            continue
        chain_lower = [c.lower() for c in chain]
        if any(inc not in chain_lower for inc in includes):
            continue
        candidates.append(p)
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def minor_name(short_title: str, current_chain: list[str], ext: str,
               datestamp: str | None = None) -> str:
    """Minor update (focus, redline) — appends ra to the existing chain.

    A minor version keeps the source file's datestamp; only a major version
    (a fresh draft) starts a new revision cycle with today's date. Pass the
    datestamp from ``parse()``; it falls back to today only when the source
    filename could not be parsed.
    """
    chain = "_".join(current_chain + ["ra"])
    return f"{datestamp or today()}_{short_title}_{chain}.{ext}"


def find_latest(
    paper_dir: Path,
    short_title: str,
    ext: str,
    last_initials: str | None = None,
    chain_includes: str | list[str] | None = None,
    chain_excludes: str | list[str] | None = None,
) -> Path | None:
    """Newest file matching the naming convention.

    chain_includes: only files whose chain contains ALL of these elements.
    chain_excludes: skip files whose chain contains ANY of these elements.
    """
    includes = _tokens(chain_includes)
    excludes = _tokens(chain_excludes)
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
        if any(inc not in chain_lower for inc in includes):
            continue
        if any(exc in chain_lower for exc in excludes):
            continue
        candidates.append(p)
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def find_user_revision(
    paper_dir: Path,
    short_title: str,
    chain_includes: str | list[str] | None = None,
    chain_excludes: str | list[str] | None = None,
) -> Path | None:
    """Newest .docx the REVIEWER last touched — their markup, awaiting the tool's answer.

    ``chain_includes`` may name SEVERAL tokens, all of which must be present. A venue-scoped
    deliverable needs exactly that: the ISMIR outline is the file whose chain carries both
    `ismir` and `outline`, and the JASSS one sits beside it.
    """
    includes = _tokens(chain_includes)
    excludes = _tokens(chain_excludes)
    candidates = []
    for p in paper_dir.glob("*.docx"):
        result = parse(p, short_title)
        if result is None:
            continue
        _, chain, _ = result
        if not chain or chain[-1].lower() == "ra":
            continue
        # A RELEASE is nobody's turn (no `ra` in the chain at all), and its last token is a
        # deliverable word — `260714_Chords_ismir.docx` ends in "ismir", which is not "ra"
        # and is emphatically not a reviewer's initials. Without this, a venue's release
        # reads as markup on itself.
        if is_release(chain):
            continue
        chain_lower = [c.lower() for c in chain]
        if any(inc not in chain_lower for inc in includes):
            continue
        if any(exc in chain_lower for exc in excludes):
            continue
        candidates.append(p)
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)

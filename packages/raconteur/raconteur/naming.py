"""raconteur's naming binding — the shared revision chain lives in haarpi.naming.

Keeps the deliverable-specific major names (onepager, outline) as thin wrappers.

A VENUE rides in the same chain, as one more token in front of the deliverable word:

    260714_Chords_onepager_ra.docx          the narrative — shared, venue-free
    260714_Chords_ismir_outline_ra.docx     ISMIR's outline
    260714_Chords_ismir_ra_DCR.docx         ISMIR's manuscript, the reviewer's markup
    260801_Chords_jasss_outline_ra.docx     JASSS's outline, beside it
    260714_Chords_ismir.docx                ISMIR's release — nobody's turn

Nothing in the chain parser had to change for this: it already reads a filename as a bag of
tokens and treats deliverable words as chain elements by convention. So the gates, the
redline, the releases and the whole ladder multiplex per venue for free — a venue is simply
part of which document this IS.
"""

from __future__ import annotations

from pathlib import Path

from haarpi.naming import (  # noqa: F401
    DELIVERABLE_WORDS,
    find_latest,
    find_latest_release,
    find_user_revision,
    is_release,
    minor_name,
    parse,
    today,
    venue_of,
)

import haarpi.naming as _core


MANUSCRIPT = "manuscript"       # the deliverable with no word of its own in the chain


def deliverable_dir(paper_dir: Path, deliverable: str = "", venue: str = "") -> Path:
    """Where a deliverable's working chain lives.

        paper/onepager/            the narrative — venue-free
        paper/venue/               the slate and its analysis
        paper/css2026/outline/     that venue's outline
        paper/css2026/manuscript/  that venue's paper

    One folder per deliverable per venue. Flat, every deliverable and every venue shared
    one directory, so `paper/` held four generations of five documents and the eye had to
    parse a filename to know what it was looking at. The chain still carries the same
    tokens — the folders are where it lives, not what it is called.

    A venue-free manuscript (a project with no slate) lands in ``paper/manuscript``.

    There is no fallback to the flat layout: SchellingChords is the only project with a
    live raconteur phase, and the other three are migrated with it. A reader that quietly
    searched both would hide a half-migrated project instead of failing on it.
    """
    d = (deliverable or MANUSCRIPT).lower()
    v = (venue or "").lower()
    if d in ("onepager", "venue"):
        return paper_dir / d          # never venue-scoped: they precede the fork
    return paper_dir / v / d if v else paper_dir / d


def _infix(venue: str = "", deliverable: str = "") -> str:
    """The chain's middle: `<venue>_<deliverable>`, either part optional."""
    return "_".join(p for p in ((venue or "").lower(), deliverable) if p)


def major_name(short_title: str, ext: str, venue: str = "") -> str:
    """Fresh raconteur manuscript — resets chain to ra, updates the datestamp."""
    return _core.major_name(short_title, ext, infix=_infix(venue))


def major_outline_name(short_title: str, ext: str, venue: str = "") -> str:
    """Fresh outline file — chain is [venue_]outline_ra."""
    return _core.major_name(short_title, ext, infix=_infix(venue, "outline"))


def major_onepager_name(short_title: str, ext: str) -> str:
    """Fresh one-pager file — chain is onepager_ra.

    No venue: the one-pager is the paper's narrative, and the narrative belongs to the work,
    not to whoever might publish it. The venue enters at the outline.
    """
    return _core.major_name(short_title, ext, infix="onepager")


def release_name(short_title: str, ext: str, deliverable: str = "",
                 venue: str = "") -> str:
    """A gate-passed consolidation: no author tokens, but it keeps WHAT it is and WHOSE."""
    return _core.release_name(short_title, ext, infix=_infix(venue, deliverable))

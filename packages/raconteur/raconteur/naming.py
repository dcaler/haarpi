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

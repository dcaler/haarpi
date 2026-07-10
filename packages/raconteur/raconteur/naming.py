"""raconteur's naming binding — the shared revision chain lives in haarpi.naming.

Keeps the deliverable-specific major names (onepager, outline) as thin wrappers.
"""

from __future__ import annotations

from haarpi.naming import (  # noqa: F401
    find_latest,
    find_user_revision,
    minor_name,
    parse,
    today,
)

import haarpi.naming as _core


def major_name(short_title: str, ext: str) -> str:
    """Fresh raconteur file — resets chain to ra, updates date stamp."""
    return _core.major_name(short_title, ext)


def major_outline_name(short_title: str, ext: str) -> str:
    """Fresh outline file — chain is outline_ra."""
    return _core.major_name(short_title, ext, infix="outline")


def major_onepager_name(short_title: str, ext: str) -> str:
    """Fresh one-pager file — chain is onepager_ra."""
    return _core.major_name(short_title, ext, infix="onepager")

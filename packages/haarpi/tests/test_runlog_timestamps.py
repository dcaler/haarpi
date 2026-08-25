"""Every HAARPi log line carries a time.

Standing rule (author, 2026-08-25): all HAARPi code emits detailed, timestamped logs. Without a
time you cannot tell a slow run from a hung one — a three-section `graft` ran eleven hours with
unstamped progress lines and the only way to prove it was still generating was to sample
`bytes_received` on its socket to ollama.

These pin the mechanism so the rule cannot be defeated by one forgotten call.

Runnable two ways:
    pytest tests/test_runlog_timestamps.py
    python tests/test_runlog_timestamps.py
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from haarpi import runlog

_WALL = re.compile(r"^\[\d{2}:\d{2}:\d{2}\] $")


@pytest.fixture(autouse=True)
def _reset_clock():
    yield
    runlog._T0 = None


def test_stamp_is_wall_clock_and_needs_no_clock_to_be_started():
    """The failure that cost eleven hours of blind waiting: `stamp()` returned "" whenever the
    run clock was not running, so ONE missing `start()` silently un-stamped the calling command
    AND every shared helper it called. It is stateless now — there is nothing left to forget."""
    runlog._T0 = None
    assert _WALL.match(runlog.stamp()), runlog.stamp()


def test_starting_the_clock_does_not_change_the_prefix():
    """Wall clock only. The author can subtract; a per-line elapsed figure just made the format
    depend on state somebody had to remember to initialise."""
    runlog._T0 = None
    before = runlog.stamp()
    runlog.start()
    assert _WALL.match(runlog.stamp())
    assert len(runlog.stamp()) == len(before)


# ── the rule, applied to the long-running verbs ──────────────────────────────
# Interactive wizards and one-shot summary banners are deliberately exempt: a prompt is not a
# log. What must be timestamped is any verb that can run for minutes or hours, because that is
# where "is it stuck?" gets asked.

_PKGS = Path(__file__).resolve().parents[3] / "packages"
_LONG_RUNNING = [
    ("rabbithole", "summarize.py"),   # report
    ("rabbithole", "revise.py"),
    ("rabbithole", "graft.py"),
    ("rabbithole", "discover.py"),    # gather
    ("rabbithole", "mindmap.py"),
    ("rabbithole", "audit.py"),
]


@pytest.mark.parametrize("pkg,mod", _LONG_RUNNING)
def test_a_long_running_verb_timestamps_its_progress(pkg, mod):
    """Any verb that can run for minutes or hours must timestamp its progress, because that is
    where "is it stuck, and where is the time going?" gets asked."""
    src = (_PKGS / pkg / pkg / mod).read_text(encoding="utf-8")
    if "def run(" not in src:
        pytest.skip(f"{mod} has no run() entry point")
    assert "stamp()" in src, f"{mod} logs no timestamps at all"

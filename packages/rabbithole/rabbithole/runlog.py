"""Shared run-progress logging: an elapsed-time stamp for long CLI commands.

A command calls start() once at the top of its run(), then prefixes progress
lines with stamp() — e.g. `print(f"  {runlog.stamp()}Critiquing synthesis...")`.
The clock is a single process-global, which is all a one-command-per-process CLI
needs; it also means helpers shared between commands (summarize.read_notes reused
by revise) stamp correctly as soon as the caller has called start().
"""

from __future__ import annotations

import time

_T0: float | None = None


def start() -> float:
    """Begin (or restart) the run clock. Returns the start time."""
    global _T0
    _T0 = time.time()
    return _T0


def stamp() -> str:
    """Elapsed `[m:ss] ` prefix since start() ('' if the clock isn't running)."""
    if _T0 is None:
        return ""
    el = int(time.time() - _T0)
    return f"[{el // 60}:{el % 60:02d}] "


def fmt_dt(secs: float) -> str:
    """A duration as `1h 2m 3s` / `2m 3s` / `3s` (for step/total summaries)."""
    s = int(secs)
    h, r = divmod(s, 3600)
    m, s = divmod(r, 60)
    return f"{h}h {m}m {s}s" if h else (f"{m}m {s}s" if m else f"{s}s")

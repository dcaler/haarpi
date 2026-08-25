"""Shared run-progress logging for long CLI commands.

Two styles, matching how the tools grew:

  * stamp() — the `[HH:MM:SS] ` prefix on every progress line. Stateless: no clock to
    start, nothing to forget. start()/fmt_dt() remain, but only for the one-off TOTAL a
    command prints when it finishes.
  * log(msg, tool)  — one-line timestamped log with elapsed-since-import, so a
    long local-model run is legible in trundlr logs  (raster style)

The log() clock is a process-global, which is all a one-command-per-process CLI
needs. stamp() has no clock at all, so a helper shared between commands stamps
correctly no matter who called it.
"""

from __future__ import annotations

import time
from datetime import datetime

_T0: float | None = None          # start()/fmt_dt() run total (not used by stamp)
_IMPORT_T0 = time.monotonic()     # log() clock (runs from import)


def start() -> float:
    """Begin (or restart) the run clock. Returns the start time.

    Only for a TOTAL at the end of a run (``fmt_dt(time.time() - t0)``). Per-line timestamps do
    not use it — :func:`stamp` is stateless on purpose.
    """
    global _T0
    _T0 = time.time()
    return _T0


def stamp() -> str:
    """``[HH:MM:SS] `` — the wall-clock prefix every HAARPi log line carries.

    Wall clock only, and deliberately stateless. It used to be ``[m:ss @ HH:MM:SS]`` and to
    return **the empty string** whenever the run clock was not running, so one command forgetting
    ``start()`` silently un-stamped itself AND every shared helper it called: a three-section
    `graft` ran eleven hours with unstamped progress lines, and the only way to tell a slow run
    from a hung one was to sample bytes on its socket to ollama.

    The elapsed half is gone because the author can subtract (2026-08-25) — and depending on a
    clock somebody has to remember to start is exactly how the stamps went missing. Total run
    time is still worth printing ONCE at the end of a run; see :func:`start` and :func:`fmt_dt`.
    """
    return f"[{time.strftime('%H:%M:%S', time.localtime())}] "


def log(msg: str, tool: str = "haarpi") -> None:
    elapsed = time.monotonic() - _IMPORT_T0
    print(f"[{tool} {datetime.now():%H:%M:%S} +{elapsed:6.0f}s] {msg}", flush=True)


def fmt_dt(secs: float) -> str:
    """A duration as `1h 2m 3s` / `2m 3s` / `3s` (for step/total summaries)."""
    s = int(secs)
    h, r = divmod(s, 3600)
    m, s = divmod(r, 60)
    return f"{h}h {m}m {s}s" if h else (f"{m}m {s}s" if m else f"{s}s")


def fmt_secs(s: float) -> str:
    m, sec = divmod(int(s), 60)
    return f"{m}m{sec:02d}s" if m else f"{sec}s"

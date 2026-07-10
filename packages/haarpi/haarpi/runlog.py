"""Shared run-progress logging for long CLI commands.

Two styles, matching how the tools grew:

  * start()/stamp() — an explicit run clock; commands call start() at the top
    of run() and prefix progress lines with stamp()  (rabbitHole style)
  * log(msg, tool)  — one-line timestamped log with elapsed-since-import, so a
    long local-model run is legible in trundlr logs  (raster style)

Both clocks are process-globals, which is all a one-command-per-process CLI
needs; helpers shared between commands stamp correctly as soon as the caller
has called start().
"""

from __future__ import annotations

import time
from datetime import datetime

_T0: float | None = None          # start()/stamp() clock (explicit)
_IMPORT_T0 = time.monotonic()     # log() clock (runs from import)


def start() -> float:
    """Begin (or restart) the run clock. Returns the start time."""
    global _T0
    _T0 = time.time()
    return _T0


def stamp() -> str:
    """Elapsed-plus-wall-clock `[m:ss @ HH:MM:SS] ` prefix since start()
    ('' if the clock isn't running)."""
    if _T0 is None:
        return ""
    el = int(time.time() - _T0)
    now = time.strftime("%H:%M:%S", time.localtime())
    return f"[{el // 60}:{el % 60:02d} @ {now}] "


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

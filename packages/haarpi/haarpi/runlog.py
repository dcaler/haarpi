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

import io
import re
import sys
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


_ALREADY_STAMPED = re.compile(r"^\s*\[\d{2}:\d{2}:\d{2}\]")


class _LineStamper(io.TextIOBase):
    """A stdout/stderr wrapper that puts ``[HH:MM:SS] `` at the front of every line.

    The rule is that EVERY log line begins with a timestamp. Hand-stamping each ``print`` cannot
    deliver that: it is a convention, and conventions leak — one verb forgot ``runlog.start()``
    and lost stamps across eleven hours of output, and `mindmap` shipped with no stamps at all.
    Wrapping the stream makes an unstamped line impossible instead of merely discouraged, so a
    line added later by someone not thinking about logging is stamped anyway.

    Care taken:
      * only at a real line START, so ``print(..., end="")`` progress lines are not chopped up;
      * a line that already carries a stamp is left alone, so existing ``stamp()`` calls do not
        double up;
      * a bare newline stays bare — a blank spacer is formatting, and stamping it would add
        noise without adding information.
    """

    def __init__(self, wrapped):
        self._w = wrapped
        self._at_line_start = True

    def write(self, s: str) -> int:                       # noqa: D102
        if not s:
            return 0
        out = []
        for part in s.splitlines(keepends=True):
            body = part.rstrip("\r\n")
            if self._at_line_start and body.strip() and not _ALREADY_STAMPED.match(body):
                out.append(stamp())
            out.append(part)
            self._at_line_start = part.endswith(("\n", "\r"))
        self._w.write("".join(out))
        return len(s)

    # -- pass-through, so callers see a normal stream ------------------------
    def flush(self):                 return self._w.flush()
    def isatty(self):                return self._w.isatty()
    def fileno(self):                return self._w.fileno()
    @property
    def encoding(self):              return getattr(self._w, "encoding", "utf-8")
    @property
    def buffer(self):                return getattr(self._w, "buffer", None)
    def writable(self):              return True


class _Tee(io.TextIOBase):
    """Write to the real stream and to a file, so a run leaves a record that outlives it."""

    def __init__(self, wrapped, sink):
        self._w = wrapped
        self._sink = sink

    def write(self, s: str) -> int:                       # noqa: D102
        n = self._w.write(s)
        try:
            self._sink.write(s)
            self._sink.flush()
        except (OSError, ValueError):
            pass          # a full or vanished disk must never take the run down with it
        return n

    def flush(self):                 return self._w.flush()
    def isatty(self):                return self._w.isatty()
    def fileno(self):                return self._w.fileno()
    @property
    def encoding(self):              return getattr(self._w, "encoding", "utf-8")
    @property
    def buffer(self):                return getattr(self._w, "buffer", None)
    def writable(self):              return True


def to_file(root, verb: str) -> "Path | None":
    """Start teeing stamped output into ``<root>/.haarpi/runlog/<stamp>_<verb>.log``.

    `.haarpi/runlog/` is created by `haarpi init` and, until now, nothing ever wrote to it: the
    only record of a run was trundlr's `log_tail`, which keeps a few kilobytes. So the decisions
    a long verb made — which comment got which treatment, where each section anchored, what the
    corpus offered — were gone within one run of scrollback. elephantRoom's September revise
    grafted two sections and the reasoning behind both had to be reconstructed from the .docx
    afterwards, because the lines that explained them had aged out of a 5.8KB tail.

    Best-effort: a log that cannot be opened is not a reason to refuse to run.
    """
    from pathlib import Path
    try:
        d = Path(root) / ".haarpi" / "runlog"
        d.mkdir(parents=True, exist_ok=True)
        fp = d / f"{datetime.now():%y%m%d_%H%M%S}_{re.sub(r'[^A-Za-z0-9_-]', '', verb)}.log"
        sink = fp.open("a", encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return None
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "write"):
            setattr(sys, name, _Tee(stream, sink))
    return fp


def stamp_output() -> None:
    """Route stdout and stderr through the line stamper. Call once, at CLI entry.

    Idempotent, and a no-op when the stream is not a text stream (pytest's capture replaces it).
    """
    global _STAMPING
    if _STAMPING:
        return
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "write"):
            setattr(sys, name, _LineStamper(stream))
    _STAMPING = True


_STAMPING = False


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

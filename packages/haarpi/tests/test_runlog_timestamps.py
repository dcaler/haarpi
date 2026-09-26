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


# ── the rule, enforced structurally ──────────────────────────────────────────
# EVERY line in a log begins with a timestamp — no exemptions (author, 2026-08-25). Hand-stamping
# each print cannot deliver that, because it is a convention and conventions leak: one verb
# forgot `runlog.start()` and lost stamps across eleven hours, and `mindmap` shipped with none at
# all. `runlog.stamp_output()` wraps stdout/stderr at CLI entry so an unstamped line is
# impossible rather than merely discouraged, and a line added later by someone not thinking
# about logging is stamped anyway.

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


def test_the_stamper_prefixes_every_line_including_code_that_never_asked():
    """The point of wrapping the stream: prose printed by a module that knows nothing about
    runlog still comes out stamped."""
    import io as _io
    buf = _io.StringIO()
    st = runlog._LineStamper(buf)
    st.write("plain line\n")
    st.write("=" * 10 + "\n")
    st.write("multi\nline\n")
    lines = [l for l in buf.getvalue().split("\n") if l]
    assert lines and all(_WALL.match(l + " ") or l.startswith("[") for l in lines), lines
    assert all(re.match(r"^\[\d{2}:\d{2}:\d{2}\] ", l) for l in lines), lines


def test_the_stamper_leaves_blank_spacers_and_partial_lines_alone():
    import io as _io
    buf = _io.StringIO()
    st = runlog._LineStamper(buf)
    st.write("\n")                       # a blank spacer is formatting, not information
    st.write("partial ")                 # print(..., end="") must not be chopped
    st.write("continues\n")
    out = buf.getvalue()
    assert out.startswith("\n")
    assert re.search(r"^\[\d{2}:\d{2}:\d{2}\] partial continues$", out.split("\n")[1])


def test_the_stamper_does_not_double_stamp():
    import io as _io
    buf = _io.StringIO()
    st = runlog._LineStamper(buf)
    st.write(f"{runlog.stamp()}already stamped\n")
    assert buf.getvalue().count("[") == 1, buf.getvalue()


def test_the_run_log_file_is_stamped_too(tmp_path, monkeypatch):
    """The CLI installs the stamper and THEN tees to the run-log file, so the tee sat outside
    the stamper: the terminal was stamped and .haarpi/runlog/*.log was not. DigiPros' report
    log went eight minutes silent in PDF extraction with no way to tell slow from hung."""
    import io as _io
    import sys as _sys
    monkeypatch.setattr(_sys, "stdout", _io.StringIO())
    monkeypatch.setattr(_sys, "stderr", _io.StringIO())
    monkeypatch.setattr(runlog, "_STAMPING", False)
    runlog.stamp_output(heartbeat=False)                   # the CLI's order: stamper first …
    fp = runlog.to_file(tmp_path, "report")                # … then the file tee
    print("Ingesting from ./pdfs/ folder...")
    print(f"{runlog.stamp()}already stamped")
    print("partial ", end="")
    print("line")
    _sys.stdout._sink.flush()
    lines = [l for l in fp.read_text(encoding="utf-8").splitlines() if l]
    assert len(lines) == 3, lines
    assert all(re.match(r"^\[\d{2}:\d{2}:\d{2}\] \S", l) for l in lines), lines
    assert lines[1].count("[") == 1                        # not double-stamped


@pytest.mark.parametrize("pkg", ["haarpi", "rabbithole", "raconteur", "raster",
                                 "rayleigh", "razzle"])
def test_every_cli_installs_the_line_stamper(pkg):
    """One call per tool is what makes the rule hold for code nobody has written yet."""
    src = (_PKGS / pkg / pkg / "cli.py").read_text(encoding="utf-8")
    assert "stamp_output()" in src, f"{pkg}/cli.py never installs the line stamper"


# ── the silence heartbeat ────────────────────────────────────────────────────
# elephantRoom's 26 Sep build printed "Ready", then nothing for 57 minutes while it copied its
# index to the NAS. Announcing long steps is a convention; the heartbeat is the backstop.

def _busy_in_our_code(seconds: float) -> None:
    import time as _t
    _t.sleep(seconds)


def test_silence_is_reported_with_where_the_run_is(monkeypatch):
    import io as _io
    import sys as _sys
    import threading
    import time as _t
    buf = _io.StringIO()
    monkeypatch.setattr(_sys, "stdout", runlog._LineStamper(buf))
    monkeypatch.setattr(runlog, "_LAST_OUTPUT", _t.monotonic())
    stop = threading.Event()
    th = threading.Thread(target=runlog._heartbeat_loop, args=(0.3, stop), daemon=True)
    th.start()
    try:
        _busy_in_our_code(1.0)
    finally:
        stop.set()
        th.join(2)
    lines = [l for l in buf.getvalue().splitlines() if "[still working]" in l]
    assert lines, buf.getvalue()
    assert re.match(r"^\[\d{2}:\d{2}:\d{2}\] \[still working\] no output for ", lines[0])
    assert "in _busy_in_our_code()" in lines[0]


def test_output_resets_the_silence_clock(monkeypatch):
    import io as _io
    import sys as _sys
    import threading
    import time as _t
    buf = _io.StringIO()
    monkeypatch.setattr(_sys, "stdout", runlog._LineStamper(buf))
    stop = threading.Event()
    th = threading.Thread(target=runlog._heartbeat_loop, args=(0.5, stop), daemon=True)
    th.start()
    try:
        for _ in range(8):
            print("progress")
            _t.sleep(0.15)
    finally:
        stop.set()
        th.join(2)
    assert "[still working]" not in buf.getvalue()


def test_a_heartbeat_never_lands_inside_a_progress_line(monkeypatch):
    import io as _io
    import sys as _sys
    buf = _io.StringIO()
    st = runlog._LineStamper(buf)
    st.write("    [37/196] ITEM1 A paper … ")
    assert runlog._at_line_start(st) is False
    tee = runlog._Tee(st, _io.StringIO())
    assert runlog._at_line_start(tee) is False, "seen through the run-log tee too"


def test_the_heartbeat_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("HAARPI_HEARTBEAT_SECS", "0")
    monkeypatch.setattr(runlog, "_HEARTBEAT_STARTED", False)
    runlog.start_heartbeat()
    assert runlog._HEARTBEAT_STARTED is False

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


@pytest.mark.parametrize("pkg", ["haarpi", "rabbithole", "raconteur", "raster",
                                 "rayleigh", "razzle"])
def test_every_cli_installs_the_line_stamper(pkg):
    """One call per tool is what makes the rule hold for code nobody has written yet."""
    src = (_PKGS / pkg / pkg / "cli.py").read_text(encoding="utf-8")
    assert "stamp_output()" in src, f"{pkg}/cli.py never installs the line stamper"

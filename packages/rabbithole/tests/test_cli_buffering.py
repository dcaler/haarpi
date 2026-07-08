"""Progress must reach the log while the run is still going.

Python block-buffers stdout (8 KB) whenever it is not a tty. Under trundlr every rabbitHole
run redirects into logs/task-NNN.log, so a multi-hour `report` wrote an empty file — there was
no way to tell a working run from a hung one, and the only way to see anything was to kill it.

Runnable two ways:
    pytest tests/test_cli_buffering.py
    python tests/test_cli_buffering.py
"""

from __future__ import annotations

import io
import tempfile
from pathlib import Path

from rabbithole.cli import _line_buffer_output


def _file_stdout(path: Path) -> io.TextIOWrapper:
    """A stdout exactly like the one trundlr hands us: a text stream over a block-buffered
    file, not a terminal.

    `open(path, "wb")` already returns a BufferedWriter — wrapping it in another one makes
    TextIOWrapper.flush() reach only the inner buffer, and the file stays empty however the
    stream is configured. That is a property of the test harness, not of the code under test.
    """
    return io.TextIOWrapper(open(path, "wb"), line_buffering=False)


def test_a_file_stdout_is_block_buffered_by_default():
    with tempfile.TemporaryDirectory() as t:
        p = Path(t) / "task.log"
        out = _file_stdout(p)
        assert out.line_buffering is False
        print("banner", file=out)
        assert p.read_bytes() == b"", "the premise: nothing reaches the file"
        out.close()


def test_line_buffering_makes_each_line_land_immediately(monkeypatch):
    with tempfile.TemporaryDirectory() as t:
        p = Path(t) / "task.log"
        out = _file_stdout(p)
        monkeypatch.setattr("sys.stdout", out)
        monkeypatch.setattr("sys.stderr", out)

        _line_buffer_output()
        assert out.line_buffering is True

        print("Planning sections from 83 sources", file=out)
        assert b"Planning sections" in p.read_bytes(), "line must be visible mid-run"
        out.close()


def test_a_tty_like_stream_is_untouched(monkeypatch):
    """Interactive runs already line-buffer; the call must be a no-op, not a downgrade."""
    with tempfile.TemporaryDirectory() as t:
        out = _file_stdout(Path(t) / "x.log")
        out.reconfigure(line_buffering=True)
        monkeypatch.setattr("sys.stdout", out)
        monkeypatch.setattr("sys.stderr", out)
        _line_buffer_output()
        assert out.line_buffering is True
        out.close()


def test_a_stream_without_reconfigure_is_survived(monkeypatch):
    """pytest's captured stdout, a StringIO in a test, a closed stream — none may raise."""
    class _NoReconfigure(io.StringIO):
        reconfigure = None    # attribute exists but is not callable -> TypeError, not caught

    class _Bare:
        def write(self, s): return len(s)
        def flush(self): pass

    monkeypatch.setattr("sys.stdout", _Bare())
    monkeypatch.setattr("sys.stderr", io.StringIO())
    _line_buffer_output()      # must not raise


def test_a_detached_stream_is_survived(monkeypatch):
    """reconfigure() on a closed/detached stream raises ValueError."""
    out = io.TextIOWrapper(io.BytesIO())
    out.detach()
    monkeypatch.setattr("sys.stdout", out)
    monkeypatch.setattr("sys.stderr", io.StringIO())
    _line_buffer_output()      # must not raise


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))

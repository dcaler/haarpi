"""rabbitHole's own ollama client against the shared fake Ollama: it must survive the GPU
watchdog stopping ollama, and must never return a cut-off stream as a whole answer."""

import pytest

from haarpi import brain as hbrain
from packages.haarpi.tests.test_brain import _serve, _short_sleep, late_server  # noqa: F401
from rabbithole.brain import Brain
from rabbithole.config import BrainConfig, GlobalConfig


@pytest.fixture()
def server(monkeypatch):
    monkeypatch.setattr(hbrain, "OUTAGE_POLL_SECS", 0.05)
    monkeypatch.setenv("HAARPI_OLLAMA_OUTAGE_WAIT", "5")
    monkeypatch.setattr(hbrain.time, "sleep", _short_sleep)
    srv = _serve()
    yield srv
    srv.shutdown()


def _brain(srv, **cfg) -> Brain:
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    return Brain(BrainConfig(**cfg), GlobalConfig(ollama_url=url))


def test_a_stream_that_ends_before_done_is_resent_not_returned(server):
    server.truncate_fails = 1
    assert _brain(server, worker_model="truncate").worker("p") == "Hello"
    assert len(server.requests) == 2 and "/api/version" in server.probes


def test_with_the_wait_off_a_cut_stream_still_never_comes_back_as_an_answer(server,
                                                                            monkeypatch):
    monkeypatch.setenv("HAARPI_OLLAMA_OUTAGE_WAIT", "0")
    server.truncate_fails = 99
    with pytest.raises(RuntimeError, match="before done"):
        _brain(server, worker_model="truncate").worker("p")
    assert len(server.requests) == 3                  # today's three retries, no partial text


def test_http_500_keeps_the_three_retries_and_never_waits(server):
    server.flaky_fails = 99
    with pytest.raises(RuntimeError, match="after 3 attempts"):
        _brain(server, worker_model="flaky").worker("p")
    assert len(server.requests) == 3 and server.probes == []


def test_embed_waits_out_a_stopped_ollama(late_server, monkeypatch):
    """An outage during indexing must pause the index, not fill it with [] vectors."""
    monkeypatch.setattr(hbrain, "OUTAGE_POLL_SECS", 0.05)
    monkeypatch.setenv("HAARPI_OLLAMA_OUTAGE_WAIT", "5")
    monkeypatch.setattr(hbrain.time, "sleep", _short_sleep)
    url, start, _box = late_server
    start(0.4)
    b = Brain(BrainConfig(embed_model="e"), GlobalConfig(ollama_url=url))
    assert b.embed_batch(["one", "two"]) == [[0.1, 0.2, 0.3]] * 2

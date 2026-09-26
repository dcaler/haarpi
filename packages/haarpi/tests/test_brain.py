"""The offline brain against a fake streaming-NDJSON Ollama server.

This is the safety net the brain convergence requires: stream assembly, think
handling, num_ctx policy, retries, worker_map ordering, and the embedding
shrink-on-overflow loop are exercised against a real HTTP server speaking
Ollama's wire format — no network, no models.
"""

import json
import socket
import threading
import time
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from haarpi import brain


class FakeOllama(BaseHTTPRequestHandler):
    """Speaks just enough /api/chat + /api/embeddings.

    Steering (via request content):
      * model "err"            -> NDJSON error object
      * prompt contains RAISE  -> NDJSON error object
      * model "flaky"          -> HTTP 500 for the first N requests (server.flaky_fails)
      * model "truncate"       -> a stream with no `done` line for the first N requests
                                  (server.truncate_fails), as when ollama is killed mid-answer
      * embeddings model "shrink" -> HTTP 500 "context length" until prompt <= 40 chars
    GET /api/version answers, and is recorded in server.probes.
    """

    def log_message(self, *a):  # keep test output clean
        pass

    def do_GET(self):
        self.server.probes.append(self.path)
        if self.path == "/api/version":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"version": "0.0.0"}')
            return
        self.send_response(404)
        self.end_headers()

    def _read(self) -> dict:
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n))

    def _ndjson(self, objs: list[dict]) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        for o in objs:
            self.wfile.write(json.dumps(o).encode() + b"\n")

    def do_POST(self):
        payload = self._read()
        self.server.requests.append((self.path, payload))

        if self.path == "/api/chat":
            prompt = "".join(m.get("content", "") for m in payload["messages"])
            if payload["model"] == "flaky" and self.server.flaky_fails > 0:
                self.server.flaky_fails -= 1
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b"boom")
                return
            if payload["model"] == "truncate" and self.server.truncate_fails > 0:
                self.server.truncate_fails -= 1
                self._ndjson([{"message": {"content": "Hel"}}])   # socket closes, no done
                self.close_connection = True
                return
            if payload["model"] == "err" or "RAISE" in prompt:
                self._ndjson([{"error": "kaput"}])
                return
            self._ndjson([
                {"message": {"thinking": "hmm"}},
                {"message": {"content": "Hel"}},
                {"message": {"content": "lo"}},
                {"done": True, "eval_count": 5, "eval_duration": int(1e9),
                 "prompt_eval_count": 10},
            ])
            return

        if self.path == "/api/embeddings":
            if payload["model"] == "shrink" and len(payload["prompt"]) > 40:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b"context length exceeded")
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"embedding": [0.1, 0.2, 0.3]}).encode())
            return

        self.send_response(404)
        self.end_headers()


def _serve(port: int = 0) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", port), FakeOllama)
    srv.requests = []
    srv.probes = []
    srv.flaky_fails = 0
    srv.truncate_fails = 0
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture()
def server():
    srv = _serve()
    yield srv
    srv.shutdown()


@pytest.fixture()
def fast_outages(monkeypatch):
    """Outage waits short enough for a test; the wait itself stays on (the default)."""
    monkeypatch.setattr(brain, "OUTAGE_POLL_SECS", 0.05)
    monkeypatch.setenv("HAARPI_OLLAMA_OUTAGE_WAIT", "5")
    monkeypatch.setattr(brain.time, "sleep", _short_sleep)


_real_sleep = __import__("time").sleep


def _short_sleep(s):          # retry backoffs vanish, the outage poll keeps its 0.05 s
    _real_sleep(min(s, 0.05))


@pytest.fixture()
def late_server():
    """A port where nothing listens yet. start(delay) brings ollama up on it after `delay`
    seconds — the watchdog's stop-then-restart, compressed."""
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    box = {}

    def start(delay: float):
        def go():
            _real_sleep(delay)
            box["srv"] = _serve(port)
        threading.Thread(target=go, daemon=True).start()

    yield f"http://127.0.0.1:{port}", start, box
    if "srv" in box:
        box["srv"].shutdown()


@pytest.fixture()
def url(server):
    return f"http://127.0.0.1:{server.server_address[1]}"


def _msgs(prompt="hi"):
    return [{"role": "user", "content": prompt}]


def test_chat_assembles_the_stream(server, url):
    assert brain.chat(url, "m", _msgs()) == "Hello"


def test_think_tristate(server, url):
    brain.chat(url, "m", _msgs())                      # None -> key omitted
    brain.chat(url, "m", _msgs(), think=False)         # False -> present
    brain.chat(url, "m", _msgs(), think=True)          # True  -> present
    payloads = [p for _, p in server.requests]
    assert "think" not in payloads[0]
    assert payloads[1]["think"] is False
    assert payloads[2]["think"] is True


def test_num_ctx_auto_sizes_to_the_prompt(server, url):
    small = "x" * 100
    big = "x" * (brain.MIN_NUM_CTX * 2 * brain.CHARS_PER_TOKEN)
    brain.chat(url, "m", _msgs(small))
    brain.chat(url, "m", _msgs(big))
    ctxs = [p["options"]["num_ctx"] for _, p in server.requests]
    assert ctxs[0] == brain.pick_num_ctx(len(small))
    assert ctxs[1] == brain.pick_num_ctx(len(big)) > ctxs[0]


def test_explicit_num_ctx_is_honored_when_the_prompt_fits(server, url, capsys):
    brain.chat(url, "m", _msgs("x" * 400), num_ctx=4096)
    assert server.requests[-1][1]["options"]["num_ctx"] == 4096
    assert "DISCARD" not in capsys.readouterr().err


def test_explicit_num_ctx_GROWS_rather_than_discarding_the_head(server, url, capsys):
    """An explicit num_ctx is the caller's estimate of the prompt, not permission to throw
    evidence away when the estimate is wrong. rabbitHole's audit pinned 2048 for a one-line
    focus string; when the question grew to the author's full research prompt, all 154
    judgements were made against a question Ollama had discarded off the front."""
    prompt = "x" * (8192 * brain.CHARS_PER_TOKEN)   # ~8k tokens into a 4k window
    brain.chat(url, "m", _msgs(prompt), num_ctx=4096)
    sent = server.requests[-1][1]["options"]["num_ctx"]
    assert sent >= brain.estimate_tokens(len(prompt))   # the whole prompt survives
    assert sent == brain.pick_num_ctx(len(prompt))
    assert "DISCARD" not in capsys.readouterr().err


def test_warns_only_when_even_the_cap_cannot_hold_the_prompt(server, url, capsys):
    prompt = "x" * (30000 * brain.CHARS_PER_TOKEN)   # past MAX_NUM_CTX's usable budget
    brain.chat(url, "m", _msgs(prompt), num_ctx=4096)
    assert server.requests[-1][1]["options"]["num_ctx"] == brain.MAX_NUM_CTX
    assert "DISCARD" in capsys.readouterr().err       # nothing left to do but say so


def test_ollama_error_raises(server, url):
    with pytest.raises(RuntimeError, match="kaput"):
        brain.chat(url, "err", _msgs())
    assert len(server.requests) == 1     # retries=0: exactly one request


def test_retries_recover_from_transient_500(server, url, monkeypatch):
    monkeypatch.setattr(brain.time, "sleep", lambda s: None)
    server.flaky_fails = 1
    assert brain.chat(url, "flaky", _msgs(), retries=1) == "Hello"
    assert len(server.requests) == 2

    server.requests.clear()
    server.flaky_fails = 1
    with pytest.raises(Exception):
        brain.chat(url, "flaky", _msgs(), retries=0)
    assert len(server.requests) == 1     # raster philosophy: no blind resend


def test_worker_map_preserves_order_and_absorbs_failures(server, url, monkeypatch):
    monkeypatch.setattr(brain.time, "sleep", lambda s: None)
    b = brain.Brain(url, "coord", "worker", worker_parallel=2)
    out = b.worker_map([("", "one"), ("", "RAISE"), ("", "three")])
    assert out == ["Hello", "", "Hello"]


def test_brain_roles_use_their_models_and_temps(server, url):
    b = brain.Brain(url, "coord", "worker",
                    coordinator_temperature=0.4, worker_temperature=0.1)
    b.coordinator("p")
    b.worker("p")
    (_, coord), (_, work) = server.requests
    assert coord["model"] == "coord" and coord["options"]["temperature"] == 0.4
    assert work["model"] == "worker" and work["options"]["temperature"] == 0.1


def test_instance_think_flows_to_both_roles(server, url):
    b = brain.Brain(url, "coord", "worker", think=False)
    b.coordinator("p")
    b.worker("p")
    assert all(p["think"] is False for _, p in server.requests)
    b.coordinator("p", think=True)       # per-call override wins
    assert server.requests[-1][1]["think"] is True


def test_embed_happy_path(server, url):
    b = brain.Brain(url, "c", "w", embed_model="e")
    assert b.embed("some text") == [0.1, 0.2, 0.3]


def test_embed_shrinks_on_context_overflow(server, url):
    b = brain.Brain(url, "c", "w", embed_model="shrink")
    assert b.embed("y" * 200) == [0.1, 0.2, 0.3]
    sent = [p["prompt"] for path, p in server.requests if path == "/api/embeddings"]
    assert len(sent) > 1 and len(sent[-1]) <= 40   # halved until it fit


def test_embed_empty_raises(server, url):
    with pytest.raises(ValueError):
        brain.Brain(url, "c", "w", embed_model="e").embed("   ")


# ── ollama outages (oddjob's GPU watchdog stops ollama at 82 °C) ────────────────

def test_a_stream_that_ends_before_done_is_not_returned_as_an_answer(server, url, monkeypatch):
    """Killed mid-generation, ollama can close the socket cleanly. The fragment that
    arrived must not come back as if it were the model's whole answer."""
    monkeypatch.setenv("HAARPI_OLLAMA_OUTAGE_WAIT", "0")
    server.truncate_fails = 1
    with pytest.raises(brain.OllamaOutage, match="before done"):
        brain.chat(url, "truncate", _msgs())
    assert len(server.requests) == 1


def test_a_cut_stream_is_resent_once_ollama_answers(server, url, fast_outages):
    server.truncate_fails = 1
    assert brain.chat(url, "truncate", _msgs()) == "Hello"      # retries=0: no retry spent
    assert len(server.requests) == 2
    assert "/api/version" in server.probes


def test_a_call_waits_for_a_stopped_ollama_and_then_succeeds(late_server, fast_outages, capsys):
    url, start, box = late_server
    start(0.4)
    assert brain.chat(url, "m", _msgs()) == "Hello"             # retries=0, yet it survives
    assert len(box["srv"].requests) == 1                        # sent once ollama was back
    out = capsys.readouterr().out
    assert "[ollama unreachable]" in out and "[ollama back]" in out


def test_http_500_keeps_todays_retry_policy_and_never_waits(server, url, fast_outages):
    """A running ollama that answers 500 has said something about the request."""
    server.flaky_fails = 1
    with pytest.raises(urllib.error.HTTPError):
        brain.chat(url, "flaky", _msgs(), retries=0)
    assert len(server.requests) == 1 and server.probes == []


def test_an_ndjson_error_is_not_an_outage(server, url, fast_outages):
    with pytest.raises(RuntimeError, match="kaput"):
        brain.chat(url, "err", _msgs())
    assert server.probes == []


def test_outage_wait_zero_fails_as_before(late_server, monkeypatch):
    monkeypatch.setenv("HAARPI_OLLAMA_OUTAGE_WAIT", "0")
    url, _start, _box = late_server                             # never started
    t0 = time.monotonic()
    with pytest.raises(urllib.error.URLError):
        brain.chat(url, "m", _msgs())
    assert time.monotonic() - t0 < 2


def test_outages_per_call_are_capped(server, url, fast_outages, monkeypatch):
    monkeypatch.setattr(brain, "MAX_OUTAGES_PER_CALL", 2)
    server.truncate_fails = 99                                  # dies mid-answer every time
    with pytest.raises(brain.OllamaOutage):
        brain.chat(url, "truncate", _msgs())
    assert len(server.requests) == 3                            # first try + 2 waited resends


def test_a_server_that_never_returns_gives_up_at_the_deadline(late_server, fast_outages,
                                                              monkeypatch, capsys):
    monkeypatch.setenv("HAARPI_OLLAMA_OUTAGE_WAIT", "1")
    url, _start, _box = late_server                             # never started
    with pytest.raises(urllib.error.URLError):
        brain.chat(url, "m", _msgs())
    assert "[ollama still unreachable]" in capsys.readouterr().out


def test_embedding_waits_out_an_outage_instead_of_storing_an_empty_vector(late_server,
                                                                          fast_outages):
    """embed_batch turns a failed embed into [] (similarity 0). During an outage that would
    quietly empty the index, so the embed waits instead."""
    url, start, _box = late_server
    start(0.4)
    b = brain.Brain(url, "c", "w", embed_model="e")
    assert b.embed_batch(["one", "two"]) == [[0.1, 0.2, 0.3]] * 2


def test_is_outage_classification():
    import http.client
    assert brain.is_outage(ConnectionRefusedError())
    assert brain.is_outage(urllib.error.URLError(ConnectionRefusedError()))
    assert brain.is_outage(http.client.RemoteDisconnected())
    assert brain.is_outage(http.client.IncompleteRead(b""))
    assert brain.is_outage(brain.OllamaOutage("x"))
    assert not brain.is_outage(TimeoutError())                  # per-chunk read gap
    assert not brain.is_outage(urllib.error.URLError(TimeoutError()))
    assert not brain.is_outage(urllib.error.HTTPError("u", 500, "boom", {}, None))
    assert not brain.is_outage(RuntimeError("ollama error: kaput"))

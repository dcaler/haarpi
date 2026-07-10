"""The offline brain against a fake streaming-NDJSON Ollama server.

This is the safety net the brain convergence requires: stream assembly, think
handling, num_ctx policy, retries, worker_map ordering, and the embedding
shrink-on-overflow loop are exercised against a real HTTP server speaking
Ollama's wire format — no network, no models.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from haarpi import brain


class FakeOllama(BaseHTTPRequestHandler):
    """Speaks just enough /api/chat + /api/embeddings.

    Steering (via request content):
      * model "err"            -> NDJSON error object
      * prompt contains RAISE  -> NDJSON error object
      * model "flaky"          -> HTTP 500 for the first N requests (server.flaky_fails)
      * embeddings model "shrink" -> HTTP 500 "context length" until prompt <= 40 chars
    """

    def log_message(self, *a):  # keep test output clean
        pass

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


@pytest.fixture()
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
    srv.requests = []
    srv.flaky_fails = 0
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()


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


def test_explicit_num_ctx_is_honored_and_warns_on_overflow(server, url, capsys):
    prompt = "x" * (8192 * brain.CHARS_PER_TOKEN)   # ~8k tokens into a 4k window
    brain.chat(url, "m", _msgs(prompt), num_ctx=4096)
    assert server.requests[-1][1]["options"]["num_ctx"] == 4096
    assert "DISCARD" in capsys.readouterr().err


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

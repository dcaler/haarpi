"""End-to-end pipeline verbs against fake trundlr + fake Ollama servers.

init queues the opening chain; a clean markup mints a release and advances the
DAG; a dirty markup is classified and queued as a self-feeding rework chain;
the loop guard refuses to plan one annotation set twice.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from haarpi import planner, project
from test_release_gate import _make_markup


class FakeTrundlr(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/projects/"):
            self._json(self.server.projects)
        elif self.path.startswith("/api/tasks/"):
            self._json(self.server.tasks)
        else:
            self._json({}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n))
        if self.path.startswith("/api/projects/"):
            body["id"] = len(self.server.projects) + 1
            self.server.projects.append(body)
        else:
            body["id"] = len(self.server.tasks) + 1
            body.setdefault("status", "todo")
            self.server.tasks.append(body)
        self._json(body)

    def do_PATCH(self):
        self.send_response(204)
        self.end_headers()


class FakePlannerOllama(BaseHTTPRequestHandler):
    """Streams one canned JSON classification (server.reply)."""

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        self.rfile.read(n)
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        self.wfile.write(json.dumps(
            {"message": {"content": self.server.reply}}).encode() + b"\n")
        self.wfile.write(json.dumps({"done": True, "eval_count": 1,
                                     "eval_duration": 1}).encode() + b"\n")


@pytest.fixture()
def servers(tmp_path, monkeypatch):
    tr = ThreadingHTTPServer(("127.0.0.1", 0), FakeTrundlr)
    tr.projects, tr.tasks = [], []
    ol = ThreadingHTTPServer(("127.0.0.1", 0), FakePlannerOllama)
    ol.reply = json.dumps({"tier": "gap_fill", "assessment": "needs more on X",
                           "gather_topics": ["X"]})
    for s in (tr, ol):
        threading.Thread(target=s.serve_forever, daemon=True).start()

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    cfgdir = tmp_path / "cfg" / "haarpi"
    cfgdir.mkdir(parents=True)
    (cfgdir / "config.toml").write_text(f"""
[ollama]
url = "http://127.0.0.1:{ol.server_address[1]}"
coordinator = "m"
worker = "w"

[trundlr]
url = "http://127.0.0.1:{tr.server_address[1]}"
gpu_resource = 2
cpu_resource = 3
human_resource = 1
""")
    yield tr, ol
    tr.shutdown()
    ol.shutdown()


@pytest.fixture()
def proj(tmp_path, servers):
    root = tmp_path / "260812_myproject"
    root.mkdir()
    rc = planner.run_init(root, name="myproject", short_title="myproj",
                          brief="test brief", initials="DCR")
    assert rc == 0
    return root


def test_init_writes_manifest_scaffold_and_opening_chain(proj, servers):
    tr, _ = servers
    m = project.load_manifest(proj)
    assert m.trundlr_project_id == 1 and m.short_title == "myproj"
    assert (proj / "litReview" / "output").is_dir()
    assert (proj / ".haarpi" / "plans").is_dir()
    titles = [t["title"] for t in tr.tasks]
    assert titles == ["litreview gather 1", "litreview collect 1",
                      "litreview report 1", "litreview comment 1",
                      "litreview next 1"]
    # chained, umbrella-form commands, human steps command-less
    assert tr.tasks[1]["depends_on_id"] == tr.tasks[0]["id"]
    assert tr.tasks[0]["command"].startswith("haarpi rabbithole")
    assert "command" not in tr.tasks[3]
    assert tr.tasks[4]["command"] == "haarpi next"


def test_clean_markup_mints_release_and_advances(proj, servers):
    tr, _ = servers
    m = project.load_manifest(proj)
    out = m.output_dir(proj, "litreview")
    markup = out / "260710_myproj_litreview_ra_DCR.docx"
    _make_markup(markup, resolved=True, tracked=True)

    assert planner.run_next(proj) == 0

    releases = [p.name for p in out.glob("*.docx")]
    assert len(releases) == 1 and "_ra" not in releases[0]      # bare-chain release
    assert (out.parent / "archive").is_dir()                     # spent chain archived
    entries = project.list_plans(proj)
    assert any(e["type"] == "gate" for e in entries)
    # build became unlocked -> attended stage opened as a human design-session task
    assert any(t["title"] == "build design session" for t in tr.tasks)
    assert project.latest_release(proj, m, "litreview") is not None


def test_dirty_markup_classifies_queues_and_loop_guards(proj, servers):
    tr, _ = servers
    m = project.load_manifest(proj)
    out = m.output_dir(proj, "litreview")
    markup = out / "260710_myproj_litreview_ra_DCR.docx"
    _make_markup(markup, resolved=False)                         # unresolved ask

    before = len(tr.tasks)
    assert planner.run_next(proj) == 0
    new = [t["title"] for t in tr.tasks[before:]]
    assert new == ["litreview gather 2", "litreview collect 2", "litreview revise 2",
                   "litreview comment 2", "litreview next 2"]    # gap_fill chain, cycle 2
    entry = [e for e in project.list_plans(proj) if e.get("type") == "plan"][-1]
    assert entry["tier"] == "gap_fill" and entry["annotation_hash"]

    # the loop guard: same annotation set is never planned twice
    before = len(tr.tasks)
    assert planner.run_next(proj) == 0
    assert len(tr.tasks) == before


def test_status_reports_stage_states(proj, capsys):
    capsys.readouterr()
    assert planner.run_status(proj) == 0
    outp = capsys.readouterr().out
    assert "litreview" in outp and "open" in outp
    assert "waiting" in outp                                     # downstream stages gated

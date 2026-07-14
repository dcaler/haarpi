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

from haarpi import planner, project, trundlr
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
                          brief="test brief", initials="DCR", priority=2)
    assert rc == 0
    return root


def test_init_writes_manifest_scaffold_and_opening_chain(proj, servers):
    tr, _ = servers
    m = project.load_manifest(proj)
    assert m.trundlr_project_id == 1 and m.short_title == "myproj"
    # the answered priority reaches trundlr and is remembered for a later `queue`
    assert tr.projects[0]["priority"] == 2 and m.trundlr_priority == 2
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


def test_init_asks_priority_and_defaults_to_trundlrs_own_band(tmp_path, servers,
                                                              monkeypatch):
    """Unanswered, a new project takes trundlr's default band — it does not barge in
    at priority 1 ahead of everything already queued."""
    tr, _ = servers
    root = tmp_path / "260814_asked"
    root.mkdir()
    asked: list[str] = []

    def fake_input(prompt=""):
        asked.append(prompt)
        return ""            # accept every offered default

    monkeypatch.setattr("builtins.input", fake_input)
    assert planner.run_init(root, name="asked", short_title="asked",
                            brief="b", initials="DCR") == 0
    assert any("priority" in p for p in asked)
    assert tr.projects[0]["priority"] == trundlr.PRIORITY_DEFAULT
    assert project.load_manifest(root).trundlr_priority == trundlr.PRIORITY_DEFAULT


def test_init_clamps_a_priority_trundlr_would_reject(tmp_path, servers):
    """Trundlr accepts 1..4. A typo lands in range instead of failing the init."""
    tr, _ = servers
    root = tmp_path / "260814_clamped"
    root.mkdir()
    assert planner.run_init(root, name="clamped", short_title="clamped", brief="b",
                            initials="DCR", priority=9) == 0
    assert tr.projects[0]["priority"] == trundlr.PRIORITY_MAX


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


def test_redirection_inserts_approval_gate(proj, servers):
    tr, ol = servers
    ol.reply = json.dumps({"tier": "redirection", "assessment": "wrong direction",
                           "gather_topics": ["Y"]})
    m = project.load_manifest(proj)
    markup = m.output_dir(proj, "litreview") / "260710_myproj_litreview_ra_DCR.docx"
    _make_markup(markup, resolved=False)

    before = len(tr.tasks)
    assert planner.run_next(proj) == 0
    new = tr.tasks[before:]
    assert new[0]["title"].startswith("litreview approve")     # confirm_tiers head
    assert "command" not in new[0]                             # human, command-less
    assert new[1]["depends_on_id"] == new[0]["id"]             # chain gated behind it
    assert "gather topics: Y" in new[0]["description"]         # the human reads the plan


def test_paper_markup_escalates_upstream_literature(proj, servers):
    tr, ol = servers
    ol.reply = json.dumps({"tier": "upstream_literature",
                           "assessment": "claims need citation support",
                           "gather_topics": ["Schelling dynamics"]})
    m = project.load_manifest(proj)
    markup = m.output_dir(proj, "paper") / "260710_myproj_ra_DCR.docx"
    _make_markup(markup, resolved=False)

    before = len(tr.tasks)
    assert planner.run_next(proj) == 0
    steps = [t["title"].rsplit(" ", 1)[0] for t in tr.tasks[before:]]
    assert steps == ["litreview gather", "litreview collect", "litreview report",
                     "litreview comment", "paper next"]        # cross-stage chain
    assert tr.tasks[before]["command"].startswith("haarpi rabbithole")


def test_release_refreshes_idle_downstream_stage(proj, servers):
    tr, _ = servers
    m = project.load_manifest(proj)
    # paper already produced a release earlier (idle, no in-flight work)
    paper_rel = m.output_dir(proj, "paper") / "260709_myproj.docx"
    _make_markup(paper_rel, resolved=True)
    from haarpi import redline
    redline.mint_release(paper_rel, paper_rel, md_sibling=False)  # normalize in place

    markup = m.output_dir(proj, "litreview") / "260710_myproj_litreview_ra_DCR.docx"
    _make_markup(markup, resolved=True)
    before = len(tr.tasks)
    assert planner.run_next(proj) == 0                          # mints litreview release

    titles = [t["title"] for t in tr.tasks[before:]]
    assert any(t.startswith("paper revise") for t in titles)    # staleness re-fired paper
    entry = [e for e in project.list_plans(proj) if e.get("type") == "refresh"][-1]
    assert entry["stage"] == "paper" and entry["bindings"].get("litreview")


def test_experiments_extend_escalates_to_attended_review(proj, servers):
    tr, ol = servers
    ol.reply = json.dumps({"tier": "extend", "assessment": "needs more seeds"})
    m = project.load_manifest(proj)
    markup = m.output_dir(proj, "experiments") / "260710_myproj_results_ra_DCR.docx"
    _make_markup(markup, resolved=False)

    before = len(tr.tasks)
    assert planner.run_next(proj) == 0
    new = tr.tasks[before:]
    assert new[0]["title"].startswith("experiments review_session")
    assert "command" not in new[0]                              # attended session, yours
    assert "rayleigh review" in new[0]["description"]


def test_run_queue_registers_and_queues_for_late_trundlr(tmp_path, servers):
    tr, _ = servers
    root = tmp_path / "260813_other"
    root.mkdir()
    planner.run_init(root, name="other", short_title="other", brief="b",
                     initials="DCR", priority=1, no_trundlr=True)
    assert project.load_manifest(root).trundlr_project_id is None
    assert planner.run_queue(root) == 0
    m = project.load_manifest(root)
    assert m.trundlr_project_id is not None
    # deferred registration still opens the project in the band init answered
    created = [p for p in tr.projects if p["name"] == "other"]
    assert created and created[0]["priority"] == 1
    titles = [t["title"] for t in tr.tasks if t.get("project_id") == m.trundlr_project_id]
    assert titles[0].startswith("litreview gather")
    assert planner.run_queue(root) == 0                         # idempotent: nothing doubled
    assert len([t for t in tr.tasks
                if t.get("project_id") == m.trundlr_project_id]) == 5

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
from packages.haarpi.tests.test_release_gate import _make_markup


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
    assert titles == ["rabbithole gather 1", "rabbithole collect 1",
                      "rabbithole report 1", "rabbithole mindmap 1", "rabbithole comment 1",
                      "rabbithole next 1"]
    # chained, umbrella-form commands, human steps command-less
    assert tr.tasks[1]["depends_on_id"] == tr.tasks[0]["id"]
    assert tr.tasks[0]["command"].startswith("haarpi rabbithole")
    assert tr.tasks[3]["command"] == "haarpi rabbithole mindmap"   # the per-draft map, on the runner
    assert "command" not in tr.tasks[4]                            # comment is the human step
    assert tr.tasks[5]["command"] == "haarpi next"


def test_init_does_not_publish_runnable_work_before_the_project_exists(
        tmp_path, servers, monkeypatch):
    """A queued `rabbithole gather` is claimable the instant it is POSTed.

    init used to queue the chain BEFORE scaffolding, so a runner idle at that moment took the
    gather and died on "No litrev.yaml found in litReview" one second later — and, unable to
    record a failure that finished before its own scheduled start, held the task in_progress and
    stopped every other task on its resource (humanTraject, 2026-08-31). Registering the trundlr
    PROJECT first is fine; it creates no runnable work.
    """
    root = tmp_path / "260831_racy"
    root.mkdir()
    seen = {}
    real = planner.queue_chain

    def spy(*a, **kw):
        seen["litrev"] = (root / "litReview" / "litrev.yaml").is_file()
        seen["manifest"] = (root / project.MANIFEST).is_file()
        seen["output_dir"] = (root / "litReview" / "output").is_dir()
        return real(*a, **kw)

    monkeypatch.setattr(planner, "queue_chain", spy)
    assert planner.run_init(root, name="racy", short_title="racy", brief="b",
                            initials="DCR", priority=2) == 0
    assert seen["litrev"], "the gather was queued before litrev.yaml existed"
    assert seen["manifest"], "the gather was queued before haarpi.yaml existed"
    assert seen["output_dir"], "the gather was queued before its output dir existed"


def test_init_still_records_the_trundlr_id_it_registered_before_scaffolding(tmp_path, servers):
    """The manifest is saved after the id is known, so splitting register from queue must not
    lose it."""
    root = tmp_path / "260831_ided"
    root.mkdir()
    assert planner.run_init(root, name="ided", short_title="ided", brief="b",
                            initials="DCR", priority=2) == 0
    assert project.load_manifest(root).trundlr_project_id == 1


def test_init_that_cannot_reach_trundlr_still_scaffolds_the_project(tmp_path, servers,
                                                                    monkeypatch):
    """A queue failure must not cost the project its directories: the scaffold now happens
    before the queueing, so an unreachable server leaves a usable project behind."""
    root = tmp_path / "260831_offline"
    root.mkdir()

    def boom(*a, **kw):
        raise trundlr.TrundlrError("connection refused")

    monkeypatch.setattr(planner, "queue_chain", boom)
    assert planner.run_init(root, name="offline", short_title="offline", brief="b",
                            initials="DCR", priority=2) == 0
    assert (root / "litReview" / "litrev.yaml").is_file()
    assert (root / "litReview" / "output").is_dir()
    assert (root / ".haarpi" / "plans").is_dir()


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


def test_init_reads_a_pasted_multiparagraph_brief_without_spilling(
        tmp_path, servers, monkeypatch):
    """A pasted multi-line brief is consumed whole — every line up to the `.`/EOF
    terminator, blank paragraph breaks included — so its later paragraphs never fall
    through into the initials/priority prompts. This is the paste corruption that once
    split a brief across `brief` and `initials`."""
    tr, _ = servers
    root = tmp_path / "260801_pasted"
    root.mkdir()
    # brief lines (with a paragraph break) → sentinel → initials → priority(default)
    feed = iter(["para one of the brief", "", "para two of the brief", ".",
                 "JQ", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(feed))
    assert planner.run_init(root, name="pasted", short_title="pasted") == 0
    m = project.load_manifest(root)
    assert m.brief == "para one of the brief\n\npara two of the brief"
    assert m.initials == "JQ"          # the 2nd paragraph did NOT leak here


def test_init_multiline_brief_ends_on_eof(tmp_path, servers, monkeypatch):
    """Ctrl-D (EOF) is an equally valid terminator for the pasted brief."""
    tr, _ = servers
    root = tmp_path / "260801_eof"
    root.mkdir()
    calls = iter(["only paragraph"])

    def fake_input(prompt=""):
        try:
            return next(calls)
        except StopIteration:
            raise EOFError
    monkeypatch.setattr("builtins.input", fake_input)
    assert planner.run_init(root, name="eof", short_title="eof",
                            initials="DCR", priority=2) == 0
    assert project.load_manifest(root).brief == "only paragraph"


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
    # design (prereg) became unlocked -> attended session task; build stays locked (needs design)
    assert any(t["title"] == "rayleigh design session" for t in tr.tasks)
    assert not any(t["title"] == "raster design session" for t in tr.tasks)
    assert project.latest_release(proj, m, "litreview") is not None


def test_dirty_markup_classifies_queues_and_loop_guards(proj, servers):
    tr, ol = servers
    # litreview now decomposes into a per-comment task list; a 'sources' task yields gap_fill.
    ol.reply = json.dumps({"tasks": [{"comments": [1], "need": "sources", "query": "X"}]})
    m = project.load_manifest(proj)
    out = m.output_dir(proj, "litreview")
    markup = out / "260710_myproj_litreview_ra_DCR.docx"
    _make_markup(markup, resolved=False)                         # unresolved ask

    before = len(tr.tasks)
    assert planner.run_next(proj) == 0
    new = [t["title"] for t in tr.tasks[before:]]
    # gap_fill chain, cycle 2: new sources are audited then EMBEDDED (`build`) before revise,
    # which loads a cached corpus and no longer embeds.
    assert new == ["rabbithole gather 2", "rabbithole collect 2", "rabbithole audit 2",
                   "rabbithole build 2", "rabbithole revise 2", "rabbithole mindmap 2",
                   "rabbithole comment 2", "rabbithole next 2"]
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


def test_redirection_runs_hands_free_by_default(proj, servers):
    """The author's COMMENTS are the gate. By default (confirm_tiers empty) even a redirection
    queues its fix chain with NO 'approve plan' task — the head is the first real step."""
    tr, ol = servers
    # a 'redirect' task derives tier=redirection (and steers gather at its query).
    ol.reply = json.dumps({"tasks": [{"comments": [1], "need": "redirect", "query": "Y"}]})
    m = project.load_manifest(proj)
    markup = m.output_dir(proj, "litreview") / "260710_myproj_litreview_ra_DCR.docx"
    _make_markup(markup, resolved=False)

    before = len(tr.tasks)
    assert planner.run_next(proj) == 0
    new = tr.tasks[before:]
    assert not any(t["title"].startswith("rabbithole approve") for t in new)  # no gate
    assert new[0]["command"].startswith("haarpi rabbithole")   # head is the first real step
    assert "depends_on_id" not in new[0]                        # nothing gates the head


def test_confirm_tiers_can_gate_a_chain_when_opted_in(proj, servers, monkeypatch):
    """The knob is retained: set confirm_tiers and the 'approve plan' human gate returns at the
    head of that tier's chain."""
    tr, ol = servers
    cfg = planner.pipeline_config()
    cfg.setdefault("planner", {})["confirm_tiers"] = ["redirection"]
    monkeypatch.setattr(planner, "pipeline_config", lambda: cfg)
    ol.reply = json.dumps({"tasks": [{"comments": [1], "need": "redirect", "query": "Y"}]})
    m = project.load_manifest(proj)
    markup = m.output_dir(proj, "litreview") / "260710_myproj_litreview_ra_DCR.docx"
    _make_markup(markup, resolved=False)

    before = len(tr.tasks)
    assert planner.run_next(proj) == 0
    new = tr.tasks[before:]
    assert new[0]["title"].startswith("rabbithole approve")     # confirm_tiers head
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
    assert steps == ["rabbithole gather", "rabbithole collect", "rabbithole report",
                     "rabbithole comment", "raconteur next"]        # cross-stage chain
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
    assert any(t.startswith("raconteur revise") for t in titles)    # staleness re-fired paper
    entry = [e for e in project.list_plans(proj) if e.get("type") == "refresh"][-1]
    assert entry["stage"] == "paper" and entry["bindings"].get("litreview")


def test_reordered_ladder_puts_design_before_build(proj):
    """The experiment DESIGN (preregistration) is its own stage, before build, in its own
    directory; build and experiments both depend on it."""
    m = project.load_manifest(proj)
    assert list(m.stages) == ["litreview", "design", "build", "experiments", "paper", "deck"]
    assert m.stages["design"]["inputs"] == ["litreview"]
    assert m.stages["design"]["dir"] == "design"
    assert m.stages["design"]["infix"] == "prereg"
    assert "design" in m.stages["build"]["inputs"]
    assert "design" in m.stages["experiments"]["inputs"]
    assert (proj / "design" / "output").is_dir()          # own workspace, scaffolded


def test_dirty_prereg_reopens_the_design_session(proj, servers):
    """A prereg annotation that isn't clean re-opens the attended design session (rayleigh
    re-authors experiments.yaml + the prereg), mirroring experiments' review_session."""
    tr, ol = servers
    ol.reply = json.dumps({"tier": "revise", "assessment": "tighten E2's metric"})
    m = project.load_manifest(proj)
    markup = m.output_dir(proj, "design") / "260710_myproj_prereg_ra_DCR.docx"
    _make_markup(markup, resolved=False)

    before = len(tr.tasks)
    assert planner.run_next(proj) == 0
    new = tr.tasks[before:]
    assert new[0]["title"].startswith("rayleigh design_session")
    assert "command" not in new[0]                         # attended session, yours
    assert "rayleigh init" in new[0]["description"]


def test_title_parse_disambiguates_rayleighs_two_stages():
    """One tool, two stages: the STEP tells design work from experiments work."""
    assert planner._parse_title("rayleigh design_session 2")[0] == "design"
    assert planner._parse_title("rayleigh process 2")[0] == "experiments"
    assert planner._parse_title("rayleigh review_session 3")[0] == "experiments"


def test_clean_methods_mints_build_and_opens_experiments(proj, servers):
    """raster's methods digest, rendered to a docx, mints the build stage on a clean read and
    unlocks experiments — the build rung is no longer unwired."""
    tr, _ = servers
    m = project.load_manifest(proj)
    # build's inputs (litreview + design) already released
    for stage, infix in (("litreview", "litreview"), ("design", "prereg")):
        rel = m.output_dir(proj, stage) / f"260710_myproj_{infix}.docx"
        _make_markup(rel, resolved=True)                      # token-free name == a release
    markup = m.output_dir(proj, "build") / "260710_myproj_methods_ra_DCR.docx"
    _make_markup(markup, resolved=True, tracked=True)         # clean methods review

    before = len(tr.tasks)
    assert planner.run_next(proj) == 0
    assert project.latest_release(proj, m, "build") is not None     # build minted
    # experiments opens with the interactive `rayleigh plan` session (executable experiments)
    opened = [t for t in tr.tasks[before:] if t["title"] == "rayleigh experiment design session"]
    assert opened and "rayleigh plan" in opened[0]["description"]


def test_dirty_methods_reopens_the_build_session(proj, servers):
    """An annotation on the methods digest re-opens the attended build session (raster
    re-builds and re-emits) — mirrors design's design_session."""
    tr, ol = servers
    ol.reply = json.dumps({"tier": "revise", "assessment": "the frozen test for E2 is wrong"})
    m = project.load_manifest(proj)
    markup = m.output_dir(proj, "build") / "260710_myproj_methods_ra_DCR.docx"
    _make_markup(markup, resolved=False)

    before = len(tr.tasks)
    assert planner.run_next(proj) == 0
    new = tr.tasks[before:]
    assert new[0]["title"].startswith("raster build_session")
    assert "command" not in new[0]                            # attended session, yours
    assert "raster" in new[0]["description"]


def test_experiments_extend_escalates_to_attended_review(proj, servers):
    tr, ol = servers
    ol.reply = json.dumps({"tier": "extend", "assessment": "needs more seeds"})
    m = project.load_manifest(proj)
    markup = m.output_dir(proj, "experiments") / "260710_myproj_results_ra_DCR.docx"
    _make_markup(markup, resolved=False)

    before = len(tr.tasks)
    assert planner.run_next(proj) == 0
    new = tr.tasks[before:]
    assert new[0]["title"].startswith("rayleigh review_session")
    assert "command" not in new[0]                              # attended session, yours
    assert "rayleigh review" in new[0]["description"]


def test_manifest_round_trips_deck_formats(tmp_path):
    m = project.Manifest(name="x", short_title="x", brief="b",
                         deck_formats=["shorttalk", "longtalk"])
    project.save_manifest(m, tmp_path)
    assert project.load_manifest(tmp_path).deck_formats == ["shorttalk", "longtalk"]


def test_old_manifest_gains_deck_stage_and_empty_formats_on_load(tmp_path):
    """A manifest written before the deck stage existed loads WITH it (DEFAULT_STAGES merge) and an
    empty deck_formats — migration is by load, no rewrite needed."""
    (tmp_path / project.MANIFEST).write_text(
        "name: old\nshort_title: old\nbrief: b\n"
        "stages:\n  litreview:\n    dir: litReview\n")
    m = project.load_manifest(tmp_path)
    assert "deck" in m.stages and m.stages["deck"]["tool"] == "razzle"
    assert m.deck_formats == []


def _human_client(tr):
    return trundlr.TrundlrClient(f"http://127.0.0.1:{tr.server_address[1]}")


def test_deck_opens_one_configure_task_pointing_at_the_interview(proj, servers):
    """The deck stage opens with ONE human task (like `rayleigh design session`): run the interview.
    haarpi never creates a task per format — the deck is tracked by its deliverable, not bookkeeping
    tasks for a fork it cannot see. Holds whether or not formats are already chosen."""
    tr, _ = servers
    for fmts in ([], ["shorttalk", "longtalk"]):
        m = project.load_manifest(proj)
        m.deck_formats = fmts
        before = len(tr.tasks)
        planner._open_deck(_human_client(tr), m, {"human_resource": 1})
        new = tr.tasks[before:]
        assert len(new) == 1                                    # exactly one, regardless of formats
        assert "configure" in new[0]["title"]
        assert "razzle interview" in new[0]["description"]      # points at the pure-python interview
        assert "command" not in new[0]                          # attended (human), not a runner task


def test_submission_assembled_recognises_built_submissions():
    assert planner._submission_assembled("assembled  (no PDF; template ready)")
    assert planner._submission_assembled("packaged   260901_x_submission.docx")
    assert not planner._submission_assembled("pending    (no template)")


def test_deck_trigger_waits_for_an_assembled_submission(proj, monkeypatch):
    """The deck opens on submission-assembled (paper finished + venue committed), not a bare
    manuscript release — a built submission for a selected venue is what flips the gate."""
    m = project.load_manifest(proj)
    monkeypatch.setattr(planner, "_selected_venues", lambda root: ["css2026"])
    assert not planner._has_assembled_submission(proj, m)          # nothing built yet
    sub = m.stage_dir(proj, "paper") / "submission" / "css2026"
    sub.mkdir(parents=True)
    (sub / "draft.tex").write_text("x")                            # assembled (uncompiled)
    assert planner._has_assembled_submission(proj, m)


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
    assert titles[0].startswith("rabbithole gather")
    assert planner.run_queue(root) == 0                         # idempotent: nothing doubled
    assert len([t for t in tr.tasks
                if t.get("project_id") == m.trundlr_project_id]) == 6

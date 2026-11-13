"""`haarpi next` reads the claims gate (2026-11-13).

A claim-grounded literature review is gated on one page, and the person's saved answers
(`…_litreview_gate_ra_DCR.json`) are its markup. A pass with nothing missing releases the
claims as `…_litreview.claims.json` and opens what waits on the litreview; a pass with ideas
missing searches for them before any release; no confidence sends the run back to Cale and
Claude; hold does nothing; a canary kind that holds blocks the release whatever the answers;
an unfinished page is not consumed; and one set of answers is never planned twice.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from haarpi import naming, planner, project

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "rabbithole" / "tests"))
import test_ground as TG          # noqa: E402  the two-paper fake project and its fake model

from rabbithole import cards as C, gate as RG, ground as G   # noqa: E402


class FakeClient:
    tasks: list = []

    def __init__(self, *a, **k):
        pass

    def all_tasks(self):
        return list(FakeClient.tasks)

    def tasks_for_project(self, pid):
        return list(FakeClient.tasks)

    def create_task(self, title, project_id, **kw):
        t = {"id": len(FakeClient.tasks) + 1, "title": title, "project_id": project_id, **kw}
        FakeClient.tasks.append(t)
        return t


@pytest.fixture
def proj(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    (tmp_path / "cfg" / "haarpi").mkdir(parents=True)
    (tmp_path / "cfg" / "haarpi" / "config.toml").write_text(
        '[trundlr]\nurl = "http://trundlr.invalid"\ngpu_resource = 9\ngpus_resource = 8\n'
        'cpu_resource = 3\nhuman_resource = 1\nclaude_resource = 4\n')
    root = tmp_path / "260812_myproject"
    root.mkdir()
    assert planner.run_init(root, name="myproject", short_title="myproj", brief="firms",
                            initials="DCR", priority=2, no_trundlr=True) == 0
    m = project.load_manifest(root)
    m.trundlr_project_id = 7
    project.save_manifest(m, root)
    monkeypatch.setattr(planner.trundlr, "TrundlrClient", FakeClient)
    FakeClient.tasks = []

    cfg = C.CardsConfig(dir=tmp_path / "cards")
    monkeypatch.setattr(C, "load_config", lambda: cfg)
    monkeypatch.setattr(G, "brain_for", lambda cfg_, model: TG.FakeBrain())
    run = G.Run(root, cfg)
    for ck, (label, title, authors, text) in TG.PAPERS.items():
        cd = run.card(ck)
        cd.write_text(text)
        cd.write_meta({"citekey": ck, "label": label, "title": title, "authors": authors,
                       "abstract": "", "pdf": f"zotero://open-pdf/{ck}", "text_version": 3})
    run.dump("papers", {"citekeys": sorted(TG.PAPERS), "skipped": []})
    TG._all_steps(run)
    page = RG.build(root, cfg=cfg, short="myproj")
    return root, run, page


def _answer(root: Path, run, *, outcome="pass", wrong=0, missing=None, judge=None) -> Path:
    gate = run.load("gate")
    a = {}
    for i, cid in enumerate(gate["sample"][:judge]):
        a[f"says|{cid}"] = "no" if i < wrong else "yes"
        a[f"own|{cid}"] = "yes"
    for t, text in (missing or {}).items():
        a[f"missing|{t}"] = text
    if outcome:
        a["gate"] = outcome
    fp = Path(run.paths.output) / gate["answers"]
    fp.write_text(json.dumps({"project": "myproj", "seed": gate["seed"], "sample": gate["sample"],
                              "gate": outcome, "answers": a}))
    return fp


def test_the_page_and_its_answers_are_named_on_the_chain(proj):
    root, run, page = proj
    m = project.load_manifest(root)
    gate = run.load("gate")
    assert page.name == gate["page"] and page.name.endswith("_myproj_litreview_gate_ra.html")
    assert gate["answers"].endswith("_litreview_gate_ra_DCR.json")
    assert naming.is_release(naming.parse(Path("261113_myproj_litreview.claims.json"), "myproj")[1])
    assert not naming.is_release(naming.parse(Path(gate["answers"]), "myproj")[1])
    html = page.read_text()
    assert html.index("The map") < html.index("What slipped") < html.index("1 · A sample") \
        < html.index("2 · What is missing")
    assert (page.parent / gate["page"].replace("_gate_ra", "_gatemap_ra")).is_file()
    assert planner.find_finished_markup(root, m) is None        # nothing saved yet


def test_a_pass_releases_the_claims_and_leaves_out_the_wrong_ones(proj):
    root, run, _ = proj
    fp = _answer(root, run, wrong=1)
    m = project.load_manifest(root)
    assert planner.find_finished_markup(root, m) == ("litreview", fp)
    assert planner.run_next(root) == 0
    rel = project.latest_release(root, m, "litreview")
    assert rel and rel.name.endswith("_myproj_litreview.claims.json")
    blob = json.loads(rel.read_text())
    assert blob["format"] == "claims-release/1" and blob["gate"]["wrong"] == 1
    assert len(blob["claims"]) == 5 and set(blob["sources"]) == set(TG.PAPERS)
    assert not fp.exists() and (fp.parent / "archive" / "gate" / fp.name).is_file()
    # design (prereg) is unlocked and opens on its session; build still waits on design
    titles = [t["title"] for t in FakeClient.tasks]
    assert "ramus analytical framework" in titles and not any(t.startswith("raster") for t in titles)
    assert planner.run_next(root) == 0                              # nothing left to read
    assert [e for e in project.list_plans(root) if e.get("release") == rel.name]


def test_missing_ideas_are_searched_for_before_any_release(proj):
    root, run, _ = proj
    _answer(root, run, missing={"T1": "procurement as a first customer\nloan guarantees"})
    assert planner.run_next(root) == 0
    m = project.load_manifest(root)
    assert project.latest_release(root, m, "litreview") is None
    steps = [t["title"].split()[1] for t in FakeClient.tasks]
    assert steps == ["gather", "collect", "audit", "build", "cards-plan"]   # it queues the rest
    assert FakeClient.tasks[-1]["command"] == "haarpi ground"
    from rabbithole import config as rhconfig
    pc = rhconfig.load_project(str(m.stage_dir(root, "litreview")))
    assert pc.gather_topics == ["procurement as a first customer", "loan guarantees"]


def test_no_confidence_goes_back_to_cale_and_claude_then_a_fresh_gate(proj):
    root, run, _ = proj
    _answer(root, run, outcome="no_confidence", wrong=2)
    assert planner.run_next(root) == 0
    steps = [t["title"].split()[1] for t in FakeClient.tasks]
    assert steps == ["cards-review", "cards-gate", "gate", "next"]
    assert FakeClient.tasks[0]["resource_ids"] == [1, 4]
    seed = run.load("gate")["seed"]
    RG.build(root, cfg=run.cfg, short="myproj")
    assert run.load("gate")["seed"] == seed + 1                    # a fresh sample


def test_hold_releases_nothing_and_queues_nothing(proj):
    root, run, _ = proj
    _answer(root, run, outcome="hold")
    assert planner.run_next(root) == 0
    assert FakeClient.tasks == []
    assert project.latest_release(root, project.load_manifest(root), "litreview") is None


def test_an_unfinished_page_is_not_consumed(proj):
    root, run, _ = proj
    fp = _answer(root, run, judge=1)
    assert planner.run_next(root) == 0
    assert fp.exists() and FakeClient.tasks == []
    assert not [e for e in project.list_plans(root) if e.get("markup") == fp.name]


def test_a_canary_kind_that_holds_blocks_a_pass(proj):
    root, run, _ = proj
    close = run.load("close")
    close["holds"] = ["step 2: direction"]
    run.dump("close", close)
    _answer(root, run)
    assert planner.run_next(root) == 0
    assert project.latest_release(root, project.load_manifest(root), "litreview") is None
    assert [t["title"].split()[1] for t in FakeClient.tasks][0] == "cards-review"


def test_one_set_of_answers_is_never_planned_twice(proj):
    root, run, _ = proj
    fp = _answer(root, run, outcome="no_confidence", wrong=2)
    body = fp.read_text()
    assert planner.run_next(root) == 0
    n = len(FakeClient.tasks)
    fp.write_text(body)                                             # the same answers again
    assert planner.run_next(root) == 0
    assert len(FakeClient.tasks) == n

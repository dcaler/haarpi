"""`haarpi ground` queues claim grounding as a graph, sized from the card store (2026-11-13).

Each task waits on every input it needs (`depends_on_ids`) and on nothing else: the two
verifiers run side by side on the GPU pool, extract runs on both cards in parts of about 8 h,
durations scale with the papers still to do, and a project whose papers are all carded queues
no GPU work at all.
"""
from __future__ import annotations

from types import SimpleNamespace

from haarpi import planner

V = ("gemma", "gpt")
K = ("gemma", "qwen")


def _need(new: int, papers: int = 200) -> dict:
    return {"papers": papers, "extract": new, "kind": {m: new for m in K},
            "reread": {m: new for m in V}, "confirm": {m: new for m in V}, "own": {m: new for m in V},
            "topics": new}


def _by(plan):
    return {t["name"]: t for t in plan}


def test_each_task_waits_on_all_its_inputs_and_the_verifiers_on_each_other_never():
    p = _by(planner.grounding_plan(_need(10)))
    assert p["cards-plant"]["after"] == ["cards-init", "cards-extract#1"]
    assert p["cards-reread-1"]["after"] == p["cards-reread-2"]["after"] == ["cards-plant"]
    assert p["cards-locate"]["after"] == ["cards-plant", "cards-reread-1", "cards-reread-2"]
    assert p["cards-confirm-2"]["after"] == ["cards-locate"]
    assert p["cards-own-1"]["after"] == ["cards-locate", "cards-kind-1", "cards-kind-2"]
    assert set(p["cards-close"]["after"]) == {"cards-locate", "cards-confirm-1", "cards-confirm-2",
                                              "cards-own-1", "cards-own-2"}
    assert [p[n]["after"] for n in ("cards-topics", "cards-gate", "gate", "next")] == \
        [["cards-close"], ["cards-topics"], ["cards-gate"], ["gate"]]
    # a task never waits on one that comes after it
    order = [t["name"] for t in planner.grounding_plan(_need(10))]
    for i, n in enumerate(order):
        assert all(order.index(a) < i for a in p[n]["after"])


def test_extract_comes_in_parts_of_about_eight_hours():
    plan = planner.grounding_plan(_need(191))             # FirmPathways from scratch: ~38 h
    parts = [t for t in plan if t["step"] == "cards-extract"]
    assert len(parts) == 5 and all(t["hours"] <= planner.EXTRACT_PART_HOURS for t in parts)
    assert [t["part"] for t in parts] == [(k, 5) for k in range(5)]
    assert _by(plan)["cards-plant"]["after"] == ["cards-init"] + [t["name"] for t in parts]


def test_durations_scale_with_the_papers_still_to_do():
    small, big = _by(planner.grounding_plan(_need(4))), _by(planner.grounding_plan(_need(100)))
    assert small["cards-reread-1"]["hours"] == round(0.055 * 4, 3)
    assert big["cards-reread-1"]["hours"] == round(0.055 * 100, 3)
    assert small["cards-confirm-1"]["hours"] == planner.CARD_MIN_HOURS     # the floor


def test_a_fully_carded_project_queues_no_gpu_work():
    plan = planner.grounding_plan(_need(0))
    assert [t["name"] for t in plan] == ["cards-init", "cards-plant", "cards-locate", "cards-close",
                                         "cards-gate", "gate", "next"]
    assert _by(plan)["cards-close"]["after"] == ["cards-locate"]
    assert _by(plan)["cards-gate"]["after"] == ["cards-close"]       # nothing new to score


def test_new_topics_alone_rescore_without_any_other_gpu_work():
    need = dict(_need(0), topics=150)
    p = _by(planner.grounding_plan(need))
    assert [n for n, t in p.items() if t["step"] in ("cards-topics",)] == ["cards-topics"]
    assert p["cards-topics"]["hours"] == 1.5 and p["cards-gate"]["after"] == ["cards-topics"]


class _Client:
    def __init__(self):
        self.tasks = []

    def all_tasks(self):
        return []

    def tasks_for_project(self, pid):
        return []

    def create_task(self, title, project_id, **kw):
        t = {"id": 100 + len(self.tasks), "title": title, **kw}
        self.tasks.append(t)
        return t


def test_the_queued_graph_names_lanes_commands_and_dependencies(monkeypatch):
    from rabbithole import ground as rground
    monkeypatch.setattr(rground, "need", lambda root: _need(50))
    c = _Client()
    tr_cfg = {"gpu_resource": 9, "gpus_resource": 8, "cpu_resource": 3, "human_resource": 1,
              "claude_resource": 4}
    got = planner.queue_grounding(None, SimpleNamespace(trundlr_project_id=7), c, tr_cfg)
    t = {x["title"]: x for x in c.tasks}
    ex = [x for x in c.tasks if " cards-extract " in x["title"]]
    assert len(ex) == 2 and all(x["resource_ids"] == [8] for x in ex)
    assert ex[1]["command"] == "haarpi rabbithole cards extract --part 1 --parts 2"
    assert ex[1]["title"] == "rabbithole cards-extract p2of2 1"
    rr = t["rabbithole cards-reread-2 1"]
    assert rr["command"] == "haarpi rabbithole cards reread --model 2" and rr["resource_ids"] == [9]
    plant = t["rabbithole cards-plant 1"]
    assert plant["depends_on_ids"] == [c.tasks[0]["id"]] + [x["id"] for x in ex]
    assert t["rabbithole gate 1"]["resource_ids"] == [1] and t["rabbithole gate 1"].get("command") is None
    assert c.tasks[-1]["command"] == "haarpi next"
    assert got["cycle"] == 1 and len(got["tasks"]) == len(c.tasks)


def test_the_graph_takes_the_cycle_of_the_cards_plan_that_queued_it(monkeypatch):
    from rabbithole import ground as rground
    monkeypatch.setattr(rground, "need", lambda root: _need(0))
    c = _Client()
    c.tasks_for_project = lambda pid: [{"title": "rabbithole build 5"}, {"title": "rabbithole cards-plan 5"}]
    got = planner.queue_grounding(None, SimpleNamespace(trundlr_project_id=7), c,
                                  {"cpu_resource": 3, "human_resource": 1, "gpu_resource": 9})
    assert got["cycle"] == 5 and all(t["title"].endswith(" 5") for t in c.tasks)

"""The approved manuscript queues its own packaging — the ladder's terminal rung.

When a venue's manuscript gate passes, haarpi queues a package RUNNER task (assemble +
compile the submission) followed by a human review of the compiled PDF. The runner waits
on the venue's template-fetch task, so the template is in the slot before it runs; with no
such task (the template was placed by hand) it simply runs, and `raconteur package`
degrades if the slot is empty.
"""

from pathlib import Path

from haarpi import planner, project


class _FakeClient:
    def __init__(self, existing=None):
        self.tasks = list(existing or [])
        self.created = []

    def tasks_for_project(self, pid):
        return [t for t in self.tasks if t.get("project_id") == pid]

    def all_tasks(self):
        return list(self.tasks)

    def create_task(self, title, project_id, **kw):
        t = {"id": 100 + len(self.created), "title": title, "project_id": project_id, **kw}
        self.created.append(t)
        self.tasks.append(t)
        return t


def _m():
    return project.Manifest(short_title="Chords", trundlr_project_id=7)


def test_template_task_id_finds_the_pending_fetch_task():
    client = _FakeClient([{"id": 5, "project_id": 7, "title": "raconteur template css2026 1"},
                          {"id": 6, "project_id": 7, "title": "raconteur outline css2026 1"}])
    assert planner._template_task_id(client, _m(), "css2026") == 5


def test_no_template_task_means_no_dependency():
    client = _FakeClient([{"id": 6, "project_id": 7, "title": "raconteur outline css2026 1"}])
    assert planner._template_task_id(client, _m(), "css2026") is None


def test_packaging_queues_a_runner_then_a_human_review():
    client = _FakeClient([{"id": 5, "project_id": 7, "title": "raconteur template css2026 1"}])
    tr_cfg = {"runner_resource": 2, "human_resource": 1}

    note = planner._queue_packaging(Path("/tmp"), _m(), client, tr_cfg, "css2026",
                                    Path("260717_Chords_css2026.docx"))

    assert len(client.created) == 2
    pkg, review = client.created
    # the package runner: venue-aware command, on the runner, waiting on the template task
    assert pkg["title"] == "raconteur package css2026 2"           # one past the template's cycle 1
    assert pkg["command"] == "haarpi raconteur package --venue css2026"
    assert pkg["resource_id"] == 2
    assert pkg["depends_on_id"] == 5
    # the human review: reads the PDF, gated on the package having run
    assert review["title"] == "raconteur submission css2026 2"
    assert "command" not in review
    assert review["resource_id"] == 1
    assert review["depends_on_id"] == pkg["id"]
    assert "css2026" in note


def test_packaging_without_a_template_task_has_no_dependency():
    client = _FakeClient()
    tr_cfg = {"runner_resource": 2, "human_resource": 1}

    planner._queue_packaging(Path("/tmp"), _m(), client, tr_cfg, "css2026",
                             Path("260717_Chords_css2026.docx"))

    pkg = client.created[0]
    assert pkg["title"] == "raconteur package css2026 1"           # nothing prior -> cycle 1
    assert pkg["depends_on_id"] is None

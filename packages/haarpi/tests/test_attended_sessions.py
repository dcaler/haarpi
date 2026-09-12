"""An attended session has a command AND needs a person at the keyboard.

The shape this fixes, found on a real board. `rayleigh design session` was queued with no
command and booked to the human resource alone, while its description said "run: haarpi
rayleigh init". Attendance was INFERRED from having no command, so the only way to stop a
runner claiming a step was to drop its command — which left the reader retyping a verb the
task already knew, and showed Claude's time as free while a session occupied it.

What actually gates runner pickup is the RESOURCE: neither the human nor the Claude resource
has a runner attached, so an attended task carrying its command is claimed by nobody.

Runnable two ways:
    pytest tests/test_attended_sessions.py
    python tests/test_attended_sessions.py
"""

from __future__ import annotations

from haarpi import planner
from haarpi.planner import Step


_TR = {"human_resource": 1, "claude_resource": 4, "gpu_resource": 2, "cpu_resource": 3,
       "runner_resource": 0}


class _Client:
    def __init__(self): self.tasks = []

    def all_tasks(self): return list(self.tasks)

    def tasks_for_project(self, pid): return [t for t in self.tasks if t["project_id"] == pid]

    def create_task(self, title, project_id, command=None, depends_on_id=None,
                    description="", resource_id=None, duration=None, resource_ids=None):
        t = {"id": len(self.tasks) + 1, "title": title, "project_id": project_id,
             "command": command, "description": description, "depends_on_id": depends_on_id,
             "resource_ids": resource_ids if resource_ids is not None
             else ([resource_id] if resource_id else [])}
        self.tasks.append(t)
        return t


# ── the three states a step can be in ────────────────────────────────────────

def test_an_attended_step_books_the_human_and_claude():
    s = Step("haarpi rayleigh init", 2.0, "design session", attended=True)
    assert s.human is True, "it waits for a person"
    assert s.resources == ("human", "claude")


def test_a_step_with_nothing_to_run_books_the_human_alone():
    """`resource` still reads "runner" on these by default, and honouring it would hand a
    runner a task with no command to run."""
    s = Step(None, 0.15, "Review the new draft and annotate it.")
    assert s.human is True and s.resources == ("human",)


def test_a_machine_step_is_unchanged():
    s = Step("haarpi rabbithole gather", 1.3, "gather")
    assert s.human is False and s.resources == ("runner",)
    assert Step("haarpi rabbithole mindmap", 0.5, "map", resource="gpu").resources == ("gpu",)


# ── the registry ─────────────────────────────────────────────────────────────

def test_every_claude_launching_step_is_marked_attended():
    """These verbs launch an interactive `claude` session in the project root. A step that
    runs one and is not marked attended would be claimed by a runner and driven headlessly."""
    launches_claude = {"haarpi rayleigh init", "haarpi rayleigh plan",
                       "haarpi rayleigh review", "haarpi raster plan"}
    for stage, steps in planner.STAGE_STEPS.items():
        for name, s in steps.items():
            if (s.command or "") in launches_claude:
                assert s.attended, f"{stage}:{name} launches claude but is not attended"


def test_the_sessions_carry_their_commands():
    """The command belongs in the command field, not only in prose the reader retypes."""
    want = {("experiments", "review_session"): "haarpi rayleigh review",
            ("design", "design_session"):      "haarpi rayleigh init",
            ("build", "build_session"):        "haarpi raster plan"}
    for (stage, name), cmd in want.items():
        s = planner.STAGE_STEPS[stage][name]
        assert s.command == cmd and s.attended


def test_the_genuinely_human_steps_keep_no_command():
    """collect and the comment gates are the person's OWN work — there is nothing to run."""
    for stage, name in (("litreview", "collect"), ("litreview", "comment"),
                        ("paper", "comment"), ("experiments", "comment"), ("deck", "comment")):
        s = planner.STAGE_STEPS[stage][name]
        assert s.command is None and not s.attended and s.resources == ("human",)


# ── what reaches the board ───────────────────────────────────────────────────

def test_a_queued_attended_step_lands_with_both_resources_and_its_command():
    c = _Client()
    planner.queue_chain(c, 7, "design", ["design_session"], _TR)
    t = c.tasks[0]                      # [-1] is the `haarpi next` every chain ends with
    assert t["command"] == "haarpi rayleigh init"
    assert sorted(t["resource_ids"]) == [1, 4]


def test_a_queued_human_step_still_books_only_the_human():
    c = _Client()
    planner.queue_chain(c, 7, "litreview", ["comment"], _TR)
    t = c.tasks[0]
    assert t["command"] is None and t["resource_ids"] == [1]


def test_a_queued_machine_step_is_unaffected():
    c = _Client()
    planner.queue_chain(c, 7, "litreview", ["gather"], _TR)
    t = c.tasks[0]
    assert t["command"] == "haarpi rabbithole gather"
    assert t["resource_ids"] == [2], "runner falls back to the gpu box"


def test_an_unconfigured_claude_resource_degrades_to_the_human_alone():
    """Not every install books Claude as a resource. Missing it must cost the booking, not
    the task — the session still waits for a person either way."""
    c = _Client()
    planner.queue_chain(c, 7, "design", ["design_session"],
                        {"human_resource": 1, "gpu_resource": 2, "runner_resource": 0})
    t = c.tasks[0]
    assert t["resource_ids"] == [1]
    assert t["command"] == "haarpi rayleigh init", "and it still carries its verb"


def test_create_task_still_accepts_a_single_resource():
    """The single-resource callers are not rewritten."""
    c = _Client()
    t = c.create_task("x", 1, resource_id=3)
    assert t["resource_ids"] == [3]


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception:  # noqa: BLE001
            failures += 1
            print(f"  FAIL  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failures}/{len(fns)} passed")
    raise SystemExit(1 if failures else 0)

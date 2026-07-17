"""A selected venue queues a scaffolded, well-prompted template-fetch task.

Locating a conference's submission template is the one step the machine cannot do
reliably, so it stays a human task — but a well-scaffolded one: passing the venue
gate drops a labelled folder and queues a task whose brief pre-fills everything the
CFP yielded (the link, the kind, the double-blind variant). It runs in parallel
with the outline chain; nothing downstream needs it until the paper is packaged.
"""

from pathlib import Path

from haarpi import planner, project


class _FakeClient:
    def __init__(self):
        self.created = []

    def create_task(self, title, project_id, **kw):
        self.created.append({"title": title, "project_id": project_id, **kw})
        return {"id": len(self.created)}


CONFIG = """\
short_title: Chords
venues:
  css2026:
    name: CSS2026
    kind: conference
    status: selected
    url: https://css/cfp
    anonymized: true
    template_url: https://acm.org/kit.zip
    template_kind: latex-acm
    sources:
      template_url: cfp
"""


def _write_config(root: Path) -> None:
    (root / "paper").mkdir(parents=True, exist_ok=True)
    (root / "paper" / "raconteur.yaml").write_text(CONFIG, encoding="utf-8")


def test_selected_venue_configs_reads_the_records(tmp_path):
    _write_config(tmp_path)
    recs = planner._selected_venue_configs(tmp_path)
    assert set(recs) == {"css2026"}
    assert recs["css2026"].template_url == "https://acm.org/kit.zip"


def test_the_template_task_is_scaffolded_and_queued(tmp_path):
    _write_config(tmp_path)
    m = project.Manifest(short_title="Chords", trundlr_project_id=7)
    client = _FakeClient()
    vcfg = planner._selected_venue_configs(tmp_path)["css2026"]

    brief = planner._queue_template_task(tmp_path, m, client, {"human_resource": 1},
                                         "css2026", vcfg, cycle=1)

    # a labelled drop-slot with a README waits for the human
    readme = tmp_path / "paper" / "templates" / "css2026" / "README.md"
    assert readme.exists() and "acm.org/kit.zip" in readme.read_text()
    # one human task: command-less, on the human resource, carrying the full brief
    assert len(client.created) == 1
    t = client.created[0]
    assert t["title"] == "paper css2026 template 1"
    assert "command" not in t
    assert t["resource_id"] == 1
    assert t["description"] == brief and "DOUBLE-BLIND" in brief


def test_an_existing_readme_is_not_clobbered(tmp_path):
    _write_config(tmp_path)
    m = project.Manifest(short_title="Chords", trundlr_project_id=7)
    tdir = tmp_path / "paper" / "templates" / "css2026"
    tdir.mkdir(parents=True)
    (tdir / "README.md").write_text("my own notes", encoding="utf-8")

    vcfg = planner._selected_venue_configs(tmp_path)["css2026"]
    planner._queue_template_task(tmp_path, m, _FakeClient(), {"human_resource": 1},
                                 "css2026", vcfg, cycle=1)

    assert (tdir / "README.md").read_text() == "my own notes"

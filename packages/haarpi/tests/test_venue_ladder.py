"""The venue analysis is the fork in the paper ladder.

Before it: one narrative, one of everything. After it: an outline and a manuscript PER
VENUE the author selected, in independent chains that share the one-pager and nothing else.

Selecting is the author's act — the tool proposes candidates and never promotes one — so
the ladder does not fork until they say where the paper is going.
"""

from __future__ import annotations

import pytest
import yaml

from haarpi import naming, planner


class _FakeClient:
    def __init__(self):
        self.tasks: list[dict] = []

    def all_tasks(self):
        return list(self.tasks)

    def tasks_for_project(self, pid):
        return [t for t in self.tasks if t["project_id"] == pid]

    def create_task(self, title, project_id, command=None, depends_on_id=None,
                    description="", resource_id=None, duration=None):
        t = {"id": len(self.tasks) + 1, "title": title, "project_id": project_id,
             "command": command, "depends_on_id": depends_on_id,
             "description": description}
        self.tasks.append(t)
        return t


def _project(tmp_path, venues: dict) -> None:
    (tmp_path / "paper").mkdir(parents=True, exist_ok=True)
    (tmp_path / "paper" / "raconteur.yaml").write_text(
        yaml.safe_dump({"short_title": "Chords", "venues": venues}))


def test_a_venue_chain_names_its_venue_and_carries_it_in_the_command():
    client = _FakeClient()
    out = planner.queue_chain(client, 1, "paper", ["outline", "comment"], {},
                              venue="ismir")
    titles = [t["title"] for t in out["tasks"]]
    assert titles == ["paper ismir outline 1", "paper ismir comment 1",
                      "paper ismir next 1"]
    cmds = [t["command"] for t in out["tasks"] if t["command"]]
    assert "haarpi raconteur outline --venue ismir" in cmds


def test_cycles_count_per_venue():
    """JASSS's first outline is its cycle 1, however many rounds ISMIR has been through."""
    client = _FakeClient()
    planner.queue_chain(client, 1, "paper", ["outline", "comment"], {}, venue="ismir")
    planner.queue_chain(client, 1, "paper", ["outline", "comment"], {}, venue="ismir")
    jasss = planner.queue_chain(client, 1, "paper", ["outline", "comment"], {},
                                venue="jasss")
    assert jasss["cycle"] == 1
    ismir = planner.queue_chain(client, 1, "paper", ["outline"], {}, venue="ismir")
    assert ismir["cycle"] == 3


def test_a_venueless_chain_is_unchanged():
    """Every project alive today queues these titles; they must keep queueing them."""
    client = _FakeClient()
    out = planner.queue_chain(client, 1, "paper", ["recut", "comment"], {})
    assert [t["title"] for t in out["tasks"]] == [
        "paper recut 1", "paper comment 1", "paper next 1"]
    assert out["tasks"][0]["command"] == "haarpi raconteur onepager --resynth"


def test_an_escalation_out_of_the_stage_carries_no_venue():
    """Gathering literature is shared work — it is not the ISMIR paper's private errand."""
    client = _FakeClient()
    out = planner.queue_chain(client, 1, "paper", ["litreview:gather"], {}, venue="ismir")
    litrev = [t for t in out["tasks"] if t["title"].startswith("litreview")]
    assert litrev and "--venue" not in (litrev[0]["command"] or "")


def test_only_venue_aware_verbs_get_the_flag():
    client = _FakeClient()
    out = planner.queue_chain(client, 1, "paper", ["recut", "draft"], {}, venue="ismir")
    by_cmd = {t["title"]: t["command"] for t in out["tasks"]}
    assert by_cmd["paper ismir recut 1"] == "haarpi raconteur onepager --resynth"
    assert by_cmd["paper ismir draft 1"] == "haarpi raconteur draft --venue ismir"


# ── the fork ─────────────────────────────────────────────────────────────────

def test_the_ladder_runs_the_venue_analysis_after_the_onepager():
    """An outline has a length, a column count and an audience. Those come from somewhere."""
    assert planner.PAPER_LADDER["onepager"][0] == "venue"


def test_selected_venues_are_read_from_the_authors_slate(tmp_path):
    _project(tmp_path, {"ismir": {"name": "ISMIR", "status": "selected"},
                        "jasss": {"name": "JASSS", "status": "candidate"}})
    assert planner._selected_venues(tmp_path) == ["ismir"]


def test_the_venue_gate_forks_one_chain_per_selected_venue(tmp_path):
    _project(tmp_path, {"ismir": {"name": "ISMIR", "status": "selected"},
                        "jasss": {"name": "JASSS", "status": "selected"},
                        "cmj":   {"name": "CMJ", "status": "candidate"}})
    client = _FakeClient()
    m = type("M", (), {"trundlr_project_id": 1, "short_title": "Chords"})()

    note = planner._queue_next_rung(tmp_path, m, client, {}, "venue", "",
                                    tmp_path / "260714_Chords_venue.docx")

    assert "ismir" in note and "jasss" in note and "cmj" not in note
    titles = [t["title"] for t in client.tasks]
    # The venue gate now forks to phase ONE of the outline: headings only, redlined before
    # a bullet is written.
    assert "paper ismir skeleton 1" in titles
    assert "paper jasss skeleton 1" in titles
    assert not any("cmj" in t for t in titles), "a candidate is not a decision"


def test_no_selected_venue_queues_nothing_and_says_why(tmp_path):
    """The tool never picks the venue. It asks."""
    _project(tmp_path, {"ismir": {"name": "ISMIR", "status": "candidate"}})
    client = _FakeClient()
    m = type("M", (), {"trundlr_project_id": 1, "short_title": "Chords"})()

    note = planner._queue_next_rung(tmp_path, m, client, {}, "venue", "",
                                    tmp_path / "260714_Chords_venue.docx")
    assert "NO VENUE SELECTED" in note
    assert client.tasks == []


def test_rework_on_one_venues_paper_stays_in_that_venues_lane(tmp_path):
    client = _FakeClient()
    m = type("M", (), {"trundlr_project_id": 1, "short_title": "Chords"})()
    note = planner._queue_next_rung(tmp_path, m, client, {}, "outline", "jasss",
                                    tmp_path / "260714_Chords_jasss_outline.docx")
    assert "jasss" in note
    assert [t["title"] for t in client.tasks][0] == "paper jasss draft 1"


# ── reading a venue back off a filename ──────────────────────────────────────

@pytest.mark.parametrize("fname,venue", [
    ("260714_Chords_ismir_outline_ra_DCR.docx", "ismir"),
    ("260714_Chords_ismir_ra_DCR.docx", "ismir"),
    ("260714_Chords_onepager_ra_DCR.docx", ""),
    ("260714_Chords_ra_DCR.docx", ""),
])
def test_the_gate_knows_which_venue_a_markup_belongs_to(fname, venue, tmp_path):
    assert naming.venue_of(tmp_path / fname, "Chords") == venue


def test_the_outline_is_written_in_two_gated_phases():
    """Phase one is headings only — enough to compute the whole word plan, and cheap to fix.
    Discovering the structure is wrong after a draft has been written from it costs hours."""
    from haarpi.planner import PAPER_LADDER, STAGE_STEPS
    assert PAPER_LADDER["venue"][0] == "skeleton"
    assert PAPER_LADDER["skeleton"][0] == "outline"
    assert PAPER_LADDER["outline"][0] == "draft"
    # each phase is followed by a human redline before the next runs
    assert PAPER_LADDER["skeleton"][1] == "comment"
    assert STAGE_STEPS["paper"]["skeleton"].command == "haarpi raconteur skeleton"


def test_a_skeleton_redline_is_answered_by_phase_one_not_by_a_draft():
    """Without its own tier map the skeleton fell through to the manuscript's, whose
    "cosmetic" tier is the drafter — so a comment on a heading would have queued a full
    draft against a structure the author had just objected to."""
    from haarpi.planner import PAPER_DELIVERABLE_TIERS
    tiers = PAPER_DELIVERABLE_TIERS["skeleton"]
    assert tiers["cosmetic"] == ["skeleton", "comment"]
    assert tiers["structural"] == ["skeleton", "comment"]
    # a complaint about the structure's story is a complaint about the one-pager
    assert tiers["narrative"] == ["recut", "comment"]

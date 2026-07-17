"""The paper stage is a ladder, not one deliverable — status expands it per venue.

Shared rungs (onepager, venue) sit under `paper`; each selected venue forks into
outline -> draft -> submission. A release wins over a spent working markup, and the
manuscript (bare per-venue release) is told apart from that venue's outline. The
submission rung reports packaging state and template readiness.
"""

from pathlib import Path

from haarpi import planner, project


def _m():
    return project.Manifest(short_title="Chords", trundlr_project_id=2)


def _touch(p: Path):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")


class TestRungState:
    def test_a_release_reads_released(self, tmp_path):
        _touch(tmp_path / "paper" / "output" / "260717_Chords_onepager.docx")
        assert planner._paper_rung_state(tmp_path, _m(), ["onepager"]).startswith("released")

    def test_the_manuscript_is_told_apart_from_the_outline(self, tmp_path):
        out = tmp_path / "paper" / "output"
        _touch(out / "260717_Chords_css2026_outline.docx")          # the outline release
        # the draft rung is the venue chain WITHOUT a deliverable word — none yet
        assert planner._paper_rung_state(tmp_path, _m(), ["css2026"]) == "pending"
        assert planner._paper_rung_state(tmp_path, _m(), ["css2026", "outline"]).startswith("released")
        _touch(out / "260717_Chords_css2026.docx")                  # mint the manuscript
        got = planner._paper_rung_state(tmp_path, _m(), ["css2026"])
        assert got.startswith("released") and "outline" not in got  # it is the manuscript

    def test_a_release_wins_over_a_spent_working_markup(self, tmp_path):
        _touch(tmp_path / "paper" / "260717_Chords_css2026_outline_ra_DCR.docx")
        assert planner._paper_rung_state(tmp_path, _m(), ["css2026", "outline"]).startswith("in flight")
        _touch(tmp_path / "paper" / "output" / "260717_Chords_css2026_outline.docx")
        assert planner._paper_rung_state(tmp_path, _m(), ["css2026", "outline"]).startswith("released")

    def test_tool_output_is_the_tools_turn(self, tmp_path):
        _touch(tmp_path / "paper" / "260717_Chords_onepager_ra.docx")
        assert "tool's turn" in planner._paper_rung_state(tmp_path, _m(), ["onepager"])

    def test_human_markup_is_your_turn(self, tmp_path):
        _touch(tmp_path / "paper" / "260717_Chords_onepager_ra_DCR.docx")
        assert "your turn" in planner._paper_rung_state(tmp_path, _m(), ["onepager"])


class TestSubmissionState:
    def test_pending_with_no_template(self, tmp_path):
        assert planner._submission_state(tmp_path, _m(), "css2026") == "pending    (no template)"

    def test_pending_with_template_ready(self, tmp_path):
        _touch(tmp_path / "paper" / "templates" / "css2026" / "llncs.cls")
        assert planner._submission_state(tmp_path, _m(), "css2026") == "pending    (template ready)"

    def test_a_readme_alone_is_not_a_template(self, tmp_path):
        _touch(tmp_path / "paper" / "templates" / "css2026" / "README.md")
        assert "no template" in planner._submission_state(tmp_path, _m(), "css2026")

    def test_assembled_but_uncompiled(self, tmp_path):
        _touch(tmp_path / "paper" / "templates" / "css2026" / "llncs.cls")
        _touch(tmp_path / "paper" / "submission" / "css2026" / "submission.tex")
        assert planner._submission_state(tmp_path, _m(), "css2026").startswith("assembled")

    def test_packaged_when_a_pdf_exists(self, tmp_path):
        _touch(tmp_path / "paper" / "submission" / "css2026" / "submission.pdf")
        assert planner._submission_state(tmp_path, _m(), "css2026").startswith("packaged")


CONFIG = """\
short_title: Chords
venues:
  css2026:
    name: CSS2026
    status: selected
"""


def test_the_expanded_paper_status_forks_per_selected_venue(tmp_path, capsys):
    (tmp_path / "paper").mkdir()
    (tmp_path / "paper" / "raconteur.yaml").write_text(CONFIG, encoding="utf-8")
    out = tmp_path / "paper" / "output"
    for name in ("260717_Chords_onepager.docx", "260717_Chords_venue.docx",
                 "260717_Chords_css2026_outline.docx"):
        _touch(out / name)

    planner._print_paper_status(tmp_path, _m())
    text = capsys.readouterr().out

    assert "  paper" in text
    assert "onepager" in text and "venue" in text
    assert "    css2026" in text                       # the venue fork
    assert "outline" in text and "draft" in text and "submission" in text
    # the rungs reflect reality: outline released, draft/submission not yet
    lines = {ln.split()[0]: ln for ln in text.splitlines() if ln.strip()}
    assert "released" in lines["outline"]
    assert "pending" in lines["draft"]
    assert "pending" in lines["submission"]

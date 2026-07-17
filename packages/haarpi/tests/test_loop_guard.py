"""The loop guard must tell two clean approvals apart.

Pins the 2026-07-17 SchellingChords venue wedge: `annotation_hash` was keyed on
the annotation content alone. A clean approval carries NO annotations, so every
clean gate on the paper ladder hashed to one constant (944ca53bc6b7721b). The
one-pager approval recorded that hash first; the next clean rung — the venue
slate — collided with it and `haarpi next` refused to plan, wedging the ladder
one rung short of the outline. The markup's identity is what keeps two clean
gates distinct, while re-firing on the SAME file still reproduces its hash so the
genuine double-plan the guard exists for is still caught.
"""

from haarpi import project


class TestCleanGatesAreNotAllTheSame:
    def test_two_clean_markups_do_not_collide(self):
        a = project.annotation_hash([], 0, "260717_p_onepager_ra_DCR.docx")
        b = project.annotation_hash([], 0, "260717_p_venue_ra_DCR.docx")
        assert a != b

    def test_the_same_markup_reproduces_its_hash(self):
        # Re-firing haarpi next on the identical finished file: still guarded.
        one = project.annotation_hash([], 0, "260717_p_onepager_ra_DCR.docx")
        two = project.annotation_hash([], 0, "260717_p_onepager_ra_DCR.docx")
        assert one == two

    def test_the_same_file_re_annotated_is_a_new_set(self):
        asks = [{"author": "DCR", "text": "cite recent work on X"}]
        clean = project.annotation_hash([], 0, "260717_p_outline_ra_DCR.docx")
        dirty = project.annotation_hash(asks, 0, "260717_p_outline_ra_DCR.docx")
        assert clean != dirty


class TestTheLedgerGuardsPerMarkup:
    def test_a_recorded_clean_gate_does_not_flag_a_different_clean_gate(self, tmp_path):
        onepager = project.annotation_hash([], 0, "260717_p_onepager_ra_DCR.docx")
        project.record_plan(tmp_path, {"type": "gate", "stage": "paper",
                                       "annotation_hash": onepager})
        assert project.already_planned(tmp_path, onepager)          # the recorded one
        venue = project.annotation_hash([], 0, "260717_p_venue_ra_DCR.docx")
        assert not project.already_planned(tmp_path, venue)         # the next clean rung

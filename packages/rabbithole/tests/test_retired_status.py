"""A cut thread is not a lexical false friend, and the ledger must not say it is.

DigiPros' 2026-09-19 audit quarantined fourteen papers — seven Page County wind-farm
sources and the narrative-transportation cluster (Green 2000, both Van Laer, Braddock,
Xu, both Mar) — and wrote each one up in `audit_quarantine.md` as a word-sense failure:
"narrative" means a story people read, not a record extracted from simulation logs.

That reason is false. Those papers served a narrative-communication thread that was cut
from the second draft. They are still the project's, still cited by the FIRST draft, and
may return if the thread does. The machine had nothing to say about any of it, and spent
~1m18s per item saying it.

So `retired` is a third status: human-set, and never judged.
"""
import pytest

from rabbithole import corpus_ledger as cl

@pytest.fixture
def led(tmp_path):
    (tmp_path / ".haarpi").mkdir()
    for k in ("GREEN2000", "VANLAER14", "MAR2004", "OUYANG22", "SYME1939"):
        cl.add_purpose(tmp_path, k, cl.LITERATURE, title=k, added_by="gather")
    return tmp_path


def test_retiring_takes_them_out_of_the_corpus(led):
    cl.retire(led, ["GREEN2000", "VANLAER14", "MAR2004"])
    serving = cl.serving(led, cl.LITERATURE)
    assert serving == {"OUYANG22", "SYME1939"}


def test_a_retired_paper_keeps_its_purpose(led):
    (row,) = cl.retire(led, "GREEN2000")
    assert row.status == cl.RETIRED
    assert row.purpose == [cl.LITERATURE], "still the literature review's paper"
    assert row.locked is True, "a human ruled; the machine may not overrule"


def test_a_mislabel_can_be_corrected(led):
    """Retiring LOCKS the row, so without a reversal the label would be a one-way door.
    That is why `restore` exists — not as a feature, as the door handle."""
    cl.retire(led, "GREEN2000")
    assert cl.load(led)["GREEN2000"].locked is True
    (row,) = cl.restore(led, keys=["GREEN2000"])
    assert row.status == cl.CORPUS
    assert cl.retired(led) == {}, "and it is back in the corpus the build reads"


def test_restore_by_key_still_works(led):
    cl.retire(led, ["GREEN2000", "VANLAER14"])
    back = cl.restore(led, keys=["VANLAER14"])
    assert [r.key for r in back] == ["VANLAER14"]
    assert set(cl.retired(led)) == {"GREEN2000"}


def test_retired_is_not_quarantine(led):
    """The two must stay distinguishable — one is contestable, the other is not the
    machine's business."""
    cl.retire(led, "GREEN2000")
    cl.set_status(led, "SYME1939", cl.QUARANTINE, added_by="audit")
    rows = cl.load(led)
    assert rows["GREEN2000"].status == cl.RETIRED
    assert rows["SYME1939"].status == cl.QUARANTINE
    assert set(cl.retired(led)) == {"GREEN2000"}, "a quarantine is not a retirement"


def test_audit_may_not_overrule_a_retirement(led):
    """`set_status` refuses a locked row, and retiring locks — so the next audit cannot
    quietly re-file a cut thread as a word-sense failure."""
    cl.retire(led, "GREEN2000")
    cl.set_status(led, "GREEN2000", cl.QUARANTINE, added_by="audit")
    assert cl.load(led)["GREEN2000"].status == cl.RETIRED


def test_an_old_ledger_without_reasons_still_loads(led):
    """Every shape this file has had must keep reading — rows predate the reason field."""
    import json
    fp = cl.ledger_path(led)
    fp.write_text(json.dumps({"version": 1, "items": [
        {"key": "OLD1", "role": "quarantine"},
        {"key": "OLD2", "purpose": ["methods"], "status": "corpus"},
    ]}), encoding="utf-8")
    rows = cl.load(led)
    assert rows["OLD1"].status == cl.QUARANTINE
    assert rows["OLD2"].purpose == ["methods"]

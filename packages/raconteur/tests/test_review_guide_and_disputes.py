"""The manuscript carries its review guide as a comment, and a disputed claim reaches the
revise with its verdict: the fact to rewrite to, or the source as re-read."""

from __future__ import annotations

import json
from types import SimpleNamespace

from haarpi import redline as hredline
from haarpi.testing import write_commented_docx

from raconteur import paper, revise

HUMAN = "D. Cale Reeves"


def test_the_guide_names_the_dispute_route_only_when_there_is_a_trace():
    traced = paper.review_guide(True)
    assert traced.startswith("HOW TO REVIEW THIS DRAFT")
    assert "paper.facttrace.md" in traced and "• dispute:" in traced
    assert "you check and release that" in traced          # the litreview round, said up front
    plain = paper.review_guide(False)
    assert "facttrace" not in plain and "dispute" not in plain
    assert "• structural:" in plain and "Release when the argument holds" in plain


def test_the_guide_rides_on_the_title_and_blocks_until_resolved(tmp_path):
    d = write_commented_docx(tmp_path / "m.docx", ["My Paper", "Body text here."],
                             [{"cid": "1", "author": HUMAN, "text": "x", "anchor": 1}])
    paper._attach_review_guide(d, "My Paper", traced=True)
    threads = hredline.comment_threads(d)
    guide = [t for t in threads.values() if t["author"] == "raconteur"]
    assert len(guide) == 1 and guide[0]["text"].startswith("HOW TO REVIEW THIS DRAFT")
    assert any(c["author"] == "raconteur" for c in hredline.unresolved_comments(d))
    # …but the revise never reads it as an instruction
    assert [c["text"] for c in revise.read_comments(d)] == ["x"]


def _rev(tmp_path, where):
    work = tmp_path / "paper"
    work.mkdir()
    user_rev = work / "261020_P_ra_DCR.docx"
    user_rev.write_bytes(b"")
    (work / "disputes.json").write_text(json.dumps({"markup": user_rev.name, "disputes": [{
        "id": "7", "comment": "this isn't what the source says",
        "sentence": "Validation compared 15 patterns [@grimm2006].", "citekey": "grimm2006",
        "fact_id": "grimm2006#1", "fact": "Validation compared 12 patterns.", "page": 3,
        "quote": "twelve patterns", "where": where, "note": ""}]}))
    lit = tmp_path / "litReview" / "output"
    lit.mkdir(parents=True)
    (lit / "P_litreview.facts.json").write_text(json.dumps({
        "topics": [{"name": "V", "serves": [1]}],
        "facts": [{"id": "grimm2006#3", "citekey": "grimm2006", "verified": True, "page": 5,
                   "scores": [3], "text": "Validation compared 14 patterns."}],
        "themes": []}))
    return user_rev, SimpleNamespace(litrev_dir="litReview")


def test_a_misused_fact_comes_back_as_the_fact_to_rewrite_to(tmp_path):
    user_rev, cfg = _rev(tmp_path, "paper")
    notes = paper.dispute_notes(user_rev, tmp_path, cfg)
    assert "“Validation compared 12 patterns.”" in notes["7"]
    assert "the source's words: “twelve patterns”" in notes["7"]
    assert "Rewrite the sentence to say what the fact says." in notes["7"]


def test_a_re_read_source_comes_back_as_its_new_facts(tmp_path):
    user_rev, cfg = _rev(tmp_path, "source")
    notes = paper.dispute_notes(user_rev, tmp_path, cfg)
    assert "was re-read" in notes["7"] and "“Validation compared 14 patterns.” (p.5)" in notes["7"]


def test_disputes_belong_to_the_markup_they_were_decided_on(tmp_path):
    user_rev, cfg = _rev(tmp_path, "paper")
    later = user_rev.with_name("261021_P_ra_DCR_ra_DCR.docx")
    assert paper.dispute_notes(later, tmp_path, cfg) == {}

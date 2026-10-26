"""A fact base's research questions and topics: drafted, reviewed on a page, adopted."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import yaml

from rabbithole import config, topics


def _cfg(**kw):
    base = dict(research_prompt="How do firms navigate public funding?", topic="cleantech funding",
                focus="sequence analysis; NK model; fitness landscapes", gather_topics=[],
                questions=[])
    return SimpleNamespace(**{**base, **kw})


class _Brain:
    def __init__(self, reply):
        self.reply, self.prompts = reply, []

    def coordinator(self, prompt, system="", **kw):
        self.prompts.append(prompt)
        return self.reply


def test_a_draft_keeps_the_questions_init_took_and_bounds_what_topics_serve():
    reply = json.dumps({"questions": ["Something else?"], "topics": [
        {"name": "Sequence analysis", "serves": [1, 7]}, {"name": "sequence  analysis"},
        {"name": "NK model", "serves": []}, {"name": "Business ecosystems", "serves": [1]}]})
    brain = _Brain(reply)
    qs, ts, note = topics.draft(brain, _cfg(questions=["How do firms navigate public funding?"]))
    assert qs == ["How do firms navigate public funding?"] and note == ""
    # duplicates dropped; out-of-range numbers dropped; serving nothing means serving every question
    assert ts == [{"name": "Sequence analysis", "serves": [1]}, {"name": "NK model", "serves": [1]},
                  {"name": "Business ecosystems", "serves": [1]}]
    assert "settled" in brain.prompts[0] and "NK model" in brain.prompts[0]


def test_an_unusable_draft_falls_back_to_the_topic_line_and_focus_and_says_so():
    qs, ts, note = topics.draft(_Brain("I cannot help with that."), _cfg())
    assert qs == ["cleantech funding?"]
    assert [t["name"] for t in ts] == ["sequence analysis", "NK model", "fitness landscapes"]
    assert all(t["serves"] == [1] for t in ts) and "no usable draft" in note


def test_settling_drops_rejections_and_renumbers_what_topics_serve():
    entry = {"questions": [{"text": "Q one?", "status": "rejected"},
                           {"text": "Q two?", "status": "pending"},
                           {"text": "Q three?", "status": "accepted"}],
             "topics": [{"name": "A", "serves": [1, 3], "status": "accepted"},
                        {"name": "B", "serves": [2], "status": "rejected"},
                        {"name": "C", "serves": [1], "status": "pending"},
                        {"name": " ", "serves": [2]}]}
    qs, ts, warn = topics.settle(entry)
    assert qs == ["Q two?", "Q three?"]
    assert ts == [{"name": "A", "serves": [2]}, {"name": "C", "serves": []}]
    assert len(warn) == 1 and "'C' serves no question" in warn[0]


def test_apply_adopts_the_newest_saved_review_into_a_new_numbered_fact_base_config(tmp_path):
    lit = tmp_path / "litReview"
    config.save_project_to(config.ProjectConfig(project_name="P", focus="lens a; lens b"),
                           lit / "litrev.yaml")
    (lit / "work").mkdir(parents=True)
    (lit / "output").mkdir()
    draft = {"questions": ["Drafted?"], "topics": [{"name": "T", "serves": [1]}]}
    (lit / "work" / topics.DRAFT).write_text(json.dumps(draft))
    saved = lit / "output" / "261026_P_topics_ra_DCR.json"
    saved.write_text(json.dumps({"projects": [{"comment": "", "questions": [
        {"text": "Edited?", "status": "accepted"}], "topics": [
        {"name": "T2", "serves": [1], "status": "pending"}]}]}))
    st = (lit / "work" / topics.DRAFT).stat().st_mtime
    os.utime(saved, (st + 5, st + 5))
    assert topics.run(str(tmp_path), apply=True) == 0
    new = yaml.safe_load((lit / "litrev_2.yaml").read_text())
    assert new["report_format"] == "facts" and new["questions"] == ["Edited?"]
    assert new["topics"] == [{"name": "T2", "serves": [1]}]
    assert new["focus"] == "lens a; lens b"                       # search scope untouched


def test_apply_with_nothing_saved_adopts_the_draft_and_ignores_a_stale_review(tmp_path):
    lit = tmp_path / "litReview"
    config.save_project_to(config.ProjectConfig(project_name="P"), lit / "litrev.yaml")
    (lit / "work").mkdir(parents=True)
    (lit / "output").mkdir()
    old = lit / "output" / "260101_P_topics_ra_DCR.json"
    old.write_text(json.dumps({"projects": [{"questions": ["Old?"], "topics": []}]}))
    os.utime(old, (1, 1))                                         # older than the draft
    (lit / "work" / topics.DRAFT).write_text(json.dumps(
        {"questions": ["Drafted?"], "topics": [{"name": "T", "serves": [1]}]}))
    assert topics.run(str(tmp_path), apply=True) == 0
    new = yaml.safe_load((lit / "litrev_2.yaml").read_text())
    assert new["questions"] == ["Drafted?"] and new["topics"] == [{"name": "T", "serves": [1]}]


def test_only_a_persons_answers_count_as_a_saved_review(tmp_path):
    (tmp_path / "261026_P_topics_ra.json").write_text("{}")
    assert topics.latest_review(tmp_path) is None
    (tmp_path / "261026_P_topics_ra_DCR.json").write_text("{}")
    assert topics.latest_review(tmp_path).name == "261026_P_topics_ra_DCR.json"


def test_the_review_page_is_self_contained_and_names_its_save_file():
    page = topics.review_page([{"project": "P", "config": "litrev_2.yaml", "note": "",
                                "focus": ["a"], "questions": ["Q?</script>"],
                                "topics": [{"name": "T", "serves": [1]}]}],
                              "261026_P_topics_ra_DCR.json")
    assert "<script src" not in page and "http" not in page.split("<script>")[1]
    assert '"261026_P_topics_ra_DCR.json"' in page
    assert "Q?<\\/script>" in page                               # data cannot close the script
    assert "showSaveFilePicker" in page

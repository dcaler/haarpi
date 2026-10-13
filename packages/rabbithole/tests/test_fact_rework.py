"""A comment on a fact base is routed by kind and answered by re-running part of the report.

A fact is its source's, so no comment is answered by rewording one: `haarpi next` sorts each
comment, fact_rework invalidates exactly what it names, and the next report says what became
of each comment, because a regenerated document cannot carry the threads forward.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from rabbithole import config, corpus_ledger, fact_rework
from rabbithole.facts import Fact, FactBase, Topic


def _project(tmp_path):
    lit = tmp_path / "litReview"
    (lit / "work" / "facts" / "scores").mkdir(parents=True)
    (lit / "litrev.yaml").write_text(
        "project_name: P\ntopic: t\nfocus: q1; q2\nreport_format: facts\n"
        "topics:\n- name: Factoid model\n  serves: [1]\n- name: Record incompleteness\n"
        "  serves: [1]\n")
    (lit / "work" / "facts.json").write_text(json.dumps({
        "facts": [{"citekey": "fagiolo2018", "text": "Backward induction approximates the "
                   "structural assumptions of the model."},
                  {"citekey": "bulst1986", "text": "Prosopography is collective biography."}],
        "sources": {"fagiolo2018": {"zotero_key": "FAG1", "label": "Fagiolo et al., 2018"},
                    "bulst1986": {"zotero_key": "BUL1", "label": "Bulst, 1986"}}}))
    for ck in ("fagiolo2018", "bulst1986"):
        (lit / "work" / "facts" / f"{ck}.json").write_text("{}")
        (lit / "work" / "facts" / "scores" / f"{ck}.json").write_text("{}")
    return tmp_path, lit


_ASKS = [{"id": "1", "text": "this isn't what the paper says"},
         {"id": "2", "text": "T2 should only mean gaps in surviving records"},
         {"id": "3", "text": "add a topic on archival silences"},
         {"id": "4", "text": "not relevant"},
         {"id": "5", "text": "fixed the PDF"}]
_ANCHORS = {
    # a by-publication fact bullet: no [@key], found by its text
    "1": {"headings": ["Annotated Bibliography (by publication)"],
          "text": "Backward induction approximates the structural assumptions of the model. — p.19"},
    "2": {"headings": ["Facts by topic", "T2. Record incompleteness (Q1)", "Calibration"],
          "text": "Backward induction … [@fagiolo2018]"},
    "3": {"headings": ["Topics"], "text": "T1. Factoid model — serves Q1"},
    "4": {"headings": ["Annotated Bibliography (by publication)"],
          "text": "Fagiolo, G. (2018). Validation. [@fagiolo2018]"},
    "5": {"headings": ["Sources with no usable text"], "text": "Bulst (1986). [@bulst1986]"},
}
_TASKS = [{"need": "fact", "comments": [_ASKS[0]["text"]], "query": ""},
          {"need": "topic_scope", "comments": [_ASKS[1]["text"]], "topic": None,
           "query": "gaps and bias in surviving records, not model calibration"},
          {"need": "topic", "comments": [_ASKS[2]["text"]], "query": "Archival silences",
           "serves": [1]},
          {"need": "drop", "comments": [_ASKS[3]["text"]], "query": ""},
          {"need": "pdf", "comments": [_ASKS[4]["text"]], "query": ""}]


def test_a_dry_run_says_what_it_would_do_and_touches_nothing(tmp_path):
    root, lit = _project(tmp_path)
    out = fact_rework.apply(str(root), _TASKS, _ASKS, _ANCHORS, gather=True, dry_run=True)
    assert out["config"] is None and not (lit / "work" / fact_rework.RESPONSES).exists()
    assert (lit / "work" / "facts" / "fagiolo2018.json").exists()
    said = "\n".join(out["lines"])
    assert "re-extracting Fagiolo et al., 2018" in said
    assert "T2 now covers: gaps and bias" in said        # the topic read from the heading
    assert "adding topic T3 'Archival silences' (serves Q1)" in said
    assert "quarantining Fagiolo et al., 2018" in said and "re-extracting Bulst, 1986" in said


def test_apply_invalidates_exactly_what_the_comments_name(tmp_path):
    root, lit = _project(tmp_path)
    out = fact_rework.apply(str(root), _TASKS, _ASKS, _ANCHORS, gather=True,
                            markup="261013_P_litreview_ra_DCR.docx")
    facts_dir = lit / "work" / "facts"
    for ck in ("fagiolo2018", "bulst1986"):                # moved aside, never deleted
        assert not (facts_dir / f"{ck}.json").exists()
        assert (facts_dir / "old_rework" / f"{ck}.json").exists()
        assert (facts_dir / "old_rework" / "scores" / f"{ck}.json").exists()
    assert corpus_ledger.load(str(root))["FAG1"].status == "quarantine"
    assert corpus_ledger.load(str(root))["FAG1"].locked
    cfg = config.load_project(str(root))                    # the new numbered config
    assert out["config"] == "litrev_2.yaml"
    assert [t["name"] for t in cfg.topics] == ["Factoid model", "Record incompleteness",
                                               "Archival silences"]
    assert cfg.topics[1]["covers"].startswith("gaps and bias")
    assert cfg.gather_topics == ["Archival silences"]
    blob = json.loads((lit / "work" / fact_rework.RESPONSES).read_text())
    assert [r["kind"] for r in blob["responses"]] == ["fact", "topic_scope", "topic", "drop", "pdf"]
    assert all(r["planned"] for r in blob["responses"])


def test_a_comment_whose_source_cannot_be_told_says_so(tmp_path):
    root, lit = _project(tmp_path)
    out = fact_rework.apply(str(root), [{"need": "fact", "comments": ["wrong"], "query": ""}],
                            [{"id": "9", "text": "wrong"}],
                            {"9": {"headings": ["Topics"], "text": "T1. Factoid model"}},
                            gather=False)
    assert "NOT ACTIONABLE" in out["lines"][0]
    assert not out["responses"][0]["planned"]


def test_the_next_report_says_what_became_of_each_comment(tmp_path):
    root, lit = _project(tmp_path)
    fact_rework.apply(str(root), _TASKS, _ASKS, _ANCHORS, gather=True, markup="m_DCR.docx")
    paths = config.project_paths(str(root))
    (lit / "work" / "facts" / "bulst1986.json").write_text("{}")          # re-extracted
    base = FactBase(focus=["q1"], themes=[],
                    facts={"bulst1986": [Fact(id="b#1", citekey="bulst1986", text="t")]},
                    topics=[Topic("Factoid model", [1]),
                            Topic("Record incompleteness", [1], covers="gaps and bias…"),
                            Topic("Archival silences", [1])])
    lines = "\n".join(fact_rework.response_lines(paths, base))
    assert lines.startswith("## Your comments on the last version")
    assert "5 comment(s) on m_DCR.docx" in lines
    by_kind = {k: ln for ln in lines.splitlines() for k in ("fact", "topic_scope", "topic",
                                                            "drop", "pdf")
               if f"— **{k}**:" in ln}
    assert by_kind["pdf"].endswith("*Done.*")           # Bulst has fresh facts
    assert by_kind["fact"].endswith("*Not done: still the old extraction.*")  # Fagiolo absent…
    assert by_kind["drop"].endswith("*Done.*")          # …because it was quarantined
    assert by_kind["topic"].endswith("*Done.*") and by_kind["topic_scope"].endswith("*Done.*")


def test_every_comment_is_routed_never_edited():
    from haarpi.redline_engine import Evidence, ParaContext, triage
    pol = fact_rework.FactBasePolicy()
    for kind in fact_rework.KINDS.names:
        v = triage(ParaContext(heading="", text="x", comments=["c"], route_hint=kind),
                   Evidence(set(), ""), pol)
        assert v.disposition == f"routed:{kind}" and not v.proceed

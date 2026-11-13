"""haarpi's fact-base tests, archived with the fact base on 2026-11-13 (tag `factbase-retired`).

They tested planner code that was removed: the fact-base rework chain (`chain_from_tasks(...,
fact_base=True)`, `_FACT_NEEDS`), the fact kinds in `_normalise_tasks`, and the facts snapshot
beside a release (`_snapshot_facts`). They do not run as they stand; restoring the fact base
means restoring that code from the tag and moving these back to packages/haarpi/tests/
(the first two to test_comment_kinds.py, the rest to test_next_orchestration.py, whose
`_task` helper they use).
"""
from haarpi import planner


def test_a_section_ask_on_a_fact_base_becomes_a_topic():
    """The deterministic section floor still catches "a section on X" whatever the model said;
    on a fact base the same ask is a new topic."""
    tasks = planner._normalise_tasks([{"comments": [1], "need": "edit"}],
                                     ["we need a section on archival bias"], fact_base=True)
    assert [t["need"] for t in tasks] == ["topic"] and tasks[0]["serves"] == []


def test_the_fact_kinds_are_not_second_guessed_by_the_cite_floor():
    tasks = planner._normalise_tasks(
        [{"comments": [1], "need": "pdf"}, {"comments": [2], "need": "topic_scope", "topic": "T7",
                                             "query": "gaps in surviving records only"}],
        ["I've fixed the PDF, it's now in Zotero", "T7 is too broad"], fact_base=True)
    assert [t["need"] for t in tasks] == ["pdf", "topic_scope"] and tasks[1]["topic"] == 7


def test_a_fact_base_report_draws_its_own_map_so_no_mindmap_follows():
    """`report` on a fact base embeds the map; a separate mindmap redrew the same files."""
    redirect = [_task("redirect", "a different question")]
    assert planner.chain_from_tasks(redirect, fact_base=True)["steps"][-2:] == ["report", "comment"]
    assert planner.chain_from_tasks(redirect)["steps"][-3:] == ["report", "mindmap", "comment"]
    # a narrative revise re-draft still gets its map
    edit = [_task("edit", "")]
    assert "mindmap" in planner.chain_from_tasks(edit)["steps"]


def test_a_fact_base_is_reworked_by_report_never_revise():
    """A fact changes only by re-reading its source, so every kind ends in a cached `report`."""
    chain = lambda *ts: planner.chain_from_tasks(list(ts), fact_base=True)["steps"]
    for need in ("fact", "pdf", "drop", "topic_scope", "edit", "correct"):
        assert chain(_task(need)) == ["report", "comment"], need
    assert chain(_task("topic", "archival bias")) == [
        "gather", "collect", "audit", "build", "report", "comment"]
    assert chain(_task("cite")) == ["build", "report", "comment"]
    assert chain(_task("ingest")) == ["ingest", "collect", "audit", "build", "report", "comment"]
    built = planner.chain_from_tasks([_task("topic", "archival bias"), _task("fact")],
                                     fact_base=True)
    assert built["gather_topics"] == ["archival bias"] and built["tier"] == "gap_fill"
    assert all("revise" not in chain(_task(n)) for n in planner._FACT_NEEDS)


def test_a_released_fact_base_freezes_its_facts_beside_the_release(tmp_path):
    """raconteur reads the facts that were RELEASED; the live sidecar changes with every
    later report, so it would shift under a paper being drafted."""
    from types import SimpleNamespace
    lit = tmp_path / "litReview"
    (lit / "work").mkdir(parents=True)
    (lit / "output").mkdir()
    (lit / "litrev.yaml").write_text("project_name: P\ntopic: t\nreport_format: facts\n")
    (lit / "work" / "facts.json").write_text('{"facts": []}')
    m = SimpleNamespace(stages={"litreview": {"dir": "litReview"}})
    dst = lit / "output" / "P_litreview.docx"
    out = planner._snapshot_facts(tmp_path, m, "litreview", dst)
    assert out.name == "P_litreview.facts.json" and out.read_text() == '{"facts": []}'
    (lit / "litrev.yaml").write_text("project_name: P\ntopic: t\n")         # narrative
    assert planner._snapshot_facts(tmp_path, m, "litreview", dst) is None

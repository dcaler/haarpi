"""Review kinds and the corpus ledger.

Two faults behind one design. A project needs more than one literature review, because one
anchor cannot serve two questions — FirmPathways spent five steering rounds trying to get
sequence-analysis methodology out of a review anchored on innovation policy, and could not,
because the methodology lives in life-course sociology, outside the anchor and adjacent to
an excluded field. And `audit` quarantined by MOVING items out of the Zotero collection,
which is where `refs.bib` comes from — so a minted review could acquire a dangling citation
from a command nobody thought of as touching bibliographies.

Both are answered by keeping ONE collection forever and recording role beside it.

Runnable two ways:
    pytest tests/test_corpus_ledger.py
    python tests/test_corpus_ledger.py
"""

from __future__ import annotations

import pytest

from rabbithole import config, corpus_ledger as cl


@pytest.fixture
def project(tmp_path):
    """A project with both reviews present."""
    (tmp_path / "litReview").mkdir()
    (tmp_path / "litReview" / "litrev.yaml").write_text("{}")
    (tmp_path / "litReviewMethods").mkdir()
    (tmp_path / "litReviewMethods" / "methodsreview.yaml").write_text("{}")
    return tmp_path


def _items(*pairs):
    return [{"data": {"key": k, "title": t}} for k, t in pairs]


# ── review kinds ─────────────────────────────────────────────────────────────

def test_a_plain_project_root_still_gets_the_default_review(tmp_path):
    """The behaviour every existing project depends on."""
    assert config.work_root(tmp_path).name == config.LITREVIEW_DIR


def test_work_root_is_idempotent_inside_either_review(project):
    for d in ("litReview", "litReviewMethods"):
        assert config.work_root(project / d) == project / d


def test_a_methods_directory_is_not_given_a_nested_litreview(project):
    """The defect this fixes. `work_root` appended `litReview` to anything not literally
    named `litReview`, so pointing a verb at a methods review resolved to
    `litReviewMethods/litReview/` and its config was never found."""
    assert config.work_root(project / "litReviewMethods").name == "litReviewMethods"


def test_kind_is_read_from_contents_before_folder_name(tmp_path):
    """Contents win, so the folder name stays a convention rather than a requirement."""
    odd = tmp_path / "whatever"
    odd.mkdir()
    (odd / "methodsreview.yaml").write_text("{}")
    assert config.kind_of(odd).name == "methods"


def test_each_review_sees_only_its_own_cycles(project):
    (project / "litReview" / "litrev_2.yaml").write_text("{}")
    (project / "litReviewMethods" / "methodsreview_2.yaml").write_text("{}")
    lit = [p.name for p in config.list_project_files(project / "litReview")]
    met = [p.name for p in config.list_project_files(project / "litReviewMethods")]
    assert lit == ["litrev.yaml", "litrev_2.yaml"]
    assert met == ["methodsreview.yaml", "methodsreview_2.yaml"]


def test_cycle_numbering_survives_per_kind(project):
    assert config.next_project_file(project / "litReview").name == "litrev_2.yaml"
    assert config.next_project_file(project / "litReviewMethods").name == "methodsreview_2.yaml"


def test_the_methods_infix_is_not_the_build_stages(project):
    """`methods` is already the BUILD stage's infix (raster's implementation writeup), and
    raconteur's find_methods_file() searches the project root for it — so a methods REVIEW
    minted as `..._methods_ra.docx` would be read as raster's writeup."""
    assert config.REVIEW_KINDS["methods"].infix == "methodsreview"
    assert config.REVIEW_KINDS["methods"].infix != "methods"


# ── the ledger ───────────────────────────────────────────────────────────────

def test_the_ledger_is_project_level_from_any_review(project):
    """One collection is shared by every review, so its ledger is shared too."""
    seen = {cl.ledger_path(p) for p in
            (project, project / "litReview", project / "litReviewMethods")}
    assert seen == {project / ".haarpi" / "corpus_ledger.json"}


def test_every_collection_item_gets_a_row(project):
    rows, rec = cl.reconcile(project, _items(("AAA", "One"), ("BBB", "Two")))
    assert set(rows) == {"AAA", "BBB"}
    assert rec.total == 2 and len(rec.added) == 2


def test_human_additions_take_the_role_of_the_review_being_run(project):
    """rabbitHole writes its own rows when it files a find, so an item with no row is by
    construction something the human added — and additions made while working on the
    methods review are methods."""
    rows, _ = cl.reconcile(project / "litReviewMethods", _items(("AAA", "Optimal matching")),
                           default_role=cl.METHODS)
    assert rows["AAA"].role == cl.METHODS


def test_reconcile_reports_orphans_rather_than_deleting_them(project):
    cl.save(project, {"GONE": cl.Row(key="GONE", title="Removed from Zotero")})
    rows, rec = cl.reconcile(project, _items(("AAA", "One")))
    assert [r.key for r in rec.orphaned] == ["GONE"]
    assert "GONE" in rows, "an orphan is reported for a human to decide, never dropped"


def test_reconcile_writes_nothing(project):
    cl.reconcile(project, _items(("AAA", "One")))
    assert not cl.ledger_path(project).exists()


def test_quarantine_keeps_the_item_out_of_the_corpus_only(project):
    """Exclusion from the corpus, never from the record: the item stays in the collection,
    so `refs.bib` still has it and no minted review loses a citation."""
    cl.save(project, {"AAA": cl.Row(key="AAA", role=cl.QUARANTINE)})
    assert cl.load(project)["AAA"].ingestible() is False
    assert cl.QUARANTINE not in cl.INGESTED_ROLES


def test_a_released_paper_is_not_quarantined_again(project):
    """What `--release` never used to do. It moved the item back and left the verdict
    cache alone, so the next audit undid it."""
    cl.save(project, {"AAA": cl.Row(key="AAA", role=cl.QUARANTINE)})
    cl.set_role(project, "AAA", cl.LITERATURE, locked=True)     # the human releases it
    cl.set_role(project, "AAA", cl.QUARANTINE)                  # a later audit tries again
    assert cl.load(project)["AAA"].role == cl.LITERATURE


def test_an_unlocked_row_still_yields_to_the_audit(project):
    cl.save(project, {"AAA": cl.Row(key="AAA", role=cl.LITERATURE)})
    cl.set_role(project, "AAA", cl.QUARANTINE)
    assert cl.load(project)["AAA"].role == cl.QUARANTINE


def test_roles_for_scopes_an_ingest(project):
    cl.save(project, {"A": cl.Row(key="A", role=cl.LITERATURE),
                      "B": cl.Row(key="B", role=cl.METHODS),
                      "C": cl.Row(key="C", role=cl.QUARANTINE)})
    assert cl.roles_for(project, cl.LITERATURE) == {"A"}
    assert cl.roles_for(project, cl.METHODS) == {"B"}


def test_an_unknown_role_is_refused(project):
    with pytest.raises(ValueError):
        cl.set_role(project, "AAA", "substance")


def test_an_unreadable_ledger_reads_as_empty_rather_than_raising(project):
    fp = cl.ledger_path(project)
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text("{ this is not json")
    assert cl.load(project) == {}


def test_rows_survive_a_save_load_round_trip(project):
    cl.save(project, {"A": cl.Row(key="A", role=cl.METHODS, citekey="studer2016",
                                  title="What matters", locked=True, added_by="human")})
    got = cl.load(project)["A"]
    assert (got.role, got.citekey, got.locked, got.added_by) == \
           (cl.METHODS, "studer2016", True, "human")




# ── what the roles are FOR ───────────────────────────────────────────────────

def test_chroma_stores_stay_separate_per_review(project):
    """Two reviews must not retrieve each other's chunks. Isolation comes from the
    directory, not the collection name — every store calls its collection "papers" — so
    this pins the property rather than the mechanism."""
    from rabbithole import config as cfg
    lit = cfg.project_paths(project / "litReview").work / "chroma"
    met = cfg.project_paths(project / "litReviewMethods").work / "chroma"
    assert lit != met
    assert lit.parent.parent.name == "litReview"
    assert met.parent.parent.name == "litReviewMethods"


def test_a_methods_review_has_its_own_output_and_pdfs(project):
    from rabbithole import config as cfg
    lit, met = cfg.project_paths(project / "litReview"), cfg.project_paths(project / "litReviewMethods")
    assert lit.pdfs != met.pdfs and lit.output != met.output


def test_the_deliverable_words_separate_the_two_methods_documents():
    """`methods` is raster's build writeup and is found by a root-level glob; a methods
    REVIEW sharing that infix would be picked up as the writeup."""
    from haarpi import naming
    assert "methodsreview" in naming.DELIVERABLE_WORDS
    assert "methods" in naming.DELIVERABLE_WORDS


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

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
                           default_purpose=cl.METHODS)
    assert rows["AAA"].purpose == [cl.METHODS]


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
    cl.save(project, {"AAA": cl.Row(key="AAA", status=cl.QUARANTINE)})
    assert cl.load(project)["AAA"].ingestible(cl.LITERATURE) is False


def test_quarantining_does_not_destroy_which_review_a_paper_came_from(project):
    """ROLE and QUARANTINE are different axes — provenance against disposition — and storing
    them in one field cost the provenance. A quarantined methods paper stopped being a methods
    paper, and releasing it put it back into `literature` regardless of where it came from."""
    cl.set_purpose(project, "M1", [cl.METHODS], added_by="methods")
    cl.set_status(project, "M1", cl.QUARANTINE, added_by="audit")
    row = cl.load(project)["M1"]
    assert row.purpose == [cl.METHODS] and row.status == cl.QUARANTINE


def test_a_released_methods_paper_returns_to_the_methods_corpus(project):
    cl.set_purpose(project, "M1", [cl.METHODS], added_by="methods")
    cl.set_status(project, "M1", cl.QUARANTINE, added_by="audit")
    cl.set_status(project, "M1", cl.CORPUS, locked=True, added_by="human")
    row = cl.load(project)["M1"]
    assert (row.purpose, row.status, row.locked) == ([cl.METHODS], cl.CORPUS, True)
    assert row.ingestible(cl.METHODS)


def test_a_row_written_before_the_axes_split_still_loads(project):
    """`role: quarantine` was a real value for part of one afternoon."""
    import json
    fp = cl.ledger_path(project); fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps({"version": 1, "items": [
        {"key": "OLD", "role": "quarantine", "locked": True}]}))
    row = cl.load(project)["OLD"]
    assert row.purpose == [cl.LITERATURE] and row.status == cl.QUARANTINE and row.locked is True


def test_a_released_paper_is_not_quarantined_again(project):
    """What `--release` never used to do. It moved the item back and left the verdict
    cache alone, so the next audit undid it."""
    cl.save(project, {"AAA": cl.Row(key="AAA", status=cl.QUARANTINE)})
    cl.set_status(project, "AAA", cl.CORPUS, locked=True)      # the human releases it
    cl.set_status(project, "AAA", cl.QUARANTINE)                    # a later audit tries again
    assert cl.load(project)["AAA"].status == cl.CORPUS


def test_an_unlocked_row_still_yields_to_the_audit(project):
    cl.save(project, {"AAA": cl.Row(key="AAA", purpose=[cl.LITERATURE])})
    cl.set_status(project, "AAA", cl.QUARANTINE)
    assert cl.load(project)["AAA"].status == cl.QUARANTINE


def test_roles_for_scopes_an_ingest(project):
    cl.save(project, {"A": cl.Row(key="A", purpose=[cl.LITERATURE]),
                      "B": cl.Row(key="B", purpose=[cl.METHODS]),
                      "C": cl.Row(key="C", purpose=[cl.LITERATURE], status=cl.QUARANTINE)})
    assert cl.serving(project, cl.LITERATURE) == {"A"}
    assert cl.serving(project, cl.METHODS) == {"B"}


def test_an_unknown_role_is_refused(project):
    with pytest.raises(ValueError):
        cl.set_purpose(project, "AAA", ["substance"])


def test_an_unreadable_ledger_reads_as_empty_rather_than_raising(project):
    fp = cl.ledger_path(project)
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text("{ this is not json")
    assert cl.load(project) == {}


def test_rows_survive_a_save_load_round_trip(project):
    cl.save(project, {"A": cl.Row(key="A", purpose=[cl.METHODS], citekey="studer2016",
                                  title="What matters", locked=True, added_by="human")})
    got = cl.load(project)["A"]
    assert (got.purpose, got.citekey, got.locked, got.added_by) == \
           ([cl.METHODS], "studer2016", True, "human")




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


# ── migrating a project that has no ledger yet ───────────────────────────────

def _ingest_filter(project, review_dir, collection_keys):
    """The rule ingest_from_zotero applies, exercised directly."""
    rows = cl.load(project)
    kind = config.kind_of(review_dir)
    if not rows:
        return list(collection_keys)                      # no ledger: no information
    out = []
    for k in collection_keys:
        row = rows.get(k)
        keep = ((kind.name == config.DEFAULT_KIND) if row is None
                else row.ingestible(kind.name))
        if keep:
            out.append(k)
    return out


def test_an_unrowed_item_is_not_dropped_from_the_default_review(project):
    """THE MIGRATION TRAP. Requiring a matching row looks right and empties corpora: on a
    project that never synced, `audit` writes rows for what it quarantines and nothing
    else, so the next `build` sees a non-empty ledger in which no item carries the
    literature role — and ingests zero papers."""
    cl.set_status(project, "BAD", cl.QUARANTINE, added_by="audit")
    kept = _ingest_filter(project, project / "litReview", ["BAD", "G1", "G2", "G3"])
    assert kept == ["G1", "G2", "G3"], "unclassified papers must survive a partial ledger"


def test_a_non_default_review_takes_only_what_is_explicitly_its_own(project):
    """The other side of that rule: a methods review must not inherit every unrowed item,
    or it swallows the substantive corpus."""
    cl.set_purpose(project, "M1", [cl.METHODS])
    kept = _ingest_filter(project, project / "litReviewMethods", ["M1", "G1", "G2"])
    assert kept == ["M1"]


def test_no_ledger_at_all_ingests_everything(project):
    """Every project predates this feature; none may change behaviour until it has rows."""
    assert _ingest_filter(project, project / "litReview", ["A", "B"]) == ["A", "B"]


def test_a_quarantined_item_is_dropped_but_an_unrowed_one_is_not(project):
    cl.set_status(project, "Q", cl.QUARANTINE)
    assert _ingest_filter(project, project / "litReview", ["Q", "U"]) == ["U"]


# ── purpose is a SET; status is the other axis ───────────────────────────────

def test_a_paper_can_serve_both_reviews(project):
    """A paper on sequence analysis applied to funding pathways is methods work AND
    substantive work. Saying so is the point of keeping one collection; the earlier
    single-value field could not express it at all."""
    cl.add_purpose(project, "P1", cl.LITERATURE, added_by="literature")
    cl.add_purpose(project, "P1", cl.METHODS, added_by="methods")
    row = cl.load(project)["P1"]
    assert row.purpose == [cl.LITERATURE, cl.METHODS]
    assert row.ingestible(cl.LITERATURE) and row.ingestible(cl.METHODS)


def test_adding_a_purpose_never_takes_the_first_one_away(project):
    cl.add_purpose(project, "P1", cl.LITERATURE)
    cl.add_purpose(project, "P1", cl.METHODS)
    assert cl.LITERATURE in cl.load(project)["P1"].purpose


def test_setting_a_purpose_replaces_because_a_human_said_so(project):
    """`collect --role KEY=methods` is a person correcting the record, not a gather adding
    to it, so it replaces."""
    cl.add_purpose(project, "P1", cl.LITERATURE)
    cl.set_purpose(project, "P1", [cl.METHODS], added_by="human")
    assert cl.load(project)["P1"].purpose == [cl.METHODS]


def test_quarantine_removes_a_paper_from_every_review_it_serves(project):
    cl.set_purpose(project, "P1", [cl.LITERATURE, cl.METHODS])
    cl.set_status(project, "P1", cl.QUARANTINE)
    row = cl.load(project)["P1"]
    assert row.purpose == [cl.LITERATURE, cl.METHODS]      # purpose survives
    assert not row.ingestible(cl.LITERATURE) and not row.ingestible(cl.METHODS)


def test_purpose_is_deduplicated_and_never_empty(project):
    cl.set_purpose(project, "P1", [cl.METHODS, cl.METHODS])
    assert cl.load(project)["P1"].purpose == [cl.METHODS]
    cl.set_purpose(project, "P2", [])
    assert cl.load(project)["P2"].purpose == [cl.LITERATURE]


def test_an_unknown_status_is_refused(project):
    with pytest.raises(ValueError):
        cl.set_status(project, "P1", "shelved")


def test_serving_excludes_the_quarantined(project):
    cl.set_purpose(project, "A", [cl.METHODS])
    cl.set_purpose(project, "B", [cl.METHODS])
    cl.set_status(project, "B", cl.QUARANTINE)
    assert cl.serving(project, cl.METHODS) == {"A"}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

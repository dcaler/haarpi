"""The accountability artifacts: refs.bib, disposition.json, and the three-way reconciliation.

These cover a defect that shipped. A postIneq review was minted on 2026-09-10 whose narrative
cited 40 keys while its own refs.bib — written by a `report` run four weeks earlier and never
regenerated — defined only 37, thirteen of them different ones. The document's own banner read
"unresolved keys 0", because `guards.metrics(...).unresolved` compares the narrative to the
CORPUS, and nothing anywhere compared it to the bibliography a reader actually binds.

The cause was structural: `report` wrote both artifacts, `revise` wrote neither, and the run
that produced the shipped document was a revise. So the tests come in two kinds — the pure
functions of the reconciliation, and structural assertions that every path emitting a document
refreshes the artifacts. The second kind is what stops the bug returning through a new exit.

Runnable two ways:
    pytest tests/test_ledger_reconciliation.py
    python tests/test_ledger_reconciliation.py
"""

from __future__ import annotations

import inspect
import json
import types

from rabbithole import ledger

BIB = """\
@article{mehr2025,
  title = {Tonal stability},
  doi = {10.1000/a},
}

@book{otsuka2008,
  title = {Neuromagnetic responses},
}
"""


# ── parsing ───────────────────────────────────────────────────────────────────

def test_bib_keys_reads_every_entry():
    assert ledger.bib_keys(BIB) == {"mehr2025", "otsuka2008"}


def test_bib_keys_tolerates_nothing():
    assert ledger.bib_keys("") == set()
    assert ledger.bib_keys(None) == set()


def test_narrative_stops_at_the_annotated_bibliography():
    """The bibliography restates every key it annotates. Counting those as citations would
    make the reconciliation vacuously clean — every cited key trivially present."""
    from rabbithole import guards
    doc = "Body [@mehr2025].\n\n## Annotated Bibliography\n\n**Otsuka 2008** [@otsuka2008]\n"
    assert set(guards.all_citekeys(doc)) == {"mehr2025", "otsuka2008"}   # both are in the file
    narr = ledger.narrative_only(doc)
    assert "[@mehr2025]" in narr
    assert "[@otsuka2008]" not in narr


def test_narrative_only_handles_docx_body_text():
    """read_body_text yields the heading as a bare line, not a markdown '## ' heading."""
    doc = "Body [@mehr2025].\n\nAnnotated Bibliography\n\nOtsuka 2008 [@otsuka2008]"
    assert "[@otsuka2008]" not in ledger.narrative_only(doc)


def test_narrative_only_is_identity_without_a_bibliography():
    assert ledger.narrative_only("Just body [@a2020].") == "Just body [@a2020]."


# ── the reconciliation ────────────────────────────────────────────────────────

def test_unbibbed_is_the_shipped_defect():
    """The exact failure: a key cited in the narrative that no refs.bib entry defines."""
    r = ledger.reconcile("Claim [@mehr2025]. Claim [@palagi2023].",
                         corpus_keys={"mehr2025", "palagi2023"}, bib_text=BIB)
    assert r.unbibbed == ["palagi2023"]
    assert not r.clean


def test_resolved_against_the_corpus_can_still_be_unbibbed():
    """The precise reason the banner lied. Both keys are in the corpus, so `unresolved` is 0 —
    and one of them still has no bibliography entry."""
    from rabbithole import guards
    narrative = "Claim [@mehr2025]. Claim [@palagi2023]."
    corpus = {"mehr2025", "palagi2023"}
    assert guards.metrics(narrative, corpus).unresolved == 0
    assert ledger.reconcile(narrative, corpus, BIB).unbibbed == ["palagi2023"]


def test_orphaned_entries_are_reported():
    r = ledger.reconcile("Claim [@mehr2025].", {"mehr2025"}, BIB)
    assert r.orphaned == ["otsuka2008"]


def test_clean_when_narrative_and_bib_agree():
    r = ledger.reconcile("A [@mehr2025]. B [@otsuka2008].",
                         {"mehr2025", "otsuka2008"}, BIB)
    assert r.clean and not r.unbibbed


def test_missing_bib_suppresses_rather_than_fabricates_findings():
    """No refs.bib is not the same as an empty one; reporting every key as unbibbed would
    bury the real signal under noise on a project that has never run report."""
    r = ledger.reconcile("Claim [@mehr2025].", {"mehr2025"}, None)
    assert r.bib_missing and r.unbibbed == [] and r.orphaned == []


# ── citekey drift ─────────────────────────────────────────────────────────────

def test_near_miss_catches_the_same_paper_under_two_years():
    """gerdesCOMMONSIM2014 vs ...2023 and jafferCan vs jafferCan2020 both shipped."""
    got = ledger.near_miss_keys({"gerdesCOMMONSIM2014", "jafferCan"},
                                {"gerdesCOMMONSIM2023", "jafferCan2020", "unrelated2001"})
    assert got == {"gerdesCOMMONSIM2014": ["gerdesCOMMONSIM2023"],
                   "jafferCan": ["jafferCan2020"]}


def test_near_miss_ignores_keys_that_are_already_correct():
    assert ledger.near_miss_keys({"mehr2025"}, {"mehr2025", "mehr2020"}) == {}


def test_drift_is_reported_never_rewritten():
    """Two genuinely different papers by one author collide under any automatic rule, and a
    silently rewritten citation is a worse defect than a flagged one."""
    r = ledger.reconcile("Claim [@jafferCan].", {"jafferCan2020"}, BIB)
    kinds = {f.kind for f in ledger.reconciliation_findings(r)}
    assert "citekey-drift" in kinds
    assert "jafferCan" in r.near_misses


def test_findings_name_the_offending_keys():
    r = ledger.reconcile("A [@palagi2023]. B [@kulp2023].",
                         {"palagi2023", "kulp2023"}, BIB)
    detail = " ".join(f.imperative for f in ledger.reconciliation_findings(r))
    assert "palagi2023" in detail and "kulp2023" in detail


# ── the ledger file ───────────────────────────────────────────────────────────

def _paths(tmp_path):
    work, output = tmp_path / "work", tmp_path / "output"
    work.mkdir(); output.mkdir()
    return types.SimpleNamespace(work=work, output=output)


class _Src:
    def __init__(self, title): self.title = title


def test_disposition_records_which_run_wrote_it(tmp_path):
    """A ledger that cannot say which run produced it cannot be caught being stale — and
    staleness is exactly how this went wrong."""
    p = _paths(tmp_path)
    out = ledger.write_disposition(p, [_Src("A"), _Src("B")], {0: "mehr2025", 1: "otsuka2008"},
                                   "Claim [@mehr2025].", {}, verb="revise (redline)")
    d = json.loads(out.read_text())
    assert d["generated_by"] == "revise (redline)"
    assert "generated_at" in d


def test_disposition_partitions_the_whole_corpus(tmp_path):
    p = _paths(tmp_path)
    out = ledger.write_disposition(p, [_Src("A"), _Src("B")], {0: "mehr2025", 1: "otsuka2008"},
                                   "Claim [@mehr2025].", {})
    d = json.loads(out.read_text())
    assert d["cited"] == ["mehr2025"]
    assert list(d["unplaced"]) == ["otsuka2008"]
    assert d["metrics"]["corpus_size"] == 2          # denominator is the corpus, not the drafted set


def test_disposition_carries_the_reconciliation(tmp_path):
    p = _paths(tmp_path)
    rec = ledger.reconcile("A [@palagi2023].", {"palagi2023"}, BIB)
    out = ledger.write_disposition(p, [_Src("A")], {0: "palagi2023"},
                                   "A [@palagi2023].", {}, reconciliation=rec)
    d = json.loads(out.read_text())
    assert d["bibliography"]["unbibbed"] == ["palagi2023"]
    assert d["bibliography"]["orphaned"] == ["mehr2025", "otsuka2008"]


def test_read_bib_returns_none_when_absent(tmp_path):
    assert ledger.read_bib(_paths(tmp_path)) is None


# ── structural: every path that emits a document refreshes the artifacts ──────

def test_report_reconciles_before_it_renders():
    """The check must run against the bibliography the document will ship with."""
    from rabbithole import summarize
    src = inspect.getsource(summarize)
    assert src.index("ledger.reconcile(") < src.index("render.write_review(")


def test_both_revise_subpaths_refresh_the_ledger():
    """The redline path returns early, before the resynth path's bookkeeping. It emits a
    document all the same — it regenerates the whole annotated bibliography, and a graft
    splices in new sections carrying new citations. The shipped defect came through exactly
    this exit, so both are pinned."""
    from rabbithole import revise
    src = inspect.getsource(revise)
    assert src.count("_ledger_refresh(") >= 3          # def + redline + resynth
    redline_return = src.index("revise (redline) complete")
    refresh_before_return = src.index('verb="revise (redline)"')
    assert refresh_before_return > redline_return      # inside the redline branch
    assert 'verb="revise"' in src


def test_revise_refresh_never_kills_the_run():
    """A revise that cannot reach Zotero still produced a valid document; the reconciliation
    it prints is what the reviewer needs, and a crash would throw the document away."""
    from rabbithole import revise
    src = inspect.getsource(revise._ledger_refresh)
    assert "except Exception" in src and "[warn]" in src


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))

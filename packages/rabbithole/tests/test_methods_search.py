"""A methods search looks for how a procedure is done, not for a topic.

DigiPros' first methods gather (2026-11-01) searched the project's subject and curated papers on
segregation and diffusion: the manifest's brief overwrote the methods prompt, the gather derived
a topic from that brief, and six leftover topic papers were swept into the methods corpus. A
methods search wants "X did this procedure in this way", the alternatives to it, and the work
that criticises each, wherever it was published.
"""

from __future__ import annotations

import types

import pytest
import yaml

from rabbithole import config, corpus_ledger as cl, discover, ranking, sources
from rabbithole.models import Candidate


@pytest.fixture
def project(tmp_path):
    (tmp_path / "haarpi.yaml").write_text(yaml.safe_dump(
        {"name": "P", "short_title": "P", "brief": "the project's subject matter"}))
    for d, f in (("litReview", "litrev.yaml"), ("methodsReview", "methodsreview.yaml")):
        (tmp_path / d).mkdir()
        (tmp_path / d / f).write_text(yaml.safe_dump(
            {"project_name": "P", "research_prompt": f"{d} prompt"}))
    return tmp_path


def test_the_brief_never_replaces_a_methods_reviews_prompt(project):
    assert config.load_project(project / "methodsReview").research_prompt == "methodsReview prompt"
    assert config.load_project(project / "litReview").research_prompt == \
        "the project's subject matter"


class _Brain:
    def __init__(self):
        self.calls = []

    def coordinator(self, prompt, sys, **k):
        self.calls.append((prompt, sys))
        if "audit" in sys:
            return "OK"
        if "ONE procedure" in sys:
            return '["block bootstrap critique"]'
        return '["optimal matching substitution costs", "optimal matching critique"]'


def test_methods_queries_name_procedures_and_each_family_gets_its_own():
    cfg = types.SimpleNamespace(topic="how to carry out: OM; bootstrap", focus="",
                                domain_anchor="OM; bootstrap", exclude_topics="subject",
                                gather_topics=["Resampling dependent data: block bootstrap"])
    brain = _Brain()
    queries, origin = discover._generate_queries(brain, cfg, methods=True)
    assert cfg.topic not in queries                  # no raw-topic query
    assert "block bootstrap critique" in queries
    assert origin["block bootstrap critique"].startswith("Resampling")
    sys_prompts = [s for _, s in brain.calls]
    assert all("Global South" not in s for s in sys_prompts)
    assert any("Criticism carries as much weight" in s for s in sys_prompts)


def test_a_methods_gather_with_no_procedures_refuses_rather_than_searching_the_subject(project):
    assert discover.run(str(project / "methodsReview"), use_zotero=False) == 1


def test_the_methods_judge_scores_the_procedure_and_its_critics_not_the_subject():
    seen = {}

    class B:
        def worker_map(self, jobs, **k):
            seen["sys"] = jobs[0][0]
            return ["9"] * len(jobs)

    c = Candidate(title="Optimal matching: a critique", year=2010)
    ranking._llm_rerank([c], "t", "f", B(), 1, "optimal matching", "", methods=True)
    assert "Criticism weighs as much as the procedure" in seen["sys"]
    assert "irrelevant" in seen["sys"]


def test_the_critique_hop_follows_the_most_cited_critiques_forward(monkeypatch):
    crit = [Candidate(title=f"c{i}", year=2020, doi=f"10.1/c{i}", cited_by_count=i,
                      found_by=["chain:critique"]) for i in range(8)]
    plain = Candidate(title="application", year=2020, doi="10.1/app", cited_by_count=999)
    got = {}

    def fake(seeds, email, **k):
        got.update(seeds=seeds, **k)
        return []
    monkeypatch.setattr(sources, "openalex_snowball", fake)
    discover._critique_hop(crit + [plain], types.SimpleNamespace(contact_email=""), lambda m: None)
    assert got["seeds"][0] == "10.1/c7" and "10.1/app" not in got["seeds"]
    assert len(got["seeds"]) == 6 and got["critique"] and not got["refs"]


def _sync(project, monkeypatch, items, **kw):
    from rabbithole import zotero
    monkeypatch.setattr(zotero, "ZoteroClient", lambda gc: types.SimpleNamespace(
        collection_items=lambda coll: items))
    monkeypatch.setattr(zotero, "resolve_collection", lambda zc, cfg: "C")
    return cl.sync(config.project_paths(project / "methodsReview"), None, None, quiet=True, **kw)


def _items(*keys):
    return [{"data": {"key": k, "title": k, "itemType": "journalArticle"}} for k in keys]


def test_a_methods_gather_claims_only_what_it_found(project, monkeypatch):
    cl.add_purpose(project, "OLD", cl.LITERATURE)
    _sync(project, monkeypatch, _items("OLD", "FOUND", "STRAY"), own={"FOUND", "OLD"})
    rows = cl.load(project)
    assert rows["FOUND"].purpose == [cl.METHODS]
    assert rows["STRAY"].purpose == [cl.LITERATURE]
    assert set(rows["OLD"].purpose) == {cl.LITERATURE, cl.METHODS}


def test_collect_still_claims_what_you_add_for_the_review(project, monkeypatch):
    _sync(project, monkeypatch, _items("MINE"), claim_all=True)
    assert cl.load(project)["MINE"].purpose == [cl.METHODS]

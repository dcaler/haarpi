"""raconteur reads a fact base through its sidecar, never through a prefix of its document.

The document opens with the reviewer's release guide, so the old 12k-character prefix would
hand every section the instructions, not the literature. The sidecar frozen with the release
is read three ways: a digest for the analysis, themes as threads for the harvest, and per-
section facts — each traced back to its page once the section is written.
"""

from __future__ import annotations

import json

from raconteur import context, factbase, paper


def _blob():
    return {
        "focus_items": ["factoid model", "validation"],
        "topics": [{"name": "Factoid model", "serves": [1]},
                   {"name": "Validation", "serves": [2], "covers": "process-level only"},
                   {"name": "Schelling", "serves": []}],
        "facts": [
            {"id": "pasin2015#1", "citekey": "pasin2015", "verified": True, "page": 1,
             "link": "zotero://open-pdf/library/items/P1?page=1", "scores": [3, 0, 0],
             "text": "Structured prosopography records each factoid as an assertion of a source."},
            {"id": "pasin2015#2", "citekey": "pasin2015", "verified": False, "page": 4,
             "scores": [3, 0, 0], "text": "A search-placed fact that must never be drafted from."},
            {"id": "grimm2006#1", "citekey": "grimm2006", "verified": True, "page": 3,
             "link": "zotero://open-pdf/library/items/G1?page=3", "scores": [0, 2, 0],
             "text": "Pattern-oriented validation compared 12 patterns across 3 scales."},
            {"id": "sch1971#1", "citekey": "sch1971", "verified": True, "page": 2,
             "scores": [0, 0, 3], "text": "Mild preferences produce strong segregation."},
            {"id": "bg2000#1", "citekey": "bg2000", "verified": True, "page": 9,
             "scores": [1, 1, 0], "text": "Background only."}],
        "themes": [{"item": 1, "name": "Factoid assertions", "fact_ids": ["pasin2015#1",
                                                                          "pasin2015#2"],
                    "synthesis": ["Factoids record assertions, not truths [@pasin2015]."]},
                   {"item": 2, "name": "Pattern validation", "fact_ids": ["grimm2006#1"],
                    "synthesis": []},
                   {"item": 3, "name": "Segregation", "fact_ids": ["sch1971#1"],
                    "synthesis": []}],
        "sources": {"pasin2015": {"label": "Pasin & Bradley, 2015"},
                    "grimm2006": {"label": "Grimm et al., 2006"}},
        "coverage": ["- **Covered** — *Q1: factoid model*. 12 sources."],
        "load_bearing": ["pasin2015", "grimm2006"],
    }


def _project(tmp_path, snapshot=True, mark=True):
    out = tmp_path / "litReview" / "output"
    out.mkdir(parents=True)
    work = tmp_path / "litReview" / "work"
    work.mkdir()
    (out / "261013_P_litreview_ra.md").write_text(
        "# Literature Review\n\n" + ("*Format:* fact base  \n" if mark else "")
        + "\n## Before you release this document\n\n" + "instructions " * 3000)
    (work / "facts.json").write_text(json.dumps(_blob()))
    if snapshot:
        (out / "P_litreview.facts.json").write_text(json.dumps(_blob()))
    return tmp_path


def test_only_facts_that_count_reach_raconteur(tmp_path):
    fb = factbase.load(_project(tmp_path), "litReview")
    assert set(fb.facts) == {"pasin2015#1", "grimm2006#1"}   # verbatim, ≥2 on a served topic
    assert [fb.theme_heading(t) for t in fb.themes] == ["T1 · Factoid assertions",
                                                        "T2 · Pattern validation"]


def test_the_released_snapshot_wins_and_the_live_sidecar_needs_the_marker(tmp_path):
    root = _project(tmp_path)
    assert factbase.find(root, "litReview").name == "P_litreview.facts.json"
    root2 = _project(tmp_path / "b", snapshot=False)
    assert factbase.find(root2, "litReview").name == "facts.json"   # unreleased, marked
    root3 = _project(tmp_path / "c", snapshot=False, mark=False)
    assert factbase.find(root3, "litReview") is None                 # a narrative review


def test_the_review_is_read_as_its_digest_not_its_opening_instructions(tmp_path):
    text = context.load_litreview(_project(tmp_path), "litReview")
    assert "instructions" not in text and len(text) < 2000
    assert "T2. Validation (serves Q2) — covers: process-level only" in text
    assert "Q1: factoid model" in text and "Pasin & Bradley, 2015 [@pasin2015]" in text


def test_themes_are_the_threads_the_harvest_binds(tmp_path):
    threads = context.load_litreview_threads(_project(tmp_path), "litReview")
    assert [t.heading for t in threads] == ["T1 · Factoid assertions", "T2 · Pattern validation"]
    assert threads[0].thesis == "Factoids record assertions, not truths."
    assert threads[0].citekeys == ("pasin2015",)
    assert "{pasin2015#1}" in threads[0].body and "search-placed" not in threads[0].body


def test_a_section_is_handed_its_own_facts_and_they_are_recorded(tmp_path):
    root = _project(tmp_path)
    fb = factbase.load(root, "litReview")
    picker = factbase.SectionFacts(fb)                     # no brain: word overlap ranks
    supplied: dict = {}
    got = paper._section_litrev(picker, "Background", "- pattern-oriented validation scales",
                                "DIGEST", supplied)
    assert got.startswith("DIGEST") and "Facts you may use" in got
    assert supplied["Background"][0].id == "grimm2006#1"   # the relevant fact first
    assert paper._section_litrev(picker, "Results", "x", "DIGEST", supplied) == "DIGEST"
    assert paper._section_litrev(None, "Background", "x", "PROSE", {}) == "PROSE"


def test_a_harvest_restricts_the_pool_to_its_themes(tmp_path):
    fb = factbase.load(_project(tmp_path), "litReview")
    picker = factbase.SectionFacts(fb, themes={"T1 · Factoid assertions"})
    assert [f.id for f in picker.pool] == ["pasin2015#1"]


def test_every_cited_sentence_is_traced_to_its_fact_and_page(tmp_path):
    fb = factbase.load(_project(tmp_path), "litReview")
    supplied = {"Background": list(fb.facts.values())}
    text = ("Structured prosopography records each factoid as a source's assertion "
            "[@pasin2015]. Validation compared 12 patterns across 3 scales [@grimm2006]. "
            "Nothing in the facts says this [@grimm2006].")
    rows = factbase.trace(text, supplied["Background"])
    assert [r["fact"].id if r["fact"] else None for r in rows] == [
        "pasin2015#1", "grimm2006#1", None]
    fp = factbase.write_trace(tmp_path / "paper.facttrace.md", [("Background", text)],
                              supplied, fb)
    out = fp.read_text()
    assert "*2 of 3 cited sentences matched to a fact.*" in out
    assert "[p.3](zotero://open-pdf/library/items/G1?page=3)" in out
    assert "*no matching fact* (@grimm2006)" in out


def test_a_sentence_may_not_change_its_facts_numbers(tmp_path):
    fb = factbase.load(_project(tmp_path), "litReview")
    facts = list(fb.facts.values())
    ok = "Validation compared 12 patterns across 3 scales [@grimm2006]."
    bad = "Validation compared 15 patterns across 3 scales [@grimm2006]."
    assert factbase.number_findings(ok, facts) == []
    [f] = factbase.number_findings(bad, facts)
    assert f.kind == "fact-number" and "15" in f.imperative and "12 patterns" in f.imperative


def test_a_fact_id_copied_into_the_prose_is_removed():
    assert factbase.strip_ids("Factoids record assertions {pasin2015#1} [@pasin2015].") == \
        "Factoids record assertions [@pasin2015]."


def test_a_narrative_review_is_read_exactly_as_before(tmp_path):
    out = tmp_path / "litReview" / "output"
    out.mkdir(parents=True)
    (out / "r.md").write_text("# Review\n\n## Thread one\n\nA thesis [@a].\n")
    assert context.load_litreview_threads(tmp_path, "litReview")[0].heading == "Thread one"
    assert context.load_litreview(tmp_path, "litReview").startswith("# Review")

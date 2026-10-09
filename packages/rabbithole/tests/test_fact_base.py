"""The fact-base litreview: facts a reader can check at their page, organised without prose.

Runnable two ways:
    pytest tests/test_fact_base.py
    python tests/test_fact_base.py
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from rabbithole import config, facts, mindmap
from rabbithole.facts import Fact, FactBase, Theme
from rabbithole.models import Author, Candidate

_MARKED = ("[p.1]\nIntroduction. Careers were coded as sequences of positions.\n\n"
           "[p.2]\nOptimal matching identified 20 ideal-type career patterns, and 85 of 88 "
           "careers were closer to their own type than to any other. The method was ini-\n"
           "tially developed for DNA.")


# ── extraction parsing ────────────────────────────────────────────────────────

def test_a_reply_becomes_facts_with_valid_kinds_focus_and_pages():
    raw = json.dumps([
        {"fact": "Optimal matching found 20 career types.", "kind": "finding",
         "focus": [3, 9, "x"], "page": "2", "quote": "identified 20 ideal-type career patterns"},
        {"fact": "  Careers were coded as   sequences. ", "kind": "nonsense", "focus": [],
         "page": None, "quote": ""},
        {"kind": "finding"}])
    fs = facts._parse_facts("Sure:\n" + raw, "abbott1990", n_items=5)
    assert [f.id for f in fs] == ["abbott1990#1", "abbott1990#2"]
    assert fs[0].focus == [3] and fs[0].page == 2
    assert fs[1].kind == "finding" and fs[1].text == "Careers were coded as sequences."


def test_an_unparseable_reply_is_no_facts():
    assert facts._parse_facts("I cannot read this.", "k", 5) == []


# ── verbatim verification ─────────────────────────────────────────────────────

def test_a_verbatim_quote_is_found_on_its_page_whatever_the_case_or_hyphenation():
    pages = facts.page_texts(_MARKED)
    assert facts.find_quote("Optimal matching identified 20 ideal-type career patterns",
                            pages, hint=1) == 2
    assert facts.find_quote("The method was initially developed for DNA", pages) == 2
    assert facts.find_quote("“Careers were coded as sequences of positions.”", pages) == 1


def test_an_elided_quote_matches_only_if_every_piece_is_on_one_page():
    pages = facts.page_texts(_MARKED)
    assert facts.find_quote("Optimal matching identified 20 ideal-type ... closer to their "
                            "own type than to any other", pages) == 2
    assert facts.find_quote("Careers were coded as sequences ... closer to their own type "
                            "than to any", pages) is None


def test_a_paraphrase_or_a_scrap_is_not_verbatim():
    pages = facts.page_texts(_MARKED)
    assert facts.find_quote("Optimal matching found twenty career types", pages) is None
    assert facts.find_quote("20 ideal-type", pages) is None        # too short to prove anything


def test_ground_verifies_what_it_can_and_locates_the_rest():
    fs = [Fact(id="a#1", citekey="a", text="Twenty types.", page=1,
               quote="Optimal matching identified 20 ideal-type career patterns"),
          Fact(id="a#2", citekey="a", text="It was robust.", page=9, quote="made up words here ok"),
          Fact(id="a#3", citekey="a", text="Nowhere.", page=3, quote="also invented text here")]

    def locate(texts):
        assert texts == ["It was robust.", "Nowhere."]           # verified facts are not re-located
        return [{"claim": "It was robust.", "location": "p.2",
                 "quote": "85 of 88 careers were closer", "support": 0.71}]

    facts.ground(fs, _MARKED, locate=locate)
    assert fs[0].verified and fs[0].page == 2                     # page corrected to where it is
    assert not fs[1].verified and fs[1].page == 2 and fs[1].support == 0.71
    assert fs[2].page is None and fs[2].quote == ""               # kept, unanchored, not dropped


# ── grouping ──────────────────────────────────────────────────────────────────

def test_cluster_separates_two_obvious_groups_deterministically():
    v = [[1, 0], [0.9, 0.1], [0, 1], [0.1, 0.9], [0.95, 0.05]]
    a = facts.cluster(v, 2)
    assert a == facts.cluster(v, 2)
    assert a[0] == a[1] == a[4] and a[2] == a[3] and a[0] != a[2]


def test_a_synthesis_bullet_may_cite_only_its_own_themes_sources():
    assert facts._synthesis_ok("A and B agree [@a; @b].", {"a", "b"})
    assert not facts._synthesis_ok("A agrees with C [@a; @c].", {"a", "b"})
    assert not facts._synthesis_ok("Everyone agrees.", {"a", "b"})


def test_themes_keep_facts_and_drop_bullets_that_bridge():
    fs = [Fact(id=f"k{i}#1", citekey=f"k{i}", text=f"fact {i}", focus=[1]) for i in range(3)]

    class _B:
        def coordinator(self, *a, **k):
            return json.dumps({"name": "Sequence methods",
                               "synthesis": ["k0 and k1 agree [@k0; @k1].",
                                             "k0 contradicts the outsider [@k0; @zz]."]})
    out = facts.themes_for(_B(), 1, "item", fs, {f.id: [1.0, 0.0] for f in fs})
    assert len(out) == 1 and out[0].name == "Sequence methods"
    assert out[0].fact_ids == ["k0#1", "k1#1", "k2#1"]
    assert out[0].synthesis == ["k0 and k1 agree [@k0; @k1]."]


# ── links, weight, map, rendering ─────────────────────────────────────────────

def _cand(**kw):
    return Candidate(title="Measuring resemblance", year=1990,
                     authors=[Author(family="Abbott", given="Andrew")], **kw)


def test_a_fact_links_to_its_page_in_zotero_or_falls_back_to_the_doi():
    gc = SimpleNamespace(zotero_library_type="user", zotero_library_id="1")
    assert facts.page_link(_cand(attachment_key="ATT1"), 7, gc) == \
        "zotero://open-pdf/library/items/ATT1?page=7"
    grp = SimpleNamespace(zotero_library_type="group", zotero_library_id="42")
    assert facts.page_link(_cand(attachment_key="ATT1"), 7, grp) == \
        "zotero://open-pdf/groups/42/items/ATT1?page=7"
    assert facts.page_link(_cand(doi="10.1086/229495"), 7, gc) == "https://doi.org/10.1086/229495"


def test_weight_counts_well_grounded_facts_that_bear_on_the_brief():
    fs = {"a": [Fact(id="a#1", citekey="a", text="t", focus=[1], verified=True),
                Fact(id="a#2", citekey="a", text="t", focus=[], verified=True),
                Fact(id="a#3", citekey="a", text="t", focus=[2], support=0.3)],
          "b": [Fact(id="b#1", citekey="b", text="t", focus=[2], support=0.8)]}
    assert facts.fact_weight(fs) == {"a": 1, "b": 1}


def _base():
    f1 = Fact(id="abbott1990#1", citekey="abbott1990", text="Optimal matching found 20 types.",
              kind="finding", focus=[1], page=2, quote="identified 20 ideal-type career patterns",
              verified=True)
    f2 = Fact(id="abbott1990#2", citekey="abbott1990", text="Careers were coded as sequences.",
              kind="method", focus=[], page=4, quote="coded", support=0.3)
    f3 = Fact(id="zed2020#1", citekey="zed2020", text="Wind farms divide towns.", focus=[])
    return FactBase(focus=["trajectory archetypes", "factoid model"],
                    facts={"abbott1990": [f1, f2], "zed2020": [f3]},
                    themes=[Theme(item=1, name="Sequence typologies", fact_ids=[f1.id],
                                  synthesis=["Abbott anchors the typology [@abbott1990]."])])


def test_the_map_is_built_from_themes_facts_and_fact_weight():
    threads, evidence, weight = facts.map_inputs(_base())
    assert [(t.theme, t.citekeys) for t in threads] == [("Sequence typologies", ["abbott1990"])]
    assert evidence["abbott1990"] == ["Optimal matching found 20 types."]
    assert weight == {"abbott1990": 1, "zed2020": 0}


def test_the_document_links_every_fact_and_says_what_was_not_verbatim(tmp_path):
    corpus = [_cand(attachment_key="ATT1"), Candidate(title="Wind", year=2020,
                                                     authors=[Author(family="Zed")])]
    cfg = SimpleNamespace(topic="t", project_name="DigiPros", focus="trajectory archetypes; "
                          "factoid model")
    gc = SimpleNamespace(zotero_library_type="user", zotero_library_id="1")
    png = tmp_path / "261009_DigiPros_litmap_ra.png"
    md = facts.build_markdown(cfg, "qwen", _base(), corpus, {0: "abbott1990", 1: "zed2020"},
                              gc, ["- **THIN** — *factoid model*."], png)
    assert mindmap.is_fact_base(md)
    assert "![Contribution map](261009_DigiPros_litmap_ra.png)" in md
    assert ("- Optimal matching found 20 types. [@abbott1990] — "
            "[p.2](zotero://open-pdf/library/items/ATT1?page=2): "
            "“identified 20 ideal-type career patterns”") in md
    assert "weak support — verify" in md                         # f2, located by search, 0.3
    assert "*No fact in the corpus bears on this item.*" in md   # item 2 has no theme
    assert md.index("### Sources with no fact bearing on the brief") < md.index("Zed")
    assert "## Annotated Bibliography (by publication)" in md
    # the map's thread parse stops at the bibliography and never sees the per-source view twice
    assert all(t.theme != "Sources with no fact bearing on the brief"
               for t in mindmap.parse_threads(md))


def test_narrative_stays_the_default_format():
    assert config.ProjectConfig().report_format == "narrative"
    assert not mindmap.is_fact_base("# Literature Review: x\n\n## Narrative Review\n")


def test_the_sidecar_round_trips(tmp_path):
    paths = SimpleNamespace(work=tmp_path)
    corpus = [_cand(attachment_key="ATT1"), Candidate(title="Wind", year=2020)]
    gc = SimpleNamespace(zotero_library_type="user", zotero_library_id="1")
    facts.save_sidecar(paths, _base(), corpus, {0: "abbott1990", 1: "zed2020"}, gc)
    blob = json.loads((tmp_path / "facts.json").read_text())
    assert blob["facts"][0]["link"] == "zotero://open-pdf/library/items/ATT1?page=2"
    back = facts.load_sidecar(paths)
    assert back.focus == _base().focus and back.themes[0].name == "Sequence typologies"
    assert back.by_id()["abbott1990#1"].verified


# ── the whole report, with a fake brain ───────────────────────────────────────

def test_the_fact_base_report_runs_end_to_end(tmp_path, monkeypatch):
    from rabbithole import summarize
    out, work = tmp_path / "output", tmp_path / "work"
    out.mkdir()
    work.mkdir()
    paths = SimpleNamespace(root=tmp_path, output=out, work=work, pdfs=tmp_path)
    corpus = [_cand(attachment_key="ATT1", fulltext=_MARKED),
              Candidate(title="Wind", year=2020, authors=[Author(family="Zed")],
                        fulltext="[p.1]\nWind farms divide rural towns, a survey of 300 found.")]
    citekeys = {0: "abbott1990", 1: "zed2020"}
    cfg = config.ProjectConfig(project_name="DigiPros", topic="t",
                               focus="trajectory archetypes; factoid model",
                               report_format="facts")

    class _Brain:
        backend = "ollama"

        def coordinator(self, prompt, system="", **kw):
            if "Paper: Measuring" in prompt:
                return json.dumps([{"fact": "Optimal matching found 20 career types.",
                                    "kind": "finding", "focus": [1], "page": 2,
                                    "quote": "identified 20 ideal-type career patterns"}])
            if "Paper: Wind" in prompt:
                return json.dumps([{"fact": "Wind farms divide rural towns.", "kind": "finding",
                                    "focus": [], "page": 1, "quote": "Wind farms divide rural "
                                    "towns, a survey"}])
            return json.dumps({"name": "Sequence typologies",
                               "synthesis": ["Abbott anchors it [@abbott1990]."]})

        def embed(self, text, max_chars=1500):
            return [1.0, 0.0]

    def _bib(cfg, gc, paths, citekeys, corpus):
        fp = paths.output / "refs.bib"
        fp.write_text("@article{abbott1990, title={M}, author={Abbott, A.}, year={1990}}\n")
        return fp

    monkeypatch.setattr(summarize, "_export_bibtex", _bib)
    monkeypatch.setattr(mindmap, "make_map", lambda *a, **k: {})
    gc = SimpleNamespace(zotero_library_type="user", zotero_library_id="1", contact_email="")
    assert facts.report(cfg, gc, paths, _Brain(), corpus, [{}, {}], citekeys, None) == 0
    md = next(out.glob("*_DigiPros_litreview_ra.md")).read_text()
    assert "[p.2](zotero://open-pdf/library/items/ATT1?page=2)" in md
    assert "### Sequence typologies" in md and "*Abbott anchors it [@abbott1990].*" in md
    assert "### Sources with no fact bearing on the brief" in md
    blob = json.loads((work / "facts.json").read_text())
    assert {f["id"]: f["verified"] for f in blob["facts"]} == {"abbott1990#1": True,
                                                                "zed2020#1": True}
    # cached: a second run extracts nothing
    calls = []
    b = _Brain()
    orig = b.coordinator
    b.coordinator = lambda p, s="", **k: (calls.append(p), orig(p, s, **k))[1]
    facts.report(cfg, gc, paths, b, corpus, [{}, {}], citekeys, None)
    assert not any("--- PAPER TEXT ---" in p for p in calls)


if __name__ == "__main__":
    import sys
    import tempfile
    from pathlib import Path
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                if fn.__code__.co_argcount:
                    with tempfile.TemporaryDirectory() as d:
                        fn(Path(d))
                else:
                    fn()
                print(f"PASS {name}")
            except AssertionError as e:
                failed += 1
                print(f"FAIL {name}: {e}")
    sys.exit(1 if failed else 0)

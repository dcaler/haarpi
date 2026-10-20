"""The fact-base litreview: facts a reader can check at their page, organised without prose.

Runnable two ways:
    pytest tests/test_fact_base.py
    python tests/test_fact_base.py
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from rabbithole import config, facts, mindmap
from rabbithole.facts import Fact, FactBase, Theme, Topic
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


def test_a_soft_hyphen_at_a_line_break_does_not_hide_a_verbatim_quote():
    pages = facts.page_texts("[p.1]\nIntro.\n[p.4]\nStudies were identified using the Scopus data\u00ad\n"
                             "base, using the following combination of terms.")
    assert facts.find_quote("Studies were identified using the Scopus database, using the "
                            "following combination", pages) == 4


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


def test_weight_sums_the_best_score_of_verbatim_useful_facts():
    """A passage found by search carries no weight, however high its support: of 30 DigiPros
    ones at 0.55-0.75 about 6 supported their fact, and the cosine did not tell which."""
    fs = {"a": [Fact(id="a#1", citekey="a", text="t", scores=[3, 0], verified=True),
                Fact(id="a#2", citekey="a", text="t", scores=[1, 1], verified=True),
                Fact(id="a#3", citekey="a", text="t", scores=[0, 2], support=0.3)],
          "b": [Fact(id="b#1", citekey="b", text="t", scores=[0, 2], support=0.8)]}
    assert facts.fact_weight(fs) == {"a": 3, "b": 0}


_TOPICS = [Topic(name="Sequence analysis", serves=[1]), Topic(name="Factoid model", serves=[2])]


def _base():
    f1 = Fact(id="abbott1990#1", citekey="abbott1990", text="Optimal matching found 20 types.",
              kind="finding", scores=[3, 0], page=2,
              quote="identified 20 ideal-type career patterns", verified=True)
    f2 = Fact(id="abbott1990#2", citekey="abbott1990", text="Careers were coded as sequences.",
              kind="method", scores=[2, 0], page=4, quote="coded", support=0.3)
    f3 = Fact(id="zed2020#1", citekey="zed2020", text="Wind farms divide towns.", scores=[1, 0])
    return FactBase(focus=["trajectory archetypes", "factoid model"],
                    facts={"abbott1990": [f1, f2], "zed2020": [f3]},
                    themes=[Theme(item=1, name="Sequence typologies", fact_ids=[f1.id, f2.id],
                                  synthesis=["Abbott anchors the typology [@abbott1990]."])],
                    topics=list(_TOPICS))


def test_the_map_slices_are_topics_and_each_source_sits_in_one():
    threads, evidence, weight = facts.map_inputs(_base())
    # abbott1990's counted fact is central on T1; zed2020 has no useful fact — its own slice
    assert [(t.theme, t.citekeys) for t in threads] == [
        ("T1. Sequence analysis", ["abbott1990"]), (facts.NO_TOPIC_SLICE, ["zed2020"])]
    assert evidence["abbott1990"][0] == "Optimal matching found 20 types."
    assert weight == {"abbott1990": 3, "zed2020": 0}       # f2 is located weakly: not counted


def test_a_source_sits_in_the_topic_that_serves_the_brief():
    topics = [Topic(name="A", serves=[1]), Topic(name="Schelling", serves=[])]
    fs = [Fact(id="k#1", citekey="k", text="t", scores=[2, 3], verified=True),
          Fact(id="k#2", citekey="k", text="t", scores=[0, 3], verified=True)]
    assert facts.map_slice(fs, topics) == 0                # T2 sums higher but serves no question
    assert facts.map_slice(fs[1:], topics) == 1            # only off-brief: it still has a home
    assert facts.map_slice([Fact(id="k#3", citekey="k", text="t", scores=[3, 0])], topics) is None


def test_map_details_carry_page_links_counts_and_a_zotero_link(tmp_path):
    base = _base()
    paths = SimpleNamespace(work=tmp_path)
    (tmp_path / "facts.json").write_text(json.dumps({
        "facts": [{"id": "abbott1990#1", "link": "zotero://open-pdf/library/items/ATT1?page=2"}],
        "sources": {"abbott1990": {"zotero_key": "ITEM1"}}}))
    base.facts["abbott1990"][0].focus = [1]
    gc = SimpleNamespace(zotero_library_type="user", zotero_library_id="1")
    d = facts.map_details(paths, base, gc)["abbott1990"]
    assert d["facts"][0] == {"text": "Optimal matching found 20 types.", "where": "p.2",
                             "link": "zotero://open-pdf/library/items/ATT1?page=2"}
    assert d["counts"] == "1 central, 0 useful of 2 facts"
    assert d["questions"] == "Serves Q1"
    assert d["zotero"] == "zotero://select/library/items/ITEM1"


def test_coverage_is_built_per_question_from_its_topics():
    base = _base()
    lines = facts.coverage(base)
    assert lines[0].startswith("- **THIN** — *Q1: trajectory archetypes*. 1 sources.")
    assert "T1 Sequence analysis: 1 central, 0 useful facts from 1 sources" in lines[0]
    assert "T2 Factoid model: 0 central, 0 useful facts from 0 sources" in lines[1]
    base.topics = [Topic(name="Sequence analysis", serves=[1])]
    assert facts.coverage(base)[1] == ("- **THIN** — *Q2: factoid model*. No topic serves this "
                                       "question.")


def test_the_document_links_every_fact_and_shows_scores(tmp_path):
    corpus = [_cand(attachment_key="ATT1"), Candidate(title="Wind", year=2020,
                                                     authors=[Author(family="Zed")])]
    cfg = SimpleNamespace(topic="t", project_name="DigiPros", focus="trajectory archetypes; "
                          "factoid model")
    gc = SimpleNamespace(zotero_library_type="user", zotero_library_id="1")
    page = tmp_path / "261009_DigiPros_litmap_ra.html"
    base = _base()
    md = facts.build_markdown(cfg, "qwen", base, corpus, {0: "abbott1990", 1: "zed2020"},
                              gc, facts.coverage(base), page)
    assert mindmap.is_fact_base(md)
    # the map is linked (relative), never embedded as a picture
    assert "**[Open the contribution map](261009_DigiPros_litmap_ra.html)**" in md
    assert "![" not in md
    # the release guide opens it: the agent's checklist inside haarpi's frame, with the phrasings
    assert md.index("## Before you release this document") < md.index("## Topics")
    assert "**Coverage against the brief.**" in md and "| **topic_scope** |" in md
    assert not any("release" in t.theme.lower() for t in mindmap.parse_threads(md))
    assert "- **T1. Sequence analysis** — serves Q1; 1 central, 0 useful facts" in md
    assert ("- Optimal matching found 20 types. [@abbott1990] — "
            "[p.2](zotero://open-pdf/library/items/ATT1?page=2): "
            "“identified 20 ideal-type career patterns” **(central)**") in md
    assert "weak support — verify. Not counted." in md           # f2, located by search, 0.3
    t1 = md.split("## T1. Sequence analysis")[1].split("## T2.")[0]
    assert "Careers were coded" not in t1          # search-placed: by-publication list only
    assert "## T2. Factoid model (Q2)" in md
    assert "*No fact in the corpus scores useful or central on this topic.*" in md
    bib = md.split("## Annotated Bibliography (by publication)")[1]
    assert "*[finding; T1:3]*" in bib
    assert "**Zed (2020). Wind.** [@zed2020] *(no fact is useful to a topic)*" in bib
    tail = bib.split("### Sources with no fact useful to a topic")[1]
    assert "- Zed (2020). Wind. [@zed2020]" in tail and "Wind farms divide" not in tail
    # the map's thread parse stops at the bibliography and never sees the per-source view twice
    assert all("Sources with no fact" not in t.theme for t in mindmap.parse_threads(md))


def test_narrative_stays_the_default_format():
    assert config.ProjectConfig().report_format == "narrative"
    assert config.ProjectConfig().topics == []
    assert not mindmap.is_fact_base("# Literature Review: x\n\n## Narrative Review\n")


def test_topics_come_from_the_config_or_fall_back_to_the_questions():
    cfg = SimpleNamespace(focus="a; b", topics=[{"name": "Factoid model", "serves": [1, "2"]},
                                                "ODD protocol", {"name": " "}])
    assert facts.resolve_topics(cfg) == [Topic("Factoid model", [1, 2]), Topic("ODD protocol", [])]
    assert facts.resolve_topics(SimpleNamespace(focus="a; b", topics=[])) == \
        [Topic("a", [1]), Topic("b", [2])]


def test_the_sidecar_round_trips(tmp_path):
    paths = SimpleNamespace(work=tmp_path)
    corpus = [_cand(attachment_key="ATT1"), Candidate(title="Wind", year=2020)]
    gc = SimpleNamespace(zotero_library_type="user", zotero_library_id="1")
    facts.save_sidecar(paths, _base(), corpus, {0: "abbott1990", 1: "zed2020"}, gc)
    blob = json.loads((tmp_path / "facts.json").read_text())
    assert blob["facts"][0]["link"] == "zotero://open-pdf/library/items/ATT1?page=2"
    assert blob["topics"][0] == {"name": "Sequence analysis", "serves": [1], "covers": ""}
    back = facts.load_sidecar(paths)
    assert back.focus == _base().focus and back.themes[0].name == "Sequence typologies"
    assert back.topics == _TOPICS and back.by_id()["abbott1990#1"].scores == [3, 0]


# ── scoring ───────────────────────────────────────────────────────────────────

def test_scores_parse_strictly_and_clip_to_the_scale():
    assert facts._parse_scores('{"F1": [3, 0], "F2": [5, -1]}', 2, 2) == [[3, 0], [3, 0]]
    assert facts._parse_scores('{"F1": [3, 0]}', 2, 2) is None          # a fact missing
    assert facts._parse_scores('{"F1": [3], "F2": [1, 1]}', 2, 2) is None   # wrong width
    assert facts._parse_scores("no.", 1, 2) is None


def test_scoring_is_cached_until_the_topics_change(tmp_path):
    paths = SimpleNamespace(work=tmp_path)
    fs = {"abbott1990": [Fact(id="abbott1990#1", citekey="abbott1990", text="t1"),
                         Fact(id="abbott1990#2", citekey="abbott1990", text="t2")]}
    calls = []

    class _B:
        def coordinator(self, prompt, system="", **kw):
            calls.append(prompt)
            n = prompt.count("\nT")
            return json.dumps({"F1": [3] + [0] * (n - 1), "F2": [0] * n})
    corpus = [_cand()]
    facts.score(_B(), fs, _TOPICS, corpus, {0: "abbott1990"}, paths)
    assert fs["abbott1990"][0].scores == [3, 0] and len(calls) == 1
    facts.score(_B(), fs, _TOPICS, corpus, {0: "abbott1990"}, paths)
    assert len(calls) == 1                                               # cached
    facts.score(_B(), fs, _TOPICS + [Topic("ODD")], corpus, {0: "abbott1990"}, paths)
    assert len(calls) == 2 and fs["abbott1990"][0].scores == [3, 0, 0]   # topics changed


def test_a_fact_bears_on_the_questions_its_useful_topics_serve():
    fs = {"a": [Fact(id="a#1", citekey="a", text="t", scores=[2, 1], verified=True),
                Fact(id="a#2", citekey="a", text="t", scores=[0, 3], verified=True),
                Fact(id="a#3", citekey="a", text="t", scores=[3, 3], support=0.9)]}
    topics = [Topic("Sequence", [1, 3]), Topic("Factoid", [2])]
    facts.derive_focus(fs, topics)
    assert fs["a"][0].focus == [1, 3] and fs["a"][1].focus == [2]
    assert fs["a"][2].focus == []                                  # search-placed: not counted


def test_search_keeps_the_models_own_quote_for_later_measurement():
    f = Fact(id="a#1", citekey="a", text="Twenty types emerged.", page=4,
             quote="twenty ideal types of careers")
    facts.ground([f], "[p.1]\nnothing here", locate=lambda texts: [
        {"claim": texts[0], "location": "p.2", "quote": "a nearby passage", "support": 0.7}])
    assert (f.page, f.quote, f.verified) == (2, "a nearby passage", False)
    assert (f.claimed_page, f.claimed_quote) == (4, "twenty ideal types of careers")


def test_extraction_does_not_tell_the_model_the_review_topic(tmp_path, monkeypatch):
    """Told the topic, the model answered [] for off-brief papers; relevance is score's job."""
    monkeypatch.setattr(facts, "_MIN_TEXT_CHARS", 0)
    seen = []

    class _B:
        def coordinator(self, prompt, system="", **kw):
            seen.append((prompt, system))
            return "[]"
    cfg = SimpleNamespace(topic="digital prosopography", focus="factoid model; ODD")
    facts.extract(_B(), [_cand(fulltext="[p.1]\nWind farms divide towns.")], cfg,
                  SimpleNamespace(work=tmp_path), None, {0: "abbott1990"})
    prompt, system = seen[0]
    assert "prosopography" not in prompt and "factoid" not in prompt
    assert "never return an empty array" in system


def test_retrieval_skips_title_pages_and_ocr_noise():
    from rabbithole.chroma import readable_chunk
    assert not readable_chunk("[p.3]\nDigitized by the Internet Archive \nIn 2023 with funding")
    assert not readable_chunk("[p.1]\n" + "nas ova i fe ae eeiat ibd Sree cee setae ame ey " * 10)
    assert readable_chunk("[p.4]\n" + "The factoid model records each assertion of a source "
                          "about a person, with the source and the place it was made. " * 3)


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
                               report_format="facts",
                               topics=[{"name": "Sequence analysis", "serves": [1]},
                                       {"name": "Factoid model", "serves": [2]}])

    class _Brain:
        backend = "ollama"

        def coordinator(self, prompt, system="", **kw):
            if "Paper: Measuring" in prompt:
                return json.dumps([{"fact": "Optimal matching found 20 career types.",
                                    "kind": "finding", "page": 2,
                                    "quote": "identified 20 ideal-type career patterns"}])
            if "Paper: Wind" in prompt:
                return json.dumps([{"fact": "Wind farms divide rural towns.", "kind": "finding",
                                    "page": 1, "quote": "Wind farms divide rural towns, a survey"}])
            if "Research topics:" in prompt:
                return json.dumps({"F1": [3, 0] if "Abbott" in prompt else [0, 0]})
            return json.dumps({"name": "Sequence typologies",
                               "synthesis": ["Abbott anchors it [@abbott1990]."]})

        def embed(self, text, max_chars=1500):
            return [1.0, 0.0]

    def _bib(cfg, gc, paths, citekeys, corpus):
        fp = paths.output / "refs.bib"
        fp.write_text("@article{abbott1990, title={M}, author={Abbott, A.}, year={1990}}\n")
        return fp

    monkeypatch.setattr(summarize, "_export_bibtex", _bib)
    monkeypatch.setattr(facts, "_MIN_TEXT_CHARS", 0)     # the fixtures are a sentence long
    monkeypatch.setattr(mindmap, "make_map", lambda *a, **k: {})
    gc = SimpleNamespace(zotero_library_type="user", zotero_library_id="1", contact_email="")
    assert facts.report(cfg, gc, paths, _Brain(), corpus, [{}, {}], citekeys, None) == 0
    md = next(out.glob("*_DigiPros_litreview_ra.md")).read_text()
    assert "[p.2](zotero://open-pdf/library/items/ATT1?page=2)" in md
    assert "### Sequence typologies" in md and "*Abbott anchors it [@abbott1990].*" in md
    assert "### Sources with no fact useful to a topic" in md
    assert "- **THIN** — *Q2: factoid model*." in md
    blob = json.loads((work / "facts.json").read_text())
    assert {f["id"]: f["scores"] for f in blob["facts"]} == {"abbott1990#1": [3, 0],
                                                              "zed2020#1": [0, 0]}
    # cached: a second run neither extracts nor scores
    calls = []
    b = _Brain()
    orig = b.coordinator
    b.coordinator = lambda p, s="", **k: (calls.append(p), orig(p, s, **k))[1]
    facts.report(cfg, gc, paths, b, corpus, [{}, {}], citekeys, None)
    assert not any("--- PAPER TEXT ---" in p or "Research topics:" in p for p in calls)


# ── dialling in: salvage, failures, names, ranking, sidecar, map date ─────────

def test_one_broken_element_no_longer_costs_the_paper_its_facts():
    raw = ('[{"fact": "A holds.", "kind": "finding", "page": 1, "quote": "q"},\n'
           ' {"fact": "B holds.", "quote": "an "unescaped" quote"},\n'
           ' {"fact": "C holds.", "kind": "method", "page": 2, "quote": "r"}]')
    fs = facts._parse_facts(raw, "k", 5)
    assert [f.text for f in fs] == ["A holds.", "C holds."]


def test_a_paper_with_no_facts_leaves_its_replies_for_diagnosis(tmp_path, monkeypatch):
    from rabbithole import summarize
    monkeypatch.setattr(facts, "_MIN_TEXT_CHARS", 0)     # the fixture is a sentence long
    paths = SimpleNamespace(work=tmp_path, root=tmp_path)
    corpus = [Candidate(title="Opaque", year=2020, fulltext="[p.1]\nSome text.")]
    cfg = SimpleNamespace(topic="t", focus="a; b")

    class _B:
        def coordinator(self, prompt, system="", **kw):
            return "I would rather not."
    out = facts.extract(_B(), corpus, cfg, paths, None, {0: "opaque2020"})
    assert out == {"opaque2020": []}
    failed = tmp_path / "facts" / f"{summarize._located_filename('opaque2020')}.failed.txt"
    assert failed.read_text().count("I would rather not.") == 2


def test_a_repeated_theme_name_is_made_distinct():
    fs1 = [Fact(id=f"a{i}#1", citekey=f"abbott199{i}", text="x") for i in range(2)]
    fs2 = [Fact(id=f"b{i}#1", citekey=f"kreuter200{i}", text="y") for i in range(2)]

    class _B:
        def coordinator(self, prompt, system="", **kw):
            assert "Other themes under this topic, already named:" in prompt
            return json.dumps({"name": "Trajectory archetypes", "synthesis": []})
    vecs = {f.id: ([1.0, 0.0] if f in fs1 else [0.0, 1.0]) for f in fs1 + fs2}
    out = facts.themes_for(_B(), 1, "trajectory archetypes", fs1 + fs2, vecs)
    names = [t.name for t in out]
    assert len(set(names)) == len(names) and all(n != "trajectory archetypes" for n in names)


def test_weight_on_the_brief_outranks_breadth_across_topics():
    """A specialist with ten central facts on one question's topic outranks a generalist
    spread thinly over three (the 2026-10-10 DigiPros top list had no prosopography source)."""
    topics = [Topic(name="Factoid", serves=[1]), Topic(name="Validation", serves=[2]),
              Topic(name="Docs", serves=[3])]
    narrow = [Fact(id=f"n#{i}", citekey="n", text="t", scores=[3, 0, 0], verified=True)
              for i in range(10)]
    broad = [Fact(id=f"b#{i}", citekey="b", text="t",
                  scores=[2 if j == i % 3 else 0 for j in range(3)], verified=True)
             for i in range(6)]
    assert facts.rank_key(narrow, topics) < facts.rank_key(broad, topics)
    # equal weight: the source serving more questions wins
    two = [Fact(id="w#1", citekey="w", text="t", scores=[3, 0, 0], verified=True),
           Fact(id="w#2", citekey="w", text="t", scores=[0, 3, 0], verified=True)]
    one = [Fact(id="o#1", citekey="o", text="t", scores=[3, 0, 0], verified=True),
           Fact(id="o#2", citekey="o", text="t", scores=[3, 0, 0], verified=True)]
    assert facts.rank_key(two, topics) < facts.rank_key(one, topics)


def test_a_topic_serving_no_question_carries_no_weight():
    topics = [Topic(name="Factoid", serves=[1]), Topic(name="Schelling", serves=[])]
    fs = {"pros": [Fact(id="p#1", citekey="pros", text="t", scores=[3, 0], verified=True)],
          "sch": [Fact(id="s#1", citekey="sch", text="t", scores=[0, 3], verified=True),
                  Fact(id="s#2", citekey="sch", text="t", scores=[2, 3], verified=True)]}
    assert facts.fact_weight(fs, topics) == {"pros": 3, "sch": 2}
    assert facts.rank_key(fs["pros"], topics) < facts.rank_key(fs["sch"], topics)
    assert facts.fact_weight(fs) == {"pros": 3, "sch": 6}      # no topics known: all count


def test_a_paper_without_a_text_layer_is_flagged_not_sent_to_the_model(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    paths = SimpleNamespace(work=work)
    scan = Candidate(title="Prosopography", year=1986, authors=[Author(family="Bulst")],
                     fulltext="[p.1]\n\n\n[p.2]\nElectronic copy available at ssrn\n")
    cfg = config.ProjectConfig(project_name="P", topic="t", focus="q")

    class _B:
        def coordinator(self, *a, **k):
            raise AssertionError("an image-only scan must not reach the model")
    no_text: dict = {}
    out = facts.extract(_B(), [scan], cfg, paths, None, {0: "bulst1986"}, no_text)
    assert out == {"bulst1986": []} and no_text == {"bulst1986": 29}
    assert not list(work.glob("facts/*.failed.txt"))
    base = FactBase(focus=["q"], facts=out, themes=[], topics=[Topic(name="q", serves=[1])],
                    no_text=no_text)
    gc = SimpleNamespace(zotero_library_type="user", zotero_library_id="1")
    md = facts.build_markdown(cfg, "m", base, [scan], {0: "bulst1986"}, gc, [], None)
    assert "### Sources with no usable text" in md
    assert "no usable text in the PDF — 29 characters; needs OCR" in md
    paths.work = work
    facts.save_sidecar(paths, base, [scan], {0: "bulst1986"}, gc)
    assert facts.load_sidecar(paths).no_text == {"bulst1986": 29}


def test_the_sidecar_says_which_sources_are_on_brief(tmp_path):
    paths = SimpleNamespace(work=tmp_path)
    corpus = [_cand(attachment_key="ATT1"), Candidate(title="Wind", year=2020)]
    gc = SimpleNamespace(zotero_library_type="user", zotero_library_id="1")
    facts.save_sidecar(paths, _base(), corpus, {0: "abbott1990", 1: "zed2020"}, gc)
    src = json.loads((tmp_path / "facts.json").read_text())["sources"]
    assert src["abbott1990"]["on_brief"] and not src["zed2020"]["on_brief"]
    assert src["abbott1990"] == {"on_brief": True, "facts": 2, "verbatim": 1,
                                 "zotero_key": "", "label": "Abbott, 1990"}
    assert facts.load_sidecar(paths).facts["zed2020"][0].text == "Wind farms divide towns."


def test_the_map_carries_its_drafts_datestamp():
    assert mindmap._draft_datestamp("261009_DigiPros_litreview_ra") == "261009"
    assert mindmap._draft_datestamp("digipros_litreview_ra") is None


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

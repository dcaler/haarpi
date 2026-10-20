"""Frozen tests for the `mindmap` verb (DESIGN_contribution_mindmap.md). Everything here is
GPU-free: the brain is mocked. These pin the contract before any live model run."""
from pathlib import Path

from rabbithole import mindmap


MD = """# Literature Review: household sorting

## Narrative Review

## Structural convenience drives compliance

Distance cuts missorting [@rousta2015]. Curbside helps too [@best2011][@rousta2015].

## Social norms amplify behaviour

Norms move high users [@allcott2011], but curbside still matters [@best2011].

## Annotated Bibliography

### Cited in the review

Rousta, K. (2015). Distance. [@rousta2015]
"""

BIB = """@article{rousta2015,
    title = {Distance and sorting},
    author = {Rousta, K. and Bolton, K. and Lundin, M.},
    year = {2015},
}
@article{best2011,
    author = {Best, Henning},
    year = {2011},
}
@incollection{allcott2011,
    author = {Allcott, Hunt},
    year = {2011},
}
@misc{noyear,
    author = {Smith, Jane},
}
"""


class FakeBrain:
    """A stand-in for the ollama coordinator: returns a fixed reply, counts calls."""
    def __init__(self, reply):
        self.reply, self.calls = reply, 0
        self.prompts: list[str] = []

    def coordinator(self, prompt, system="", **kw):
        self.calls += 1
        self.prompts.append(prompt)
        return self.reply


def test_parse_threads_themes_and_citekeys():
    ts = mindmap.parse_threads(MD)
    assert [t.theme for t in ts] == ["Structural convenience drives compliance",
                                     "Social norms amplify behaviour"]   # wrapper + bib excluded
    assert ts[0].citekeys == ["rousta2015", "best2011"]    # deduped, first-seen order
    assert ts[1].citekeys == ["allcott2011", "best2011"]


def test_the_load_bearing_front_block_is_not_a_theme():
    """`summarize.top_sources_block` heads every draft with the top 5% of sources and a line of
    rationale each. Read as review prose it opened a theme holding exactly the papers the map's
    innermost ring already names, and credited them the block's own words on top of their real
    ones — the same ranking drawn twice, one copy labelled as a thesis."""
    md = ("## Most load-bearing sources (top 5% of 184)\n\n"
          "- **Best 2011** [@best2011] — carries the compliance argument.\n\n"
          + MD.split("# Literature Review: household sorting\n", 1)[1])
    assert "Most load-bearing sources (top 5% of 184)" not in [t.theme
                                                               for t in mindmap.parse_threads(md)]
    ev = mindmap.citation_evidence(md)
    assert not any("carries the compliance argument" in s for s in ev["best2011"])
    assert mindmap.evidence_weight(md) == mindmap.evidence_weight(MD)   # the block adds no weight


def test_parse_threads_handles_both_multicite_styles():
    md = "## Theme A\n\nText [@keyA; @keyB] then [@keyC][@keyA] and [@keyB].\n"
    t = mindmap.parse_threads(md)[0]
    assert t.citekeys == ["keyA", "keyB", "keyC"]   # bracket + semicolon styles, deduped first-seen


def test_bib_keys_labels():
    k = mindmap.bib_keys(BIB)
    assert k["rousta2015"] == "Rousta et al. 2015"   # multiple authors -> et al.
    assert k["best2011"] == "Best 2011"              # single author
    assert k["noyear"] == "Smith"                    # missing year -> just surname, no crash
    braced = mindmap.bib_keys("@article{b,\n author = {{Bonjoc, X}},\n year = {2025},\n}\n")
    assert braced["b"] == "Bonjoc 2025"              # stray biblatex braces stripped from the label


def test_validate_enforces_the_grounding_law():
    valid = {"rousta2015": "Rousta 2015", "best2011": "Best 2011"}
    themes = ["Convenience", "Norms"]
    raw = {
        "papers": [
            {"key": "rousta2015", "theme": "Convenience", "phrase": "distance matters"},
            {"key": "GHOST", "theme": "Norms", "phrase": "invented"},              # dropped
            {"key": "best2011", "theme": "No such theme", "phrase": "curbside"},   # theme coerced
            {"key": "rousta2015", "theme": "Norms", "phrase": "dup"},              # duplicate dropped
        ],
        "edges": [
            {"src": "rousta2015", "dst": "best2011", "kind": "temporal"},
            {"src": "rousta2015", "dst": "GHOST", "kind": "influence"},    # dst invalid -> dropped
            {"src": "best2011", "dst": "rousta2015", "kind": "nonsense"},  # kind -> influence
        ],
    }
    m = mindmap.validate(raw, valid, themes)
    assert {p.key for p in m.papers} == {"rousta2015", "best2011"}          # invented + dup gone
    best = next(p for p in m.papers if p.key == "best2011")
    assert best.theme == "Convenience"                                     # coerced to a real theme
    assert best.label == "Best 2011"                                       # label from refs.bib
    pairs = {(e.src, e.dst): e.kind for e in m.edges}
    assert pairs.get(("rousta2015", "best2011")) == "temporal"
    assert ("rousta2015", "GHOST") not in pairs                            # ungrounded edge dropped
    assert pairs.get(("best2011", "rousta2015")) == "influence"            # unknown kind defaulted


def _sample_map():
    return mindmap.Mindmap(
        themes=["Convenience", "Norms"],
        papers=[mindmap.Paper("rousta2015", "Rousta 2015", "Convenience", "distance cuts missorting"),
                mindmap.Paper("allcott2011", "Allcott 2011", "Norms", "norms move high users")],
        edges=[mindmap.Edge("rousta2015", "allcott2011", "influence"),
               mindmap.Edge("allcott2011", "rousta2015", "temporal")],
    )


def test_the_map_is_one_self_contained_page():
    html = mindmap.to_html(_sample_map(), title="Test map", corpus_size=8)
    assert html.startswith("<!doctype html>") and "<title>Test map</title>" in html
    assert "vis-network" in html and "<script src" not in html       # script inlined: opens offline
    assert html.count("</script>") == 2                              # nothing inside closes a tag
    d = _data(html)
    assert {p["id"] for p in d["papers"]} == {"rousta2015", "allcott2011"}
    assert d["papers"][0]["phrase"] == "distance cuts missorting"     # the sentence rides the tooltip
    assert [(r["name"], r["cut"]) for r in d["rings"]] == [("top 5%", 1), ("top 25%", 2),
                                                           ("top 50%", 4)]
    assert d["corpus"] == 8 and "budget" not in html                  # a ring is not a cap


def _data(html):
    import json
    blob = html.split("const D = ", 1)[1].split(";\n", 1)[0]
    return json.loads(blob.replace("<\\/", "</"))


def test_band_layout_bands_by_importance_with_exact_quantile_rings():
    # 8 papers, descending importance; rings at ranks 2 and 5 must enclose exactly 2 and 5.
    papers = [mindmap.Paper(f"p{i}", f"A{i}", "T", "", importance=100 - i) for i in range(8)]
    m = mindmap.Mindmap(themes=["T"], papers=papers, edges=[])
    pos, circle_r, _label, outer = mindmap.band_layout(m, [2, 5])
    r = lambda k: (pos[k][0] ** 2 + pos[k][1] ** 2) ** 0.5
    assert r("p0") < r("p7")                                      # most-important nearer the centre
    inside2 = sum(1 for i in range(8) if r(f"p{i}") < circle_r[0])
    inside5 = sum(1 for i in range(8) if r(f"p{i}") < circle_r[1])
    assert inside2 == 2 and inside5 == 5                          # rings hold exactly their cuts
    assert circle_r[0] < circle_r[1] < outer


def test_band_layout_draws_a_ring_per_cut():
    """Three quantiles -> three rings and four bands, not the old fixed two."""
    papers = [mindmap.Paper(f"p{i}", f"A{i}", "T", "", importance=100 - i) for i in range(20)]
    m = mindmap.Mindmap(themes=["T"], papers=papers, edges=[])
    _pos, circle_r, _label, outer = mindmap.band_layout(m, [1, 5, 10])
    assert len(circle_r) == 3
    assert circle_r[0] < circle_r[1] < circle_r[2] < outer


def test_band_cuts_are_corpus_quantiles_and_strictly_increasing():
    assert mindmap.band_cuts(181) == [9, 45, 90]                  # 5% / 25% / 50%
    assert mindmap.band_cuts(8) == [1, 2, 4]
    # a tiny corpus rounds several quantiles onto one rank; collapse rather than stack rings
    assert mindmap.band_cuts(2) == [1]
    assert mindmap.band_cuts(0) == [1]
    assert mindmap.band_cuts(181) == sorted(set(mindmap.band_cuts(181)))


def test_band_layout_has_no_overlapping_dots():
    # varied sizes (via cited_by) across two slices; the packing must separate every pair.
    papers = [mindmap.Paper(f"p{i}", f"Author {i} 20{i:02d}", "A" if i % 2 else "B",
                            "a contribution phrase", cited_by=10 ** (i % 4), importance=50 - i)
              for i in range(16)]
    m = mindmap.Mindmap(themes=["A", "B"], papers=papers, edges=[])
    pos, *_ = mindmap.band_layout(m, [4, 10])
    r = {p.key: mindmap._node_size(p.cited_by) for p in papers}
    for i in range(16):
        for j in range(i + 1, 16):
            (xi, yi), (xj, yj) = pos[f"p{i}"], pos[f"p{j}"]
            assert ((xi - xj) ** 2 + (yi - yj) ** 2) ** 0.5 >= r[f"p{i}"] + r[f"p{j}"] - 1, \
                f"p{i}/p{j} overlap"


def test_a_digipros_sized_map_stays_compact():
    """224 sources in 10 topic slices. Packing label boxes and scaling until none overlapped made
    DigiPros' map 64,000 pt square; dots without text must pack into a canvas you can see whole."""
    papers = [mindmap.Paper(f"p{i}", f"A{i} 2020", f"T{i % 10}", "x" * 120,
                            cited_by=(i * 37) % 900, importance=300 - i) for i in range(224)]
    m = mindmap.Mindmap(themes=[f"T{k}" for k in range(10)], papers=papers, edges=[])
    _pos, _rings, _labels, outer = mindmap.band_layout(m, mindmap.band_cuts(226))
    assert outer < 1500


def test_each_slice_gets_its_palette_hue_and_every_paper_appears():
    papers = [mindmap.Paper(f"p{i}", f"A{i} 20{i:02d}", "AB"[i % 2], f"phrase {i}")
              for i in range(8)]
    d = mindmap.map_data(mindmap.Mindmap(themes=["A", "B"], papers=papers, edges=[]))
    assert [s["hue"] for s in d["slices"]] == [mindmap._PALETTE[0][0], mindmap._PALETTE[1][0]]
    assert [s["papers"] for s in d["slices"]] == [4, 4]
    assert len(d["papers"]) == 8                                      # every paper, none collapsed


def test_bands_follow_the_ranking():
    papers = [mindmap.Paper(f"p{i}", f"A{i}", "T", "", importance=100 - i) for i in range(8)]
    d = mindmap.map_data(mindmap.Mindmap(themes=["T"], papers=papers, edges=[]), corpus_size=8)
    band = {p["id"]: p["band"] for p in d["papers"]}
    assert band["p0"] == 0 and band["p1"] == 1 and band["p2"] == band["p3"] == 2
    assert band["p7"] == 3 and d["bands"][-1] == "the rest"


def test_details_reach_the_click_panel_and_nothing_else_does():
    d = mindmap.map_data(_sample_map(), details={"rousta2015": {
        "facts": [{"text": "f", "where": "p.2", "link": "zotero://open-pdf/x"}],
        "zotero": "zotero://select/library/items/K", "secret": "dropped"}})
    p = next(p for p in d["papers"] if p["id"] == "rousta2015")
    assert p["facts"][0]["link"] == "zotero://open-pdf/x" and p["zotero"].endswith("/K")
    assert "secret" not in p


def test_a_closing_tag_in_the_data_cannot_end_the_script():
    m = mindmap.Mindmap(themes=["T"], papers=[mindmap.Paper("k", "K 1", "T",
                                                            "</script><b>x</b>")], edges=[])
    html = mindmap.to_html(m)
    assert html.count("</script>") == 2 and _data(html)["papers"][0]["phrase"] == "</script><b>x</b>"


def test_evidence_weight_measures_review_prose_per_paper():
    w = mindmap.evidence_weight(MD)
    # best2011 is cited in two sentences (more total words) than rousta2015; the measure is by words
    assert w["best2011"] > w["rousta2015"] > 0                    # more prose devoted -> higher importance
    assert "noyear" not in w                                      # only papers the review actually cites


def test_clean_phrase_strips_the_stray_brace_bug():
    m = mindmap.validate(
        {"papers": [{"key": "k", "theme": "T", "phrase": "{Bonjoc 2025 explores frameworks*"}]},
        {"k": "Bonjoc 2025"}, ["T"])
    assert m.papers[0].phrase == "Bonjoc 2025 explores frameworks"   # leading { and trailing * gone


def test_bib_dois_parses_and_normalises():
    bib = ("@article{a,\n doi = {10.1000/AbC},\n year={2020},\n}\n"
           "@article{b,\n doi = {https://doi.org/10.5/x},\n}\n"
           "@article{c,\n year={2019},\n}\n")
    d = mindmap.bib_dois(bib)
    assert d == {"a": "10.1000/abc", "b": "10.5/x"}   # lowercased, url prefix stripped, no-doi omitted


def test_citation_evidence_pulls_citing_sentences_not_the_bibliography():
    ev = mindmap.citation_evidence(MD)
    assert len(ev["rousta2015"]) == 2                              # both citing sentences, capped at 2
    assert any("Distance cuts missorting" in s for s in ev["rousta2015"])
    assert all("[@" not in s and "Rousta, K." not in s and "#" not in s for s in ev["rousta2015"])
    # best2011 is cited in two sentences; tags stripped, no space left before the period, no heading text
    assert ev["best2011"] == ["Curbside helps too.",
                              "Norms move high users, but curbside still matters."]


def test_citation_edges_are_the_real_reference_graph():
    papers = [mindmap.Paper(k, k, "T", "") for k in ("A", "B", "C")]
    dois = {"A": "10/a", "B": "10/b", "C": "10/c"}
    works = [
        {"id": "https://openalex.org/W1", "doi": "https://doi.org/10/A",   # A cites B and a non-corpus work
         "referenced_works": ["https://openalex.org/W2", "https://openalex.org/Wx"]},
        {"id": "https://openalex.org/W2", "doi": "https://doi.org/10/b", "referenced_works": []},
        {"id": "https://openalex.org/W3", "doi": "https://doi.org/10/c",   # C cites A
         "referenced_works": ["https://openalex.org/W1"]},
    ]
    edges = mindmap.citation_edges(papers, dois, "me@x", fetch=lambda ds, email: works)
    pairs = {(e.src, e.dst, e.kind) for e in edges}
    assert pairs == {("A", "B", "cites"), ("C", "A", "cites")}     # real refs only, non-corpus dropped


def test_citation_graph_also_returns_world_citation_counts():
    papers = [mindmap.Paper(k, k, "T", "") for k in ("A", "B")]
    works = [{"id": "https://openalex.org/W1", "doi": "https://doi.org/10/a",
              "referenced_works": ["https://openalex.org/W2"], "cited_by_count": 1200},
             {"id": "https://openalex.org/W2", "doi": "https://doi.org/10/b",
              "referenced_works": [], "cited_by_count": 3}]
    edges, world = mindmap.citation_graph(papers, {"A": "10/a", "B": "10/b"}, "me@x",
                                          fetch=lambda ds, email: works)
    assert {(e.src, e.dst) for e in edges} == {("A", "B")}
    assert world == {"A": 1200, "B": 3}                            # OpenAlex cited_by_count per paper


def test_dot_size_and_ring_encode_citations():
    # A is highly cited (big) and cited within the corpus (ring); B is neither.
    m = mindmap.Mindmap(themes=["T"],
                        papers=[mindmap.Paper("A", "A 1", "T", "", cited_by=1000),
                                mindmap.Paper("B", "B 2", "T", "", cited_by=0)],
                        edges=[mindmap.Edge("B", "A", "cites")])   # B cites A -> A gets a ring
    d = {p["id"]: p for p in mindmap.map_data(m)["papers"]}
    assert d["A"]["size"] > d["B"]["size"] == 4.0                   # log-scaled, floor at 4
    assert d["A"]["border"] > 0 and d["B"]["border"] == 0
    assert mindmap.map_data(m)["edges"] == [{"from": "B", "to": "A", "kind": "cites"}]
    assert "cites" in mindmap.map_data(m)["kinds"]                  # the legend decodes it


def test_validate_reads_the_finding_key():
    m = mindmap.validate({"papers": [{"key": "k", "theme": "T", "finding": "cut missorting 55%->39%"}]},
                         {"k": "K 2020"}, ["T"])
    assert m.papers[0].phrase == "cut missorting 55%->39%"        # new 'finding' key honored


def test_build_map_with_fake_brain_is_grounded():
    reply = """```json
    {"papers": [{"key": "rousta2015", "theme": "Structural convenience drives compliance",
                 "phrase": "distance to bins cuts missorting"},
                {"key": "GHOST", "theme": "Social norms amplify behaviour", "phrase": "invented"}],
     "edges": [{"src": "rousta2015", "dst": "GHOST", "kind": "influence"}]}
    ```"""
    m = mindmap.build_map(MD, BIB, FakeBrain(reply))
    assert [p.key for p in m.papers] == ["rousta2015"]   # grounding dropped the invented paper
    assert m.edges == [] and m.papers[0].importance > 0


def test_emit_writes_a_chain_named_page_into_output_not_the_figures_pool(tmp_path):
    reply = ('{"papers": [{"key": "rousta2015", "theme": "Structural convenience drives compliance",'
             ' "phrase": "distance cuts missorting"}], "edges": []}')
    html = mindmap.to_html(mindmap.build_map(MD, BIB, FakeBrain(reply)), title="Map")
    res = mindmap.emit(tmp_path, "consGateII", html, datestamp="261020")
    assert res["html"] == tmp_path / "261020_consGateII_litmap_ra.html" and res["html"].exists()
    assert not (tmp_path / "figures").exists()              # never the paper's figures pool
    assert not list(tmp_path.glob("*.png")) and not list(tmp_path.glob("*.svg"))


def test_make_map_can_pin_each_paper_to_the_slice_that_lists_it(tmp_path):
    from types import SimpleNamespace
    threads = [mindmap.Thread(theme="T1. A", citekeys=["rousta2015"]),
               mindmap.Thread(theme="T2. B", citekeys=["best2011"])]
    reply = ('[{"key": "rousta2015", "theme": "T2. B", "contribution": "x"},'
             ' {"key": "best2011", "theme": "T2. B", "contribution": "y"}]')
    gc = SimpleNamespace(contact_email="")
    orig = mindmap.citation_graph
    mindmap.citation_graph = lambda *a, **k: ([], {})       # offline
    try:
        res = mindmap.make_map(FakeBrain(reply), gc, tmp_path, "X", threads, BIB, {}, {},
                               datestamp="261020", pin_slices=True)
    finally:
        mindmap.citation_graph = orig
    d = _data(res["html"].read_text())
    names = [s["name"] for s in d["slices"]]
    assert names == ["T1. A", "T2. B"]
    assert {p["id"]: names[p["slice"]] for p in d["papers"]} == {"rousta2015": "T1. A",
                                                                   "best2011": "T2. B"}


def test_compose_survives_a_garbage_reply():
    threads = mindmap.parse_threads(MD)
    keys = mindmap.bib_keys(BIB)
    fb = FakeBrain("there is no json in here at all")
    m = mindmap.compose(fb, threads, keys)
    assert m.papers == [] and m.edges == []           # graceful empty, never an exception
    # one try + one repair PER BATCH; this fixture has two themes, so two batches
    assert fb.calls == 2 * len(mindmap.parse_threads(MD))


def test_compose_batches_so_one_call_never_carries_the_whole_review():
    """A single call carrying 160 papers built a 14.7k-token prompt needing ~5.6k tokens back,
    against a 16k window; it truncated and the map shipped empty."""
    threads = [mindmap.Thread(theme="T", citekeys=[f"k{i}" for i in range(12)])]
    keys = {f"k{i}": f"A{i} 2020" for i in range(12)}
    fb = FakeBrain("[]")
    mindmap.compose(fb, threads, keys, repair=False, batch_size=5)
    assert fb.calls == 3, "12 papers at batch_size=5 is three calls"
    assert all(p.count("- k") <= 5 for p in fb.prompts), "no call carries more than the batch"


def test_a_failing_batch_costs_only_its_own_papers():
    """Batching contains failure: an unparseable batch must not empty the whole map."""
    class _Flaky:
        def __init__(self): self.n = 0
        def coordinator(self, prompt, sys, **kw):
            self.n += 1
            if "k0" in prompt:
                return "no json here"                      # first batch is unparseable
            return '[{"key": "k5", "theme": "T", "contribution": "a thing we now know"}]'
    threads = [mindmap.Thread(theme="T", citekeys=["k0", "k5"])]
    keys = {"k0": "A0 2020", "k5": "A5 2020"}
    m = mindmap.compose(_Flaky(), threads, keys, repair=False, batch_size=1)
    assert [p.key for p in m.papers] == ["k5"], "the surviving batch still lands"


def test_a_paper_cited_in_several_themes_is_composed_once():
    threads = [mindmap.Thread(theme="A", citekeys=["k1"]),
               mindmap.Thread(theme="B", citekeys=["k1"])]
    keys = {"k1": "A1 2020"}
    fb = FakeBrain('[{"key": "k1", "theme": "A", "contribution": "a thing we now know"}]')
    m = mindmap.compose(fb, threads, keys, repair=False)
    assert fb.calls == 1, "the second theme has nothing left to compose"
    assert [p.key for p in m.papers] == ["k1"]


def test_parse_spec_accepts_a_bare_top_level_array():
    # a reasoning model routinely drops the {"papers":...} wrapper and returns just the list; the old
    # object-only regex spanned first-{ to last-} across two objects → invalid JSON → silent stub.
    reply = ('[{"key": "rousta2015", "theme": "T", "contribution": "distance cuts missorting"},\n'
             ' {"key": "best2011", "theme": "T", "contribution": "curbside helps"}]')
    out = mindmap.parse_spec(reply)
    assert [p["key"] for p in out["papers"]] == ["rousta2015", "best2011"]


def test_parse_spec_accepts_a_fenced_array():
    reply = '```json\n[{"key": "rousta2015", "theme": "T", "contribution": "x"}]\n```'
    assert mindmap.parse_spec(reply)["papers"][0]["key"] == "rousta2015"


def test_compose_grounds_papers_from_an_array_reply():
    # the end-to-end regression: an array reply must yield a grounded map, not a stub.
    threads = mindmap.parse_threads(MD)
    keys = mindmap.bib_keys(BIB)
    reply = '[{"key": "rousta2015", "theme": "Structural convenience drives compliance", "contribution": "distance cuts missorting"}]'
    m = mindmap.compose(FakeBrain(reply), threads, keys)
    assert [p.key for p in m.papers] == ["rousta2015"]

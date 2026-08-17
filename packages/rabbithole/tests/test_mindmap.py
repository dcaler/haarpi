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

    def coordinator(self, prompt, system="", **kw):
        self.calls += 1
        return self.reply


def test_parse_threads_themes_and_citekeys():
    ts = mindmap.parse_threads(MD)
    assert [t.theme for t in ts] == ["Structural convenience drives compliance",
                                     "Social norms amplify behaviour"]   # wrapper + bib excluded
    assert ts[0].citekeys == ["rousta2015", "best2011"]    # deduped, first-seen order
    assert ts[1].citekeys == ["allcott2011", "best2011"]


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


def test_to_dot_is_a_pinned_banded_map_that_renders():
    dot = mindmap.to_dot(_sample_map(), title="Test map", target_min=1, target_max=2)
    assert "digraph litmap {" in dot                              # pinned digraph (render: dot -Kneato -n2)
    assert '"__hub__"' in dot and "Contribution map" not in dot   # centre hub carries the title
    assert '"__t0__"' in dot and '"__t1__"' in dot                # a theme label node per theme, outside
    assert '"__ring1__"' in dot and '"__ring2__"' in dot          # the two red target rings
    assert '"rousta2015"' in dot and '"allcott2011"' in dot       # a node per paper
    assert '!"' in dot and dot.count('pos="') >= 5                # everything pinned
    assert "distance cuts" in dot and "tooltip" not in dot        # phrase in the node, no tooltips
    assert 'color="#dc2626"' in dot and "target 1" in dot         # red rings + their labels
    assert "target reference budget" in dot                       # legend explains the rings
    assert mindmap._render_pinned(dot, tmp := Path(__file__).parent / "_x.svg") and tmp.unlink() is None


def test_band_layout_bands_by_importance_with_exact_target_rings():
    # 8 papers, descending importance; target rings at 2 and 5 must enclose exactly 2 and 5.
    papers = [mindmap.Paper(f"p{i}", f"A{i}", "T", "", importance=100 - i) for i in range(8)]
    m = mindmap.Mindmap(themes=["T"], papers=papers, edges=[])
    pos, circle_r, _label, outer = mindmap.band_layout(m, target_min=2, target_max=5)
    r = lambda k: (pos[k][0] ** 2 + pos[k][1] ** 2) ** 0.5
    assert r("p0") < r("p7")                                      # most-important nearer the centre
    inside2 = sum(1 for i in range(8) if r(f"p{i}") < circle_r[0])
    inside5 = sum(1 for i in range(8) if r(f"p{i}") < circle_r[1])
    assert inside2 == 2 and inside5 == 5                          # rings hold exactly target_min/target_max
    assert circle_r[0] < circle_r[1] < outer


def test_band_layout_has_no_overlapping_boxes():
    # varied sizes (via cited_by) across two themes; the collision-scale must separate every pair.
    papers = [mindmap.Paper(f"p{i}", f"Author {i} 20{i:02d}", "A" if i % 2 else "B",
                            "a contribution phrase that wraps onto a few lines here",
                            cited_by=10 ** (i % 4), importance=50 - i) for i in range(16)]
    m = mindmap.Mindmap(themes=["A", "B"], papers=papers, edges=[])
    pos, *_ = mindmap.band_layout(m, target_min=4, target_max=10)
    ext = {p.key: mindmap._extents(p) for p in papers}
    for i in range(16):
        for j in range(i + 1, 16):
            (xi, yi), (wi, hi) = pos[f"p{i}"], ext[f"p{i}"]
            (xj, yj), (wj, hj) = pos[f"p{j}"], ext[f"p{j}"]
            assert abs(xi - xj) >= (wi + wj) / 2 - 1 or abs(yi - yj) >= (hi + hj) / 2 - 1, \
                f"p{i}/p{j} overlap"


def test_to_dot_colors_each_theme_from_the_palette():
    dot = mindmap.to_dot(_sample_map(), title="t")
    hue0, tint0 = mindmap._PALETTE[0]
    hue1, _ = mindmap._PALETTE[1]
    assert f'fillcolor="{hue0}"' in dot and f'fillcolor="{hue1}"' in dot   # distinct theme hues
    assert f'fillcolor="{tint0}"' in dot                          # papers take the theme's pale tint


def test_all_papers_appear_no_collapse():
    papers = [mindmap.Paper(f"p{i}", f"A{i} 20{i:02d}", "T", f"phrase {i}") for i in range(8)]
    m = mindmap.Mindmap(themes=["T"], papers=papers, edges=[])
    dot = mindmap.to_dot(m)
    assert all(f'"p{i}"' in dot for i in range(8)) and "_more" not in dot   # every paper, none collapsed


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


def test_spec_from_map_renders_all_papers():
    spec = mindmap.spec_from_map(_sample_map(), title="t")
    assert spec.provenance["papers"] == 2 and spec.provenance["themes"] == 2
    assert mindmap._renders(spec.source)


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


def test_node_size_and_ring_encode_citations():
    # A is highly cited (big) and cited within the corpus (ring); B is neither.
    m = mindmap.Mindmap(themes=["T"],
                        papers=[mindmap.Paper("A", "A 1", "T", "", cited_by=1000),
                                mindmap.Paper("B", "B 2", "T", "", cited_by=0)],
                        edges=[mindmap.Edge("B", "A", "cites")])   # B cites A -> A gets a ring
    dot = mindmap.to_dot(m)
    big, small = mindmap._node_fontsize(1000), mindmap._node_fontsize(0)
    assert big > small and small == 8.0                            # log-scaled size, floor at 8pt
    a_line = next(ln for ln in dot.splitlines() if ln.strip().startswith('"A" ['))
    assert f"fontsize={big}" in a_line and 'color="#0f172a"' in a_line   # big + black ring (in-corpus cited)
    b_line = next(ln for ln in dot.splitlines() if ln.strip().startswith('"B" ['))
    assert "penwidth=0]" in b_line                                 # B has no in-corpus citations: no ring
    assert mindmap._renders(dot)


def test_cites_edges_render_with_a_citation_legend():
    m = mindmap.Mindmap(themes=["T"],
                        papers=[mindmap.Paper("A", "A 1", "T", ""), mindmap.Paper("B", "B 2", "T", "")],
                        edges=[mindmap.Edge("A", "B", "cites")])
    dot = mindmap.to_dot(m)
    assert '"A" -> "B"' in dot and "arrowsize" in dot              # a directed citation arrow
    assert "cites — A cites B" in dot                             # legend decodes it
    assert mindmap._renders(dot)


def test_validate_reads_the_finding_key():
    m = mindmap.validate({"papers": [{"key": "k", "theme": "T", "finding": "cut missorting 55%->39%"}]},
                         {"k": "K 2020"}, ["T"])
    assert m.papers[0].phrase == "cut missorting 55%->39%"        # new 'finding' key honored


def test_build_spec_with_fake_brain_is_grounded_and_renders():
    reply = """```json
    {"papers": [{"key": "rousta2015", "theme": "Structural convenience drives compliance",
                 "phrase": "distance to bins cuts missorting"},
                {"key": "GHOST", "theme": "Social norms amplify behaviour", "phrase": "invented"}],
     "edges": [{"src": "rousta2015", "dst": "GHOST", "kind": "influence"}]}
    ```"""
    spec = mindmap.build_spec(MD, BIB, FakeBrain(reply), title="Contribution map")
    assert spec.format == "dot" and spec.id == "litmap" and spec.kind == "mindmap"
    assert "rousta2015" in spec.source
    assert "GHOST" not in spec.source                 # grounding dropped the invented paper + its edge
    assert mindmap._renders(spec.source)
    assert spec.provenance["papers"] == 1             # only the grounded paper survived


def test_emit_writes_into_output_not_the_figures_pool(tmp_path):
    reply = ('{"papers": [{"key": "rousta2015", "theme": "Structural convenience drives compliance",'
             ' "phrase": "distance cuts missorting"}], "edges": []}')
    spec = mindmap.build_spec(MD, BIB, FakeBrain(reply), title="Map")
    res = mindmap.emit(tmp_path, "consGateII", spec)
    assert res["source"].parent == tmp_path                 # written into the given output dir
    assert res["source"].suffix == ".dot" and res["source"].exists()
    assert "litmap" in res["source"].name and res["source"].name.endswith("_ra.dot")   # chain draft
    assert not (tmp_path / "figures").exists()              # never the paper's figures pool


def test_compose_survives_a_garbage_reply():
    threads = mindmap.parse_threads(MD)
    keys = mindmap.bib_keys(BIB)
    fb = FakeBrain("there is no json in here at all")
    m = mindmap.compose(fb, threads, keys)
    assert m.papers == [] and m.edges == []           # graceful empty, never an exception
    assert fb.calls == 2                              # tried once, then the repair pass

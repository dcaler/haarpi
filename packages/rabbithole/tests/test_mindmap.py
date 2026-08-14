"""Frozen tests for the `mindmap` verb (DESIGN_contribution_mindmap.md). Everything here is
GPU-free: the brain is mocked. These pin the contract before any live model run."""
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


def test_to_dot_is_valid_and_renders():
    m = mindmap.Mindmap(
        themes=["Convenience", "Norms"],
        papers=[mindmap.Paper("rousta2015", "Rousta 2015", "Convenience", "distance cuts missorting"),
                mindmap.Paper("allcott2011", "Allcott 2011", "Norms", "norms move high users")],
        edges=[mindmap.Edge("rousta2015", "allcott2011", "influence"),
               mindmap.Edge("allcott2011", "rousta2015", "temporal")],
    )
    dot = mindmap.to_dot(m, title="Test map")
    assert "digraph litmap" in dot
    assert "cluster_t0" in dot and "cluster_t1" in dot            # one cluster per theme
    assert '"rousta2015"' in dot and '"allcott2011"' in dot       # a node per paper
    assert mindmap._renders(dot)                                  # compiles under dot -Tsvg


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

"""The fact-base review page: what a person assesses a litreview on instead of a redline."""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

from rabbithole import facts, mindmap, review_page
from rabbithole.facts import Fact, FactBase, Theme, Topic
from rabbithole.models import Author, Candidate


def _base(n_good: int = 20) -> FactBase:
    good = [Fact(id=f"k{i}#1", citekey=f"k{i}", text=f"Fact {i}.", page=i + 1,
                 quote=f"quote {i}", verified=True, scores=[3 if i % 2 else 2, 0])
            for i in range(n_good)]
    weak = Fact(id="w#1", citekey="w", text="Searched passage.", scores=[3, 0], support=0.9)
    bg = Fact(id="b#1", citekey="b", text="Background.", scores=[1, 1], verified=True)
    by: dict[str, list[Fact]] = {}
    for f in good + [weak, bg]:
        by.setdefault(f.citekey, []).append(f)
    return FactBase(focus=["How do firms fund?"], facts=by,
                    themes=[Theme(item=1, name="Funding order", fact_ids=["k1#1"],
                                  synthesis=["Order matters [@k1]."])],
                    topics=[Topic(name="Funding", serves=[1]), Topic(name="NK", serves=[])])


def _project(tmp_path, n_good: int = 20):
    work, out = tmp_path / "work", tmp_path / "output"
    work.mkdir()
    out.mkdir()
    paths = SimpleNamespace(work=work, output=out)
    base = _base(n_good)
    corpus = [Candidate(title=f"<i>Paper</i> {ck}", year=2000,
                        authors=[Author(family="Smith", given="A")], citekey=ck)
              for ck in base.facts]
    gc = SimpleNamespace(zotero_library_type="user", zotero_library_id="1")
    facts.save_sidecar(paths, base, corpus, {i: c.citekey for i, c in enumerate(corpus)}, gc)
    (work / "corpus.json").write_text(json.dumps([c.to_dict() if hasattr(c, "to_dict")
                                                  else vars(c) for c in corpus], default=str))
    (out / "261101_Firm_litreview_ra.md").write_text("draft")
    return paths


def _data(page: str) -> dict:
    return json.loads(re.search(r"const D = (.*);\n", page).group(1).replace("<\\/", "</"))


def test_the_spot_check_samples_only_counted_facts_and_is_stable_per_draft(tmp_path):
    paths = _project(tmp_path)
    a = review_page.page_data(paths, "261101_Firm_review_ra")["spot"]
    b = review_page.page_data(paths, "261101_Firm_review_ra")["spot"]
    c = review_page.page_data(paths, "261201_Firm_review_ra")["spot"]
    assert len(a) == review_page.SPOT_N and a == b
    assert {f["id"] for f in a} != {f["id"] for f in c}       # a new draft draws new facts
    assert not {"w#1", "b#1"} & {f["id"] for f in a}          # unverified and background: never


def test_topics_carry_counts_themes_and_the_questions_they_serve(tmp_path):
    d = review_page.page_data(_project(tmp_path), "261101_Firm_review_ra")
    t1, t2 = d["topics"]
    assert (t1["central"], t1["useful"], t1["sources"]) == (10, 10, 20)
    assert t1["themes"] == [{"name": "Funding order", "facts": 1, "sources": 1}]   # no bullets
    assert d["questions"][0]["topics"] == [1] and t2["serves"] == []
    assert all("<i>" not in c["title"] for c in d["corpus"])
    assert d["needs"]["topic"]["thin"] == "sources" and d["needs"]["map"]["false_friend"] == "drop"


def test_every_need_is_a_fact_base_comment_kind():
    from haarpi import comment_kinds
    names = set(comment_kinds.LITREVIEW_FACTS.names)
    used = {n for kind in review_page.NEEDS.values() for n in kind.values() if n}
    assert used - {"sort"} <= names       # "sort": the note is sorted like a docx comment


class _Brain:
    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    def coordinator(self, prompt, *a, **k):
        self.prompts.append(prompt)
        return self.replies.pop(0)


def test_restating_renames_themes_as_claims_in_the_sidecar_only(tmp_path):
    paths = _project(tmp_path)
    blob = json.loads((paths.work / "facts.json").read_text())
    blob["themes"].append(dict(blob["themes"][0], name="Second"))
    (paths.work / "facts.json").write_text(json.dumps(blob))
    brain = _Brain('{"name": "Grants first de-risks firms"}', '{"name": "grants first de-risks firms!"}')
    assert facts.restate_themes(brain, paths) == 1
    after = json.loads((paths.work / "facts.json").read_text())
    assert [t["name"] for t in after["themes"]] == ["Grants first de-risks firms", "Second"]
    assert after["themes"][0]["synthesis"] == blob["themes"][0]["synthesis"]
    assert "[@k1] Fact 1." in brain.prompts[0] and "Grants first" in brain.prompts[1]


def test_write_names_the_page_after_the_draft_and_embeds_the_map(tmp_path):
    paths = _project(tmp_path)
    m = mindmap.Mindmap(themes=["A"], papers=[mindmap.Paper("k1", "Smith 2000", "A", "x")],
                        edges=[])
    (paths.output / "261101_Firm_litmap_ra.html").write_text(mindmap.to_html(m))
    page = review_page.write(paths)
    assert page.name == "261101_Firm_review_ra.html"
    d = _data(page.read_text())
    assert d["litmap"] == "261101_Firm_litmap_ra.html"
    assert "261101_Firm_review_ra_DCR.json" in page.read_text()


def test_reskin_keeps_the_map_data_and_rebuilds_the_template(tmp_path):
    m = mindmap.Mindmap(themes=["A"], papers=[mindmap.Paper("k1", "Smith 2000", "A", "x")],
                        edges=[])
    fp = tmp_path / "map.html"
    html = mindmap.to_html(m, title="My map")
    fp.write_text(html.replace("const EMBEDDED", "const OLD_TEMPLATE"))
    assert mindmap.reskin(fp)
    new = fp.read_text()
    assert "const EMBEDDED" in new and "<title>My map</title>" in new
    old_data = re.findall(r"^const D = (.*);$", html, re.M)[-1]
    assert re.findall(r"^const D = (.*);$", new, re.M)[-1] == old_data
    other = tmp_path / "x.html"
    other.write_text("<html>not a map</html>")
    assert not mindmap.reskin(other) and other.read_text() == "<html>not a map</html>"


def test_off_brief_drops_the_paper_and_unreadable_papers_are_not_offered(tmp_path):
    assert review_page.NEEDS["spot"]["off_brief"] == "drop"
    paths = _project(tmp_path)
    blob = json.loads((paths.work / "facts.json").read_text())
    blob["unreadable"] = ["k3"]
    (paths.work / "facts.json").write_text(json.dumps(blob))
    d = review_page.page_data(paths, "261101_Firm_review_ra")
    assert "k3" not in {c["ck"] for c in d["corpus"]} and "k4" in {c["ck"] for c in d["corpus"]}

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
    assert t1["themes"][0]["synthesis"] == ["Order matters [@k1]."]
    assert d["questions"][0]["topics"] == [1] and t2["serves"] == []
    assert all("<i>" not in c["title"] for c in d["corpus"])
    assert d["needs"]["topic"]["thin"] == "sources" and d["needs"]["map"]["false_friend"] == "drop"


def test_every_need_is_a_fact_base_comment_kind():
    from haarpi import comment_kinds
    names = set(comment_kinds.LITREVIEW_FACTS.names)
    used = {n for kind in review_page.NEEDS.values() for n in kind.values() if n}
    assert used <= names


class _Brain:
    def __init__(self, reply):
        self.reply, self.calls = reply, 0

    def coordinator(self, *a, **k):
        self.calls += 1
        return self.reply


def test_an_answer_citing_outside_its_facts_is_discarded(tmp_path):
    paths = _project(tmp_path)
    got = review_page.answer_questions(_Brain("Firms sequence grants [@nobody2020]."), paths)
    assert got["1"]["text"] == "" and "nobody2020" in got["1"]["note"]


def test_a_good_answer_is_kept_and_cached_on_its_facts(tmp_path):
    paths = _project(tmp_path)
    brain = _Brain("Order matters [@k1]. The facts do not settle debt [@k3].")
    assert review_page.answer_questions(brain, paths)["1"]["text"].startswith("Order matters")
    review_page.answer_questions(brain, paths)
    assert brain.calls == 1


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

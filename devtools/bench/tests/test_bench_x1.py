"""X1, the extraction bake-off: automatic scores, a blind page, and unblinding."""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest

import bench


def _fact(text, quote="", verified=True, bears_on=()):
    return SimpleNamespace(text=text, quote=quote, verified=verified, bears_on=list(bears_on))


def test_the_automatic_scores_catch_copy_back_unresolved_references_and_definitions():
    papers = [{"citekey": "a", "full": "[p.1]\nCrossover has no benefits for the search 12."},
              {"citekey": "b", "full": "[p.1]\nx"}]
    got = {"a": [_fact("Given the current formulation, crossover has no benefits for the search.",
                       "given the current formulation crossover has no benefits for the search"),
                 _fact("Fitness is defined as survival.", "unrelated words entirely", False, [1]),
                 _fact("In SBSE, crossover gave 99 gains.", "q", True)]}
    m = bench.x1_metrics(papers, got, {"b": "off-question"}, 30.0)
    assert m["facts"] == 3 and m["empty_papers"] == 1 and m["nothing_papers"] == 1
    assert m["copyback_pct"] == 33.3 and m["unresolved_pct"] == 33.3
    assert m["definition_pct"] == 33.3 and m["number_not_on_page_pct"] == 33.3
    assert m["topic_linked_pct"] == 33.3 and m["secs_per_paper"] == 15.0


@pytest.fixture
def bench_root(tmp_path, monkeypatch):
    monkeypatch.setattr(bench, "BENCH_ROOT", tmp_path)
    monkeypatch.setattr(bench, "HAARPI_DIR", tmp_path)
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "results").mkdir()
    bench._dump(tmp_path / "fixtures" / "x1.json", {"questions": [], "topics": [], "papers": [
        {"citekey": "aleti2017", "label": "Aleti 2017", "title": "Fitness landscapes", "abstract": ""}]})
    for lab, txt in (("x1_qwen3-6_p0_nothink", "P0 SAYS"), ("x1_gemma4_p1_nothink", "P1 SAYS")):
        bench._dump(tmp_path / "results" / f"{lab}.json", {"label": lab, "model": lab, "X1": {
            "sets": {"aleti2017": {"facts": [{"text": txt, "quote": "q", "page": 1}], "nothing": ""}}}})
    return tmp_path


def test_the_page_is_blind_and_the_key_stays_in_the_bench_folder(bench_root):
    page = bench.judge().read_text()
    assert "qwen" not in page and "gemma" not in page and "p0" not in page.lower().replace("p0 says", "")
    data = json.loads(re.search(r"const D = (.*);\n", page).group(1))
    assert [s["label"] for s in data["papers"][0]["sets"]] == ["A", "B"]
    key = json.loads((bench_root / bench.BLIND_KEY).read_text())
    assert set(key["aleti2017"].values()) == {"x1_qwen3-6_p0_nothink", "x1_gemma4_p1_nothink"}


def test_unblinding_scores_runs_and_pools_by_prompt(bench_root):
    bench.judge()
    key = json.loads((bench_root / bench.BLIND_KEY).read_text())["aleti2017"]
    letter = {v: k for k, v in key.items()}
    judged = bench_root / "EVAL_extraction_DCR.json"
    judged.write_text(json.dumps({"aleti2017": {
        letter["x1_gemma4_p1_nothink"]: {"cite": "all", "understand": "yes", "wrong": []},
        letter["x1_qwen3-6_p0_nothink"]: {"cite": "some", "understand": "no", "wrong": [0]}}}))
    out = bench.unblind(judged)
    assert out["runs"]["x1_gemma4_p1_nothink"]["citable_clear_faithful_pct"] == 100.0
    assert out["runs"]["x1_qwen3-6_p0_nothink"]["cite_pct"] == 50.0
    assert out["runs"]["x1_qwen3-6_p0_nothink"]["wrong_facts"] == 1
    assert out["by_prompt"]["p1"]["citable_clear_faithful_pct"] == 100.0
    assert out["by_prompt"]["p0"]["citable_clear_faithful_pct"] == 0.0


def test_an_x1_run_gets_its_own_results_file_per_prompt_and_thinking(bench_root, monkeypatch):
    monkeypatch.setattr(bench, "task_x1", lambda m, w, p, t: {"prompt": p, "think": t})
    fp = bench.run("qwen3.6:27b-16k", tasks=["X1"], prompt="p1", think=True)
    assert fp.name == f"x1_{bench._slug('qwen3.6:27b-16k')}_p1_think.json"
    assert json.loads(fp.read_text())["X1"] == {"prompt": "p1", "think": True}

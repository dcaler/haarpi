"""X2, paper cards: cards checked verbatim, all-green rules, canaries, role rotation, the page
and the unblinding — all on stubs, no model."""

from __future__ import annotations

import json
import math
import re

import pytest

import bench
import bench_x2 as X

PAGES = {1: "agent based simulation of the mitacs programme shows that reducing partner conflict "
            "from 2 percent to 0.5 percent increases completed projects by 18",
         2: "fitness landscapes of evosuite have plateaus rather than many local optima"}


def _reply(*claims):
    return json.dumps({"count": len(claims), "contributions": [
        {"claim": c, "domain": d, "method": "ABM", "conditions": "none stated",
         "quotes": [{"text": q, "page": p}]} for c, d, q, p in claims]})


def test_a_card_keeps_every_contribution_and_checks_each_quote_on_its_page():
    raw = "Sure!\n```json\n" + _reply(
        ("Reducing conflict increases completed projects by 18.", "Mitacs",
         "reducing partner conflict from 2 percent to 0.5 percent increases completed projects", 1),
        ("Invented claim.", "Mars", "this sentence is not in the paper at all anywhere", 1)) + "\n```"
    card = X.parse_card(raw, PAGES, "m|ck")
    assert card["count"] == 2 and [c["id"] for c in card["contributions"]] == ["m|ck|0", "m|ck|1"]
    assert card["contributions"][0]["verbatim"] and not card["contributions"][1]["verbatim"]
    assert X.parse_card("no json here", PAGES, "m|ck") == {"count": None, "contributions": []}


def test_the_judge_says_same_only_when_every_field_agrees():
    ok = {"finding": "same", "direction": "n/a", "domain": "same", "conditions": "same",
          "numbers": "none"}
    assert X.judge_rule(ok)
    for k, v in (("finding", "overlapping"), ("direction", "opposite"), ("domain", "A narrower"),
                 ("conditions", "different"), ("numbers", "disagree")):
        assert not X.judge_rule(dict(ok, **{k: v})), k
    assert not X.judge_rule({})


def test_a_verifier_pass_needs_yes_yes_matches_and_a_span_on_the_page():
    form = {"supported": "yes", "own_finding": "yes", "scope": "matches",
            "span": "fitness landscapes of evosuite have plateaus rather than many local optima"}
    assert X.verify_rule(form, PAGES)
    assert not X.verify_rule(dict(form, span="a span the model made up for itself here"), PAGES)
    assert not X.verify_rule(dict(form, scope="overreaches"), PAGES)
    assert not X.verify_rule(dict(form, supported="cant_tell"), PAGES)


def _c(cid, claim="Reducing conflict increases completed projects by 18.", domain="Mitacs"):
    return {"id": cid, "claim": claim, "domain": domain, "method": "ABM", "conditions": "none",
            "quotes": [{"text": "q", "page": 1, "found": 1}], "verbatim": True}


def test_canaries_change_a_number_a_direction_a_domain_or_the_whole_claim():
    c, other = _c("a"), _c("b", "Plateaus dominate.", "EvoSuite")
    assert "18" not in X.mutate(c, "number", [other])["claim"]
    assert "decreases" in X.mutate(c, "direction", [other])["claim"]
    assert X.mutate(c, "domain", [other])["domain"] == "EvoSuite"
    assert X.mutate(c, "foreign", [other])["claim"] == "Plateaus dominate."
    assert X.mutate(_c("x", "No numbers or directions."), "number", [other]) is None


def _cards(models, papers=("p1", "p2"), n=2):
    return {m: {"cards": {ck: {"count": n, "contributions": [
        dict(_c(f"{bench._slug(m)}|{ck}|{i}", f"claim {ck} {i}", f"domain {ck}"),
             verbatim=True) for i in range(n)]} for ck in papers},
        "per_paper": n, "verbatim_pct": 100.0, "secs_per_paper": 60.0} for m in models}


def _cos(a, b):          # same paper and index -> identical; anything else far apart
    return 1.0 if a["id"].split("|", 1)[1] == b["id"].split("|", 1)[1] else 0.1


MODELS = ["qwen3.6:27b-16k", "gemma4:31b-it-q4_K_M", "granite4.2:30b", "gpt-oss:20b"]


def test_pairs_are_judged_and_contributions_verified_only_by_other_families():
    asg = X.build_assignment(_cards(MODELS), _cos)
    fam = asg["families"]
    assert asg["pairs"] and all(len(p["judges"]) == 2 for p in asg["pairs"].values())
    model_of = lambda cid: next(m for m in MODELS if cid.startswith(bench._slug(m)))  # noqa: E731
    for p in asg["pairs"].values():
        involved = {fam[model_of(p["a"])], fam[model_of(p["b"])]}
        assert not involved & {fam[j] for j in p["judges"]}
    for cid, vs in asg["verify"].items():
        assert len(vs) == 2 and fam[model_of(cid)] not in {fam[v] for v in vs}
    for m in MODELS:
        for role in ("X2J", "X2V"):
            items = asg["queues"][m][role]
            assert sum(1 for it in items if (it.get("pid") or it.get("cid")).startswith("canary|")) >= 2
    loads = [len(asg["queues"][m]["X2J"]) for m in MODELS]
    assert max(loads) - min(loads) <= 3                 # rotation is balanced


def test_a_lineup_keeps_only_verified_contributions_its_members_agree_on():
    cards = _cards(MODELS[:3])
    asg = X.build_assignment(cards, _cos)
    contribs = {c["id"]: dict(c, model=m, paper=ck) for m in cards
                for ck, card in cards[m]["cards"].items() for c in card["contributions"]}
    js = {pid: [True, True] for pid in asg["pairs"]}
    vs = {cid: [True, True] for cid in asg["verify"]}
    same = X.green_pairs(asg, js)
    lineup = tuple(MODELS[:3])
    assert len(X.lineup_green(lineup, contribs, same, vs, asg, 2)) == 12
    one_bad = next(iter(vs))
    vs[one_bad] = [True, False]                       # one verifier said no: not green
    g = X.lineup_green(lineup, contribs, same, vs, asg, 2)
    assert one_bad not in g and len(g) < 12
    assert len(set(X.clusters(sorted(contribs), same).values())) == 4


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(bench, "BENCH_ROOT", tmp_path)
    monkeypatch.setattr(bench, "HAARPI_DIR", tmp_path)
    (tmp_path / "fixtures").mkdir()
    (tmp_path / "results").mkdir()
    bench._dump(tmp_path / "fixtures" / X.FIXTURE, {"papers": [
        {"citekey": ck, "label": ck.upper(), "title": f"Title {ck}", "abstract": "abs",
         "authors": ["Kennedy"], "pdf": "zotero://open-pdf/library/items/ATT1",
         "zotero": "zotero://select/library/items/ITEM1", "full": "[p.1]\nq"} for ck in ("p1", "p2")]})
    cards = _cards(MODELS[:3])
    for m, r in cards.items():
        bench._dump(tmp_path / "results" / f"x2e_{bench._slug(m)}.json",
                    {"model": m, "X2E": r})
    asg = X.build_assignment(cards, _cos)
    bench._dump(tmp_path / "fixtures" / X.ASSIGN, asg)
    for m in MODELS[:3]:
        jv = {it["pid"]: {"same": not it["pid"].startswith("canary|")}
              for it in asg["queues"][m]["X2J"]}
        vv = {it["cid"]: {"pass": not it["cid"].startswith("canary|")}
              for it in asg["queues"][m]["X2V"]}
        bench._dump(tmp_path / "results" / f"x2r_{bench._slug(m)}.json",
                    {"model": m, "X2J": {"verdicts": jv, "canaries": 2, "canaries_caught": 2},
                     "X2V": {"verdicts": vv, "canaries": 2, "canaries_caught": 2}})
    return tmp_path


def test_the_page_is_blind_paper_by_paper_with_zotero_links_and_the_key_stays_out(root):
    page = X.judge_x2().read_text(encoding="utf-8")
    data = json.loads(re.search(r"const D = (.*);\n", page).group(1).replace("<\\/", "</"))
    p = data["papers"][0]
    assert p["zotero"].startswith("zotero://select") and p["pdf"].startswith("zotero://open-pdf")
    assert len(p["statements"]) == 6 and p["pairs"]
    for m in MODELS:
        assert bench._slug(m) not in page and X.family(m) not in json.dumps(data)
    key = json.loads((root / X.KEY).read_text())
    assert set(key["statements"]["p1"]) == {s["sid"] for s in p["statements"]}


def test_unblinding_scores_models_judges_verifiers_and_lineups(root):
    X.judge_x2()
    key = json.loads((root / X.KEY).read_text())
    marks = {"statements": {ck: {sid: ("1" if cid.endswith("|0") else "4")
                                 for sid, cid in sts.items()}
                            for ck, sts in key["statements"].items()},
             "pairs": {ck: {b: "same" for b in prs} for ck, prs in key["pairs"].items()},
             "missing": {"p1": "a point nobody found"}}
    fp = root / "EVAL_cards_DCR.json"
    fp.write_text(json.dumps(marks))
    out = X.unblind_x2(fp)
    m0 = out["models"][MODELS[0]]
    assert m0["labelled"] == 4 and m0["faithful_pct"] == 50.0 and m0["keep_pct"] == 50.0
    v = out["verifiers"][MODELS[0]]
    assert v["false_accepts"] > 0 and v["canaries_caught"] == 2
    trio = next(k for k in out["lineups"] if k.count("+") == 2 and "unanimous" in k)
    assert out["lineups"][trio]["keep_recall_pct"] == 100.0
    assert out["person"]["missing"] == {"p1": "a point nobody found"}


def test_the_cli_sends_x2_runs_to_the_x2_harness(monkeypatch):
    got = {}
    monkeypatch.setattr(X, "run_x2", lambda m, t: got.update(m=m, t=t) or "ok")
    bench.main(["run", "--coordinator", "gpt-oss:20b", "--tasks", "X2J", "X2V"])
    assert got == {"m": "gpt-oss:20b", "t": ["X2J", "X2V"]}


# ── what a card reads ───────────────────────────────────────────────────────────

def _paper(intro=True, concl=True, pages=12, per=4000):
    out = []
    for n in range(1, pages + 1):
        head = ("1. Introduction\n" if intro and n == 2 else
                "5. Conclusions and policy implications\n" if concl and n == pages - 2 else
                "References\n" if n == pages else "")
        out.append(f"[p.{n}]\n{head}" + f"page {n} words " * (per // 14) + "\n")
    out.insert(1, "data from 2015 to 2024.\nModel 2\nmethods of knowledge transfer\n")
    return "".join(out)


def test_headings_are_short_capitalised_lines_not_fragments_or_table_labels():
    kinds = [(k, h) for k, _, h in X.find_sections(_paper())]
    assert kinds == [("introduction", "1. Introduction"),
                     ("conclusion", "5. Conclusions and policy implications"),
                     ("references", "References")]


def test_a_short_paper_is_read_whole_and_never_its_references():
    r = X.card_read(_paper(pages=5, per=1000))
    assert r["mode"] == "whole" and "References" not in r["text"]


def test_a_long_paper_reads_its_introduction_and_conclusion_whole_within_budget():
    r = X.card_read(_paper(pages=20))
    assert r["mode"] == "focused" and r["text"].startswith("[p.2]")
    assert "1. Introduction" in r["text"] and "5. Conclusions" in r["text"]
    assert r["read"] <= X.CARD_BUDGET and "References" not in r["text"]


def test_a_paper_without_findable_sections_is_read_in_windows_then_merged():
    r = X.card_read(_paper(intro=False, pages=20))
    assert r["mode"] == "merge" and len(r["windows"]) >= 3
    assert all(w.startswith("[p.") for w in r["windows"])
    assert sum(len(w) for w in r["windows"]) >= r["read"] - 50 * len(r["windows"])

    calls = []

    class Brain:
        def coordinator(self, prompt, system, **k):
            calls.append(system)
            return _reply(("A claim.", "D", "page 3 words page 3 words page 3 words", 3))
    p = {"label": "X", "title": "T", "abstract": "", "read": r}
    X.build_card_raw(Brain(), "granite4.2:30b", p)
    assert len(calls) == len(r["windows"]) + 1
    assert "part 1 of" in calls[0] and calls[-1] == X.MERGE_SYS

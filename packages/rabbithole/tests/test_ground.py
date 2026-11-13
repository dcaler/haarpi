"""Claim grounding, end to end on a two-paper project with a fake model (2026-11-13).

The fake answers the way a careful verifier would: a claim is supported only where its words
are on the pages shown. So real claims pass every check, the planted canaries (a flipped
direction, a changed number, another paper's claim or setting) fail, and the steps, the card
store and the close are tested without a GPU. The port itself was checked against bench FP's
saved answers: close reproduces 395 of 896 confirmed on 153 of 191 papers, stage by stage.
"""
from __future__ import annotations

import json
import re

import pytest

from rabbithole import canaries as K
from rabbithole import cards as C
from rabbithole import ground as G
from rabbithole import ground_prompts as P

PAPERS = {
    "alpha2020": ("Alpha, 2020", "Grants and survival", ["Ann Alpha"],
                  "[p.1]\nIntroduction\nWe study young firms. Public grants increase the survival of young firms "
                  "in the clean energy sector by 12 percent over five years. Smith (2006) found that venture "
                  "capital lowers the failure rate of software start-ups in large cities.\n\n[p.2]\nResults\n"
                  "Firms with partners grow faster than firms without partners in the solar industry. "
                  "Larger firms receive more public money than small firms in every region we observe. "
                  + "Further detail about the sample is reported in the appendix tables here. " * 40),
    "beta2021": ("Beta, 2021", "Partners and learning", ["Bo Beta"],
                 "[p.1]\nIntroduction\nWe study learning. Capability building raises the patent output of "
                 "manufacturing firms in northern Italy by 30 percent. Jones (2010) found that public "
                 "procurement increases the demand for new products in the defence sector.\n\n[p.2]\n"
                 "Results\nPartnerships with universities improve product quality in the biotech sector. "
                 "Higher export shares are associated with faster learning in small open economies. "
                 + "Robustness checks are described at length in the online supplement. " * 40),
}
CLAIMS = {
    "alpha2020": ["Public grants increase the survival of young firms in the clean energy sector by 12 percent over five years.",
                  "Firms with partners grow faster than firms without partners in the solar industry.",
                  "Larger firms receive more public money than small firms in every region we observe."],
    "beta2021": ["Capability building raises the patent output of manufacturing firms in northern Italy by 30 percent.",
                 "Partnerships with universities improve product quality in the biotech sector.",
                 "Higher export shares are associated with faster learning in small open economies."],
}


def _norm(t: str) -> str:
    return " ".join(re.sub(r"[^\w%]+", " ", t.lower()).split())


def _section(prompt: str, head: str, nxt: str | None) -> str:
    a = prompt.index(head) + len(head)
    return prompt[a:prompt.index(nxt, a)] if nxt else prompt[a:]


class FakeBrain:
    """Supports a claim only where its words are on the pages or passages it is shown."""

    def coordinator(self, prompt: str, system: str = "", think: bool = True, num_ctx: int = 16384) -> str:
        if system == P.KIND_SYS:
            return json.dumps({"kind": "empirical", "reason": "own data"})
        if system.startswith(P.CARD_SYS[:40]):
            ck = next(k for k, v in PAPERS.items() if f"PAPER: {v[0]}" in prompt)
            return json.dumps({"count": 3, "contributions": [
                {"claim": c, "quotes": [{"text": c.rstrip("."), "page": 1 + (i > 0)}]}
                for i, c in enumerate(CLAIMS[ck])]})
        if system == P.REREAD_SYS:
            claim = re.search(r"claim: (.*)", prompt).group(1)
            ok = _norm(claim) in _norm(_section(prompt, "PAGES", None))
            return json.dumps({"supported": "yes" if ok else "no", "scope": "matches" if ok else "overreaches",
                               "span": claim.rstrip(".") if ok else "", "reason": "fake"})
        if system == P.CONFIRM_SYS:
            claim = _section(prompt, "CLAIM\n", "\n\nPASSAGES")
            ok = _norm(claim) in _norm(_section(prompt, "PASSAGES", None))
            return json.dumps({"says": "yes" if ok else "no", "direction": "same" if ok else "opposite",
                               "numbers": "agree", "reason": "fake"})
        if system in (P.OWN_SYS_REVIEW, P.OWN_SYS_NOT_REVIEW):
            claim = _section(prompt, "CLAIM\n", "\n\nPASSAGES")
            cited = f"found that {_norm(claim)}" in _norm(_section(prompt, "PASSAGES", None))
            return json.dumps({"own_finding": "no" if cited else "yes", "role": "" if cited else "finding",
                               "source": "Smith 2006" if cited else "", "reason": "fake"})
        raise AssertionError(f"unexpected prompt: {system[:60]}")


@pytest.fixture
def run(tmp_path, monkeypatch):
    cfg = C.CardsConfig(dir=tmp_path / "cards")
    proj = tmp_path / "Proj"
    (proj / "litReview" / "work").mkdir(parents=True)
    (proj / "litReview" / "litrev.yaml").write_text("project_name: Proj\ntopic: firms\n")
    monkeypatch.setattr(G, "brain_for", lambda cfg_, model: FakeBrain())
    r = G.Run(proj, cfg)
    for ck, (label, title, authors, text) in PAPERS.items():
        cd = r.card(ck)
        cd.write_text(text)
        cd.write_meta({"citekey": ck, "label": label, "title": title, "authors": authors, "abstract": "",
                       "pdf": f"zotero://open-pdf/library/items/{ck.upper()}", "text_version": 3})
    r.dump("papers", {"citekeys": sorted(PAPERS), "skipped": []})
    return r


def _all_steps(r: G.Run) -> dict:
    d = r.project
    for m in r.cfg.kind_models:
        G.kind(d, m, cfg=r.cfg)
    G.extract(d, cfg=r.cfg)
    G.plant(d, cfg=r.cfg)
    for m in r.cfg.verifiers:
        G.reread(d, m, cfg=r.cfg)
    G.locate(d, cfg=r.cfg)
    for m in r.cfg.verifiers:
        G.confirm(d, m, cfg=r.cfg)
        G.own(d, m, cfg=r.cfg)
    return G.close(d, cfg=r.cfg)


def test_every_real_claim_passes_and_no_canary_slips(run):
    s = _all_steps(run)
    assert s["claims"] == 6 and s["confirmed"] == 6 and s["papers_with_a_confirmed_claim"] == 2
    assert s["holds"] == [] and s["roles"] == {"finding": 6} and "flagged_for_the_gate" not in s
    planted = {chk: sum(v["planted"] for v in per.values()) for chk, per in s["canaries"].items()}
    assert planted["step 2"] >= 4 and planted["step 4"] >= 4 and planted["step 2b"] == 2
    assert all(v["slipped"] == 0 for per in s["canaries"].values() for v in per.values())


def test_a_card_holds_its_claims_and_verdicts_in_the_paper_words(run):
    _all_steps(run)
    cd = run.card("alpha2020")
    v = cd.step("verdict")["claims"]["alpha2020#1"]
    assert v["confirmed"] and v["role"] == "finding"
    assert v["passages"][0]["text"].startswith("Public grants increase the survival")
    assert set(p.name for p in cd.dir.iterdir()) >= {
        "card.json", "text.txt", "located.json", "verdict.json",
        "reread_gemma4_31b-it-q4_K_M.json", "reread_gpt-oss_20b.json",
        "confirm_gemma4_31b-it-q4_K_M.json", "own_gpt-oss_20b.json"}


def test_a_second_run_reuses_the_cards_and_asks_nothing(run, monkeypatch):
    _all_steps(run)

    class NoCalls:
        def coordinator(self, *a, **k):
            raise AssertionError("a carded paper was asked again")
    monkeypatch.setattr(G, "brain_for", lambda cfg_, model: NoCalls())
    d = run.project
    for m in run.cfg.kind_models:
        G.kind(d, m, cfg=run.cfg)
    G.extract(d, cfg=run.cfg)
    for m in run.cfg.verifiers:
        G.reread(d, m, cfg=run.cfg)
        G.confirm(d, m, cfg=run.cfg)
        G.own(d, m, cfg=run.cfg)


def test_a_verifier_that_passes_everything_lets_canaries_slip_and_holds_the_release(run, monkeypatch):
    class Gullible(FakeBrain):
        def coordinator(self, prompt, system="", think=True, num_ctx=16384):
            if system == P.REREAD_SYS:
                claim = re.search(r"claim: (.*)", prompt).group(1)
                return json.dumps({"supported": "yes", "scope": "matches", "span": claim})
            return super().coordinator(prompt, system, think, num_ctx)
    monkeypatch.setattr(G, "brain_for", lambda cfg_, model: Gullible())
    s = _all_steps(run)
    per = s["canaries"]["step 2"]
    assert sum(v["slipped"] for v in per.values()) == sum(v["planted"] for v in per.values())
    assert s["holds"] and all(h.startswith("step 2: ") for h in s["holds"])


def test_canaries_are_made_by_code_and_the_same_each_time():
    claims = [{"id": f"{ck}#{i + 1}", "paper": ck, "claim": c} for ck, cs in CLAIMS.items()
              for i, c in enumerate(cs)]
    a = K.plant(K.STEP2_KINDS, claims, 8, "S2")
    assert a == K.plant(K.STEP2_KINDS, claims, 8, "S2")
    kinds = {c["canary"] for c in a}
    assert {"direction", "number", "setting", "foreign"} <= kinds
    num = K.mutate(claims[0], "number", claims[3:])
    assert "25 percent" in num["claim"]                       # 12 → 2·12+1
    d = K.mutate(claims[0], "direction", [])
    assert d["claim"].startswith("Public grants decrease")
    st = K.mutate(claims[0], "setting", claims[3:])
    assert st["claim"].endswith(f", in {st['setting']}.") and st["setting"] != "the clean energy sector"


def test_a_kind_holds_only_when_even_its_low_bound_is_above_one_in_ten():
    lo, hi = K.wilson(2, 18)                                  # FirmPathways' step-2 direction slips
    assert lo < K.HOLD_RATE < hi
    s = K.slips({f"c{i}": "direction" for i in range(18)}, {"c0", "c1"})
    assert s["direction"] == {"planted": 18, "slipped": 2, "low": round(lo, 3), "high": round(hi, 3),
                              "holds": False}
    assert K.slips({f"c{i}": "number" for i in range(10)}, {f"c{i}" for i in range(6)})["number"]["holds"]


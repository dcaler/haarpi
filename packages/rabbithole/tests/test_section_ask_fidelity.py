"""A reviewer's section ask must survive the plan, the retrieval, and the decline.

Built from elephantRoom task 1052 (2026-09-18), where all three lost it.

The reviewer asked for "individual level or Household level impacts and the distributional
equity of impacts... ABM have unique ability to generate insight into micro-level responses".
`haarpi next` planned TWO sections for that comment — "Household heterogeneity in ABM" (the
ask) and "Distributional equity of CBAM" (trade between countries) — and what came back was
672 words in which the words household, individual, consumer and worker appear zero times.

Three defects, each sufficient on its own:

  1. `_planned_sections` was `{s["ask"]: s}`. Two sections on one ask collide and the second
     overwrites the first, so the right section was destroyed before anything read it. Five
     planned sections became three, and nothing said so.
  2. The shortlist embedded `heading. claim` — the planner's compression — never the
     reviewer's own words.
  3. The decline judged "the corpus cannot carry it" from an 18-source ranking. The corpus
     held Firooz 2025, *Reshoring, automation, and labor markets under trade uncertainty*,
     indexed by the same run.
"""
import json

import pytest

from rabbithole import graft
from rabbithole.summarize import Section

HOUSEHOLD_ASK = (
    "I'd like to add an entire section on individual level or Household level impacts and the "
    "distributional equity of impacts. ABM have unique ability to generate insight into "
    "micro-level responses, and we should use it.")
RESHORE_ASK = (
    "I'd also like to add a section on nation-level drives to reduce supply chain dependencies "
    "and increase domestic production as a means of reducing exposure to unilateral external "
    "decisions - and these nation-level goals have unemployment impacts as well.")


# ── 1. two sections on one ask ──────────────────────────────────────────────
def test_two_sections_on_one_ask_both_survive(monkeypatch, tmp_path):
    """The dict comprehension that destroyed one of them."""
    from rabbithole import revise
    plan = {"stage": "litreview", "type": "plan", "sections": [
        {"ask": HOUSEHOLD_ASK, "heading": "Household heterogeneity in ABM", "claim": "c1"},
        {"ask": HOUSEHOLD_ASK, "heading": "Distributional equity of CBAM", "claim": "c2"},
        {"ask": RESHORE_ASK, "heading": "Reshoring mitigates geopolitical climate risks",
         "claim": "c3"},
    ]}

    class _P:
        root = tmp_path / "litReview"

    import sys, types
    fake = types.ModuleType("haarpi.project")
    fake.list_plans = lambda root: [plan]
    monkeypatch.setitem(sys.modules, "haarpi.project", fake)
    monkeypatch.setitem(sys.modules, "haarpi", types.ModuleType("haarpi"))
    sys.modules["haarpi"].project = fake

    got = revise._planned_sections(_P())
    assert [s["heading"] for s in got[HOUSEHOLD_ASK]] == [
        "Household heterogeneity in ABM", "Distributional equity of CBAM"], \
        "the ask the reviewer actually wrote must not be overwritten by the one planned after it"
    assert len(got[RESHORE_ASK]) == 1


# ── 2. retrieval must use the reviewer's words ─────────────────────────────
def test_the_shortlist_embeds_the_ask_not_only_the_heading(monkeypatch):
    from rabbithole import summarize
    seen = {}

    class _B:
        def embed_batch(self, texts):
            seen.setdefault("calls", []).append(texts)
            return [[1.0, 0.0] for _ in texts]

    sec = Section(heading="Distributional equity of CBAM", claim="Border adjustments shift costs.",
                  ask=HOUSEHOLD_ASK)
    summarize._shortlist(_B(), [sec], {"k": "a source line"}, {"k": "a source line"})
    section_text = seen["calls"][1][0]
    assert "Household level impacts" in section_text, \
        "the heading is the planner's compression; the ask is what the reviewer wants"


# ── 3. a decline is a claim about the CORPUS ───────────────────────────────
def _corpus_line(author, year, title, abstract=""):
    return f"{author} {year}. {title}. {abstract}"


def test_the_second_look_finds_what_the_shortlist_missed():
    """Firooz 2025 is the paper the real decline said did not exist."""
    sec = Section(heading="Domestic production reshapes labor markets",
                  claim="The drive for economic sovereignty through reshoring generates "
                        "complex unemployment dynamics.",
                  ask=RESHORE_ASK,
                  candidates=["off1", "off2"])
    full = {
        "off1": _corpus_line("Nordhaus", 1996, "Regional dynamic general equilibrium climate"),
        "off2": _corpus_line("Acemoglu", 2012, "The environment and directed technical change"),
        "firooz": _corpus_line("Firooz", 2025,
                               "Reshoring, automation, and labor markets under trade uncertainty",
                               "We study the implications of trade uncertainty for domestic "
                               "production and unemployment."),
        "rengs": _corpus_line("Rengs", 2020,
                              "Evolutionary macroeconomic assessment of employment and "
                              "innovation impacts of climate policy packages"),
    }
    w = graft._lexical_witnesses(sec, full, shortlisted=set(sec.candidates))
    assert "firooz" in w, "the corpus holds the ask almost verbatim; the shortlist never showed it"
    assert "off1" not in w and "off2" not in w


def test_a_decline_stands_when_the_corpus_really_is_empty():
    """The consumption-smoothing decline in that same run was CORRECT — zero sources. The
    second look must not turn every decline into a draft."""
    sec = Section(heading="Consumption smoothing dampens innovation",
                  claim="Household savings withdrawals during unemployment suppress investment.",
                  ask="consumption smoothing, whereby households withdraw from their savings",
                  candidates=["off1"])
    full = {"off1": _corpus_line("Nordhaus", 1996, "Regional dynamic general equilibrium"),
            "off2": _corpus_line("Acemoglu", 2012, "Directed technical change")}
    assert graft._lexical_witnesses(sec, full, shortlisted={"off1"}) == []


def test_ask_terms_drop_words_every_paper_contains():
    sec = Section(heading="h", claim="", ask="I would like to add a section on the impacts of "
                                             "reshoring using an agent-based model")
    terms = graft._ask_terms(sec)
    assert "reshoring" in terms
    for junk in ("section", "impacts", "model", "using", "would", "about"):
        assert junk not in terms


# ── the decline becomes a gather, and the gather re-drafts ─────────────────
def test_a_declined_section_is_carried_forward_once(tmp_path):
    from haarpi import planner, project
    project.record_plan(tmp_path, {"type": "needs_gather", "stage": "litreview", "sections": [
        {"heading": "Consumption smoothing dampens innovation", "claim": "c",
         "ask": "the reviewer's sentence", "missing": "Evidence linking household consumption "
                                                      "smoothing to firm-level investment"}]})
    carried = planner.carried_declines(tmp_path)
    assert [c["heading"] for c in carried] == ["Consumption smoothing dampens innovation"]
    assert "household consumption" in carried[0]["missing"]

    planner.consume_declines(tmp_path)
    assert planner.carried_declines(tmp_path) == [], \
        "a re-queued section must not be re-queued forever"


def test_a_carried_decline_implies_a_gather_chain():
    """The brief the decline wrote becomes the gather query, so the chain is derived by the
    same rule as any other section ask rather than by a special case."""
    from haarpi import planner
    built = planner.chain_from_tasks([
        {"comments": ["the reviewer's sentence"], "need": "section",
         "query": "Evidence linking household consumption smoothing to firm-level investment"}])
    assert "gather" in built["steps"]
    assert any("consumption smoothing" in t for t in built["gather_topics"])

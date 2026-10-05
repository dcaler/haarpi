"""What a source is made to say, what the review repeats, and what the corpus does not cover.

DigiPros 261005 attributed the project's vocabulary to sources that never used it ("a
framework for handling incomplete simulation outputs" — a prosopography database paper),
re-explained Edmonds 2015's three-stage framework in five of six sections, anchored claims on
quotes from the wrong passage, and had no way to say that "extraction instruments against a
model's action structure" was a term no source in the corpus uses.

Runnable two ways:
    pytest tests/test_attribution_and_coverage.py
    python tests/test_attribution_and_coverage.py
"""

from __future__ import annotations

from types import SimpleNamespace

from rabbithole import chroma, guards, summarize
from rabbithole.models import Author, Candidate
from rabbithole.summarize import Section

_FOCUS = ("digital prosopography and the factoid model applied to simulation output; "
          "designing extraction instruments against a model's action structure")


# ── focus terms ───────────────────────────────────────────────────────────────

def test_focus_terms_are_the_brief_bigrams_without_verbs_or_possessive_joins():
    terms = guards.focus_terms(_FOCUS)
    assert {"digital prosopography", "factoid model", "simulation output",
            "extraction instrument", "action structure"} <= set(terms)
    assert "model applied" not in terms and "model action" not in terms
    assert "designing extraction" not in terms


# ── unsupported bridges ───────────────────────────────────────────────────────

_SELF = {"akoka2022": "Conceptual models for prosopographical databases integrate precision "
                      "and uncertainty dimensions.",
         "elsen2016": "The simulation output was traced to annotated evidence."}


def test_a_brief_term_no_cited_source_uses_is_flagged():
    text = ("Conceptual models provide a framework for handling incomplete simulation outputs "
            "[@akoka2022].")
    f = guards.unsupported_bridges(text, guards.focus_terms(_FOCUS), _SELF, section=2)
    assert len(f) == 1 and f[0].kind == "unsupported-bridge" and f[0].section == 2
    assert "'simulation output'" in f[0].imperative and "[@akoka2022]" in f[0].imperative


def test_one_cited_source_that_uses_the_term_carries_it():
    text = ("Simulation outputs can be traced to evidence and modelled with uncertainty "
            "[@akoka2022; @elsen2016].")
    assert guards.unsupported_bridges(text, guards.focus_terms(_FOCUS), _SELF) == []


def test_a_sentence_citing_a_source_without_notes_is_not_judged():
    text = "Simulation outputs need extraction instruments [@unknown2020]."
    assert guards.unsupported_bridges(text, guards.focus_terms(_FOCUS), _SELF) == []


def test_self_text_leaves_out_the_fields_written_against_the_brief():
    note = {"argument": "A.", "findings": "F.", "relevance": "Key for simulation output.",
            "gaps": "Does not address trajectory archetypes.", "themes": ["prosopography"]}
    t = summarize._self_text(note)
    assert "simulation output" not in t and "archetype" not in t and "prosopography" in t


# ── repeated facts ────────────────────────────────────────────────────────────

_EARLIER = [(0, "Stakeholder narratives coded into agent rules reproduced the vote, with 13 "
                "supporting and 3 dissenting [@schenk2014]. A three-stage analytical framework "
                "decomposes narrative data into context, scope, and narrative elements "
                "[@edmonds2015].")]


def test_the_same_number_from_the_same_source_is_a_repeated_fact():
    text = "Coding narratives reproduced voting with 13 supporting votes [@schenk2014]."
    f = guards.repeated_facts(text, _EARLIER, section=3)
    assert len(f) == 1 and f[0].kind == "repeated-fact" and "§1" in f[0].imperative


def test_a_restated_framework_is_a_repeated_fact():
    text = ("A three-stage structure covering context, scope, and narrative elements "
            "decomposes the narrative data [@edmonds2015].")
    assert len(guards.repeated_facts(text, _EARLIER)) == 1


def test_citing_the_same_source_for_something_new_is_never_flagged():
    text = ("Edmonds' framework also sets the unit of comparison when archetypes are matched "
            "across runs [@edmonds2015].")
    assert guards.repeated_facts(text, _EARLIER) == []


def test_a_number_from_a_different_source_is_not_a_repeat():
    assert guards.repeated_facts("Only 13 models reported seeds [@groff2019].", _EARLIER) == []


def test_years_and_bare_digits_are_not_facts():
    earlier = [(0, "Abbott found 3 patterns in 1990 [@abbott1990].")]
    assert guards.repeated_facts("By 1990, 3 groups had emerged [@abbott1990].", earlier) == []


# ── already-established block in the drafting prompt ─────────────────────────

def test_the_drafter_is_told_what_earlier_sections_said_about_its_candidates():
    s0 = Section("Why narratives", "c", candidates=["edmonds2015"])
    s0.text = _EARLIER[0][1]
    s1 = Section("Validation", "c", candidates=["edmonds2015", "groff2019"])
    block = summarize._established([s0, s1], 1)
    assert "three-stage analytical framework" in block and "§1" in block
    assert "schenk2014" not in block                      # not a candidate here
    assert "Cite these sources again" in block and "do NOT re-explain" in block
    assert summarize._established([s0, s1], 0) == ""


def test_the_draft_prompt_carries_the_established_block():
    assert "{established}" in summarize._DRAFT_PROMPT


def test_section_guards_run_the_attribution_guards_when_given():
    sec = Section("I", "c")
    text = "Coding narratives reproduced voting with 13 supporting votes [@schenk2014]."
    sections = [Section("0", "c"), sec]
    sections[0].text = _EARLIER[0][1]
    extra = summarize._attribution_guards(sections, 1, [], {})
    kinds = {f.kind for f in summarize._section_guards(sec, text, {"schenk2014"}, extra)}
    assert "repeated-fact" in kinds


# ── quote support ─────────────────────────────────────────────────────────────

class _Coll:
    def __init__(self, chunks):
        self.chunks = chunks

    def get(self, where=None, include=None, limit=None):
        return {"ids": [str(i) for i in range(len(self.chunks))]}

    def query(self, query_embeddings, where, n_results, include):
        return {"documents": [self.chunks[:n_results]],
                "metadatas": [[{"chunk_idx": i, "page": i + 1}
                               for i in range(min(n_results, len(self.chunks)))]]}


class _TopicBrain:
    """Embeds by topic word, so a claim and a quote on different topics are orthogonal."""

    def embed(self, text):
        t = text.lower()
        return [1.0 if "focus group" in t else 0.0, 1.0 if "fusion" in t else 0.0, 0.1]


def test_each_located_claim_carries_a_support_score():
    coll = _Coll(["The JDL Data Fusion model provides a process flow for sensor data fusion."])
    items = chroma.locate_direct(coll, _TopicBrain(), "akoka2020",
                                 "Focus group testing confirms the approach.")
    assert items[0]["support"] < summarize._WEAK_SUPPORT


def test_a_weakly_supported_bullet_is_labelled_and_a_strong_one_is_not():
    corpus = [Candidate(title="Contribution", year=2020, authors=[Author(family="Akoka")])]
    located = {0: [{"claim": "Focus group testing confirms it.", "location": "p.3",
                    "quote": "The JDL fusion model.", "support": 0.31},
                   {"claim": "It fuses data and process models.", "location": "p.5",
                    "quote": "We fuse data and process models.", "support": 0.82},
                   {"claim": "Unscored legacy claim.", "location": "p.6", "quote": "q"}]}
    md = summarize.bibliography(corpus, located)
    lines = [ln for ln in md.splitlines() if ln.startswith("- ")]
    assert "weak support" in lines[0]
    assert "weak support" not in lines[1] and "weak support" not in lines[2]


# ── coverage against the brief ────────────────────────────────────────────────

class _CoverBrain:
    """Close to item 1 if the text mentions prosopography; nothing is close to item 2."""

    def embed(self, text):
        t = text.lower()
        if "designing extraction" in t:
            return [0.0, 1.0]
        return [1.0, 0.0] if "prosopograph" in t else [0.0, 0.0]


def _cover_fixture():
    corpus = [Candidate(title=f"P{i}", year=2000 + i, authors=[Author(family=f"A{i}")])
              for i in range(4)]
    notes = [{"argument": "Digital prosopography with the factoid model."},
             {"argument": "Prosopography databases and the factoid model."},
             {"argument": "Prosopographical networks traced through simulation outputs."},
             {"argument": "An unrelated survey."}]
    citekeys = {i: f"k{i}" for i in range(4)}
    return corpus, notes, citekeys


def test_coverage_marks_an_item_thin_when_no_source_uses_its_terms():
    corpus, notes, citekeys = _cover_fixture()
    lines = summarize.coverage_lines(_CoverBrain(), _FOCUS, "", corpus, notes, citekeys)
    assert len(lines) == 2
    assert lines[0].startswith("- **Covered**") and "3 source(s) close" in lines[0]
    assert "“factoid model” 2" in lines[0]
    assert lines[1].startswith("- **THIN**") and "“extraction instrument” 0" in lines[1]


def test_coverage_counts_review_sentences_that_bridge_an_items_terms():
    corpus, notes, citekeys = _cover_fixture()
    narrative = "An unrelated survey shows the action structure guides extraction [@k3]."
    lines = summarize.coverage_lines(_CoverBrain(), _FOCUS, narrative, corpus, notes, citekeys)
    assert "1 review sentence(s) attribute" in lines[1]


def test_coverage_survives_an_embedding_outage():
    class _Down:
        def embed(self, text):
            raise RuntimeError("ollama down")
    corpus, notes, citekeys = _cover_fixture()
    lines = summarize.coverage_lines(_Down(), _FOCUS, "", corpus, notes, citekeys)
    assert len(lines) == 2 and "close to it" not in lines[0]
    assert lines[1].startswith("- **THIN**")       # a term no source uses is still thin


def test_coverage_rides_inside_the_load_bearing_block():
    corpus, notes, citekeys = _cover_fixture()

    class _B(_CoverBrain):
        def coordinator(self, *a, **k):
            return "{}"
    narrative = ("## S\n\nDigital prosopography uses the factoid model [@k0; @k1]. "
                 "More on networks [@k2].")
    block = summarize.top_sources_block(_B(), SimpleNamespace(topic="t", focus=_FOCUS),
                                        narrative, corpus, citekeys, notes=notes)
    assert block.startswith("## Most load-bearing sources")
    assert block.count("\n## ") == 0                    # no second heading to strand a copy
    assert "**Coverage against the brief**" in block
    assert summarize.top_sources_block(_B(), SimpleNamespace(topic="t", focus=_FOCUS),
                                       narrative, corpus, citekeys).find("Coverage") == -1


if __name__ == "__main__":
    import sys
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                failed += 1
                print(f"FAIL {name}: {e}")
    sys.exit(1 if failed else 0)

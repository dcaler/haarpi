"""Sectioned synthesis: no call sees more evidence than its context can hold.

A whole-corpus synthesis handed a 31k-token digest to a 16k-token window, and Ollama silently
discarded the head of it — so the review cited only sources from the tail of the digest. The
review is now built one section at a time. These tests exercise the control flow with a fake
brain: planning, embedding shortlist, section-scoped guards and repair, orphan placement, and
the rejection ledger.

Runnable two ways:
    pytest tests/test_sectioned_synthesis.py
    python tests/test_sectioned_synthesis.py
"""

from __future__ import annotations

import json

from rabbithole import guards, summarize
from rabbithole.summarize import Section

CORPUS = {"a1", "b2", "c3", "d4", "e5"}
FULL = {k: f"- [@{k}] full digest line for {k} with Findings: 42% effect." for k in CORPUS}
COMPACT = {k: f"- [@{k}] (Author 2020) argues {k}" for k in sorted(CORPUS)}


class _Cfg:
    topic = "tonal stability"
    focus = "chord distance metrics"


class _BrainCfg:
    critique_rounds = 1


class _FakeBrain:
    """Routes by system prompt. Embeddings are one-hot so cosine is exactly controllable."""

    def __init__(self, *, plan=None, draft=None, lint="OK", substance="OK",
                 revise=None, reject="{}", vectors=None, weave=None):
        self.cfg = _BrainCfg()
        self._plan = plan
        self._draft = list(draft or [])
        self._lint = lint
        self._substance = substance
        self._revise = list(revise or [])
        self._reject = reject
        self._weave = list(weave or [])
        self._vectors = vectors or {}
        self.calls: list[str] = []
        self.revise_prompts: list[str] = []
        self.weave_prompts: list[str] = []
        self.reject_prompts: list[str] = []

    def coordinator(self, prompt, sys_prompt, **kw):
        s = sys_prompt.lower()
        if "plan the thematic sections" in s:
            self.calls.append("plan")
            return self._plan
        if "json object mapping each citekey" in s:
            self.calls.append("reject")
            self.reject_prompts.append(prompt)
            return self._reject
        if "you add evidence to one section" in s:
            self.calls.append("weave")
            self.weave_prompts.append(prompt)
            return self._weave.pop(0) if self._weave else "PARAGRAPH:\nNONE\n\nDECLINED:\n{}"
        if "copy-editor" in s:
            self.calls.append("lint")
            return self._lint
        if "peer reviewer" in s:
            self.calls.append("substance")
            return self._substance
        # SYNTH_SYS: either a fresh draft or a section revision
        if "Problems to fix:" in prompt:
            self.calls.append("revise")
            self.revise_prompts.append(prompt)
            return self._revise.pop(0) if self._revise else "revised [@a1] and [@b2]."
        self.calls.append("draft")
        return self._draft.pop(0) if self._draft else "drafted [@a1] and [@b2]."

    def embed_batch(self, texts):
        return [self._vectors.get(t, [1.0, 0.0, 0.0]) for t in texts]


# ── compact digest ───────────────────────────────────────────────────────────

class _Src:
    def __init__(self, cites=0):
        self.cited_by_count = cites

    def author_year(self):
        return "Schelling 1971"


def test_compact_line_is_short_enough_for_the_whole_corpus():
    notes = [{"argument": "x " * 300, "themes": ["a", "b", "c", "d", "e", "f"]}]
    lines = summarize._compact_lines([_Src(120)], notes, {0: "schelling1971"})
    line = lines["schelling1971"]
    assert line.startswith("- [@schelling1971] (Schelling 1971, 120 cites)")
    assert len(line) < 320, f"compact line is {len(line)} chars — the planner sees all of them"
    assert line.endswith("]")            # themes, capped at 4


def test_truncate_keeps_whole_words():
    assert summarize._truncate("one two three four", 9) == "one two…"


# ── section planning ─────────────────────────────────────────────────────────

def test_plan_sections_parses_and_caps():
    plan = json.dumps([{"heading": f"Idea {i}", "claim": f"claim {i}"} for i in range(20)])
    secs = summarize._plan_sections(_FakeBrain(plan=plan), _Cfg(), COMPACT)
    assert len(secs) == summarize._MAX_SECTIONS
    assert secs[0].heading == "Idea 0" and secs[0].claim == "claim 0"


def test_plan_sections_skips_malformed_entries():
    plan = json.dumps([{"heading": "Good", "claim": "c"}, {"claim": "no heading"}, "junk"])
    secs = summarize._plan_sections(_FakeBrain(plan=plan), _Cfg(), COMPACT)
    assert [s.heading for s in secs] == ["Good"]


# ── shortlist: retrieval, not judgement ──────────────────────────────────────

def _one_hot(i, n=3):
    v = [0.0] * n
    v[i] = 1.0
    return v


def test_shortlist_ranks_by_cosine_and_costs_no_llm_call():
    sections = [Section("Segregation", "clustering emerges"),
                Section("Consonance", "spectra predict pleasantness")]
    vectors = {
        "Segregation. clustering emerges": _one_hot(0),
        "Consonance. spectra predict pleasantness": _one_hot(1),
        COMPACT["a1"]: _one_hot(0), COMPACT["b2"]: _one_hot(0),
        COMPACT["c3"]: _one_hot(1), COMPACT["d4"]: _one_hot(1),
        COMPACT["e5"]: _one_hot(2),
    }
    brain = _FakeBrain(vectors=vectors)
    matrix = summarize._shortlist(brain, sections, COMPACT, FULL, top_k=2)

    assert brain.calls == [], "shortlisting must not call the coordinator"
    assert set(sections[0].candidates) == {"a1", "b2"}
    assert set(sections[1].candidates) == {"c3", "d4"}
    assert len(matrix) == 2 and len(matrix[0]) == len(COMPACT)


def test_shortlist_respects_the_character_budget():
    """A section whose sources carry long digest lines still has to fit the drafting call."""
    sections = [Section("Idea", "claim")]
    fat = {k: "- [@%s] %s" % (k, "x" * 20_000) for k in CORPUS}
    summarize._shortlist(_FakeBrain(), sections, COMPACT, fat, top_k=5)
    assert len(sections[0].candidates) == 1  # only one 20k line fits in 24k chars


def test_cosine_handles_unembeddable_sources():
    assert summarize._cosine([], [1.0]) == 0.0
    assert summarize._cosine([1.0, 0.0], [1.0, 0.0]) == 1.0


# ── assembly + section-scoped guards ─────────────────────────────────────────

def test_assemble_skips_empty_sections():
    secs = [Section("A", "", text="para [@a1]."), Section("B", "", text="  ")]
    assert summarize._assemble(secs) == "## A\n\npara [@a1]."


def test_section_guards_see_a_section_not_a_review():
    """`thin_sections` and disposition are properties of the whole review and must not fire
    on one section in isolation; short_sections and sparse_paragraphs must."""
    sec = Section("Idea", "claim")
    findings = summarize._section_guards(sec, "One paragraph only [@a1] [@b2].", CORPUS)
    kinds = {f.kind for f in findings}
    assert "short-section" in kinds
    assert "thin-section" not in kinds
    assert all(f.section == 0 for f in findings)


def test_section_guards_flag_an_unresolvable_key():
    findings = summarize._section_guards(Section("I", "c"), "A claim [@ghost].", CORPUS)
    assert "unresolved-key" in {f.kind for f in findings}


# ── orphan placement, then the ledger ────────────────────────────────────────

def _sections_citing(*keysets):
    return [Section(f"S{i}", f"claim {i}", candidates=list(CORPUS),
                    text=" ".join(f"claim [@{k}]." for k in ks))
            for i, ks in enumerate(keysets)]


def test_orphans_are_offered_to_their_nearest_section():
    """Each orphan goes to the section it is closest to — using the similarity already
    computed for the shortlist — and an offer REWRITES one numbered paragraph, never appends
    a new one and never re-drafts the section."""
    sections = _sections_citing(["a1"], ["b2"])
    keys = ["a1", "b2", "c3", "d4", "e5"]
    # matrix[section][source]: c3, d4, e5 all sit closest to section 1
    matrix = [[1.0, 0.0, 0.0, 0.1, 0.0],
              [0.0, 1.0, 0.5, 0.9, 0.5]]
    brain = _FakeBrain(weave=["PARAGRAPH: 1\n\nREVISED:\nA deepened claim [@b2], now also "
                              "[@d4] and [@c3] and [@e5].\n\nDECLINED:\n{}"],
                       reject="{}")
    rejected = summarize._place_orphans(brain, _Cfg(), sections, matrix, keys, FULL,
                                        "SYS", CORPUS, COMPACT, rounds=1)
    assert brain.calls.count("weave") == 1, "section 0 is nearest to nothing; do not call it"
    assert "revise" not in brain.calls, "an offer must not re-emit the whole section"
    assert sections[0].text == "claim [@a1]."     # untouched
    assert "[@d4]" in sections[1].text
    assert "[@b2]" in sections[1].text, "a rewrite keeps the evidence the paragraph already had"
    assert len(summarize._paragraphs(sections[1].text)) == 1, "replaced in place, not appended"
    assert rejected == {}
    # the orphan's own digest line must reach the model, and the shortlist must not
    assert "[@d4] full digest" in brain.weave_prompts[0]
    assert "[@a1]" not in brain.weave_prompts[0], "a weave may only cite what it was offered"


def test_a_weave_that_drops_the_paragraphs_own_citations_is_refused():
    """A rewrite deepens a paragraph's argument; it must never cost the evidence already
    standing in it. Losing a citekey is a decline, so the orphan routes on instead."""
    sections = _sections_citing(["a1"], ["b2"])
    keys = ["a1", "b2", "c3", "d4", "e5"]
    matrix = [[1.0, 0.0, 0.0, 0.1, 0.0],
              [0.0, 1.0, 0.5, 0.9, 0.5]]
    # cites the orphan but silently drops [@b2], the paragraph's own source
    brain = _FakeBrain(weave=["PARAGRAPH: 1\n\nREVISED:\nOnly [@d4] now.\n\nDECLINED:\n{}"],
                       reject='{"c3": "no", "d4": "no", "e5": "no"}')
    summarize._place_orphans(brain, _Cfg(), sections, matrix, keys, FULL,
                             "SYS", CORPUS, COMPACT, rounds=1)
    assert sections[1].text == "claim [@b2].", "the original paragraph survives untouched"


def test_a_weave_that_restates_the_paragraph_and_appends_is_refused():
    """The accretion shape: the paragraph echoed back with new sentences bolted on. This is
    what stacked 126 -> 188 -> 284-word near-duplicates across placement rounds."""
    sections = _sections_citing(["a1"], ["b2"])
    keys = ["a1", "b2", "c3", "d4", "e5"]
    matrix = [[1.0, 0.0, 0.0, 0.1, 0.0],
              [0.0, 1.0, 0.5, 0.9, 0.5]]
    original = sections[1].text
    echo = f"{original} Furthermore [@d4] agrees."
    brain = _FakeBrain(weave=[f"PARAGRAPH: 1\n\nREVISED:\n{echo}\n\nDECLINED:\n{{}}"],
                       reject='{"c3": "no", "d4": "no", "e5": "no"}')
    summarize._place_orphans(brain, _Cfg(), sections, matrix, keys, FULL,
                             "SYS", CORPUS, COMPACT, rounds=1)
    assert sections[1].text == original, "an echo-plus-tail rewrite is refused"


def test_a_declined_orphan_is_offered_to_the_next_nearest_section_then_stops():
    """A refusal routes the source onward once — embedding proximity is a cosine, not a
    judgement. It does not shop the source through every section until one relents."""
    sections = _sections_citing(["a1"], ["b2"], ["c3"])
    keys = ["a1", "b2", "c3", "d4", "e5"]
    matrix = [[1.0, 0.0, 0.0, 0.9, 0.0],    # d4: §0 nearest, then §1, then §2
              [0.0, 1.0, 0.0, 0.5, 0.0],
              [0.0, 0.0, 1.0, 0.1, 0.0]]
    refuse = 'PARAGRAPH:\nNONE\n\nDECLINED:\n{"d4": "bears on nothing here", "e5": "ditto"}'
    brain = _FakeBrain(weave=[refuse, refuse, refuse],
                       reject='{"d4": "no section could use it", "e5": "off topic"}')
    rejected = summarize._place_orphans(brain, _Cfg(), sections, matrix, keys, FULL,
                                        "SYS", CORPUS, COMPACT, rounds=4)
    assert brain.calls.count("weave") == 2, "two hearings for d4/e5, then the ledger"
    assert set(rejected) == {"d4", "e5"}
    # the ledger sees why each section refused it, not just that it is uncited
    assert "bears on nothing here" in brain.reject_prompts[0]


def test_a_woven_paragraph_is_written_in_the_authors_voice():
    """The paragraph is appended to a section drafted under the style profile; it has to
    carry the same profile or the seam shows."""
    styled = "You synthesise.\n\nWRITING STYLE\nMatch the voice:\nlong sinuous clauses"
    assert "long sinuous clauses" in summarize._weave_sys(styled)
    assert "You add evidence to one section" in summarize._weave_sys(styled)
    assert summarize._weave_sys("You synthesise.") == summarize._WEAVE_SYS


def test_the_rejection_prompt_is_batched_under_the_context_budget():
    """One call carrying every orphan's digest overran num_ctx and Ollama ate the head of the
    prompt — outline included — so 31 of 32 sources came back neither cited nor justified."""
    corpus = {f"k{i:02d}" for i in range(40)}
    compact = {k: f"- [@{k}] " + "x" * 400 for k in corpus}
    sections = [Section("S0", "c", candidates=[], text="claim [@k00].")]
    brain = _FakeBrain(reject="{}")
    summarize._reject_ledger(brain, _Cfg(), sections, corpus - {"k00"}, compact, {})
    assert brain.calls.count("reject") > 1, "39 orphans must not go out in one prompt"
    assert all(len(p) < summarize._REJECT_BATCH_CHARS + 2_000
               for p in brain.reject_prompts), "a batch overran the budget"


def test_survivors_must_be_rejected_by_name():
    sections = _sections_citing(["a1", "b2", "c3", "d4"])
    keys = ["a1", "b2", "c3", "d4", "e5"]
    matrix = [[1, 1, 1, 1, 1]]
    brain = _FakeBrain(weave=['PARAGRAPH:\nNONE\n\nDECLINED:\n{"e5": "different construct"}'],
                       reject='{"e5": "measures a different construct entirely"}')
    rejected = summarize._place_orphans(brain, _Cfg(), sections, matrix, keys, FULL,
                                        "SYS", CORPUS, COMPACT, rounds=1)
    assert rejected == {"e5": "measures a different construct entirely"}
    assert brain.calls.count("reject") == 1


def test_a_cited_source_cannot_be_rejected():
    sections = _sections_citing(list(CORPUS))
    brain = _FakeBrain(reject='{"a1": "not relevant"}')
    rejected = summarize._place_orphans(brain, _Cfg(), sections, [[1] * 5],
                                        sorted(CORPUS), FULL, "SYS", CORPUS, rounds=1)
    assert rejected == {}
    assert "reject" not in brain.calls   # nothing undecided, so nothing to justify


def test_unjustified_omission_is_reported_not_absorbed():
    sections = _sections_citing(["a1"])
    brain = _FakeBrain(reject="{}")     # every section declines; the ledger justifies nothing
    rejected = summarize._place_orphans(brain, _Cfg(), sections, [[1] * 5],
                                        sorted(CORPUS), FULL, "SYS", CORPUS, COMPACT, rounds=1)
    assert rejected == {}
    d = guards.disposition(summarize._assemble(sections), CORPUS, rejected)
    assert d.unplaced == {"b2", "c3", "d4", "e5"}
    assert "unplaced 4" in guards.metrics(summarize._assemble(sections), CORPUS).line()


# ── repair is routed to the section at fault ─────────────────────────────────

def test_repair_redrafts_only_the_offending_section():
    good = ("first [@a1] and [@b2].\n\nsecond brings [@c3] alongside [@a1].\n\n"
            "third adds [@d4] against [@b2].")
    bad = "one paragraph [@e5] only [@a1]."
    sections = [Section("Good", "c", candidates=list(CORPUS), text=good),
                Section("Bad", "c", candidates=list(CORPUS), text=bad)]
    brain = _FakeBrain(revise=[good])
    summarize._repair_assembly(brain, _Cfg(), sections, FULL, "SYS", CORPUS, rounds=1)
    assert brain.calls.count("revise") == 1, "only the short section should be re-drafted"
    assert "§2" not in sections[0].text
    assert sections[1].text == good


def test_repair_is_a_noop_on_a_clean_assembly():
    good = ("first [@a1] and [@b2].\n\nsecond brings [@c3] alongside [@a1].\n\n"
            "third adds [@d4] against [@b2] and [@e5].")
    sections = [Section("A", "c", candidates=list(CORPUS), text=good),
                Section("B", "c", candidates=list(CORPUS), text=good)]
    brain = _FakeBrain()
    summarize._repair_assembly(brain, _Cfg(), sections, FULL, "SYS", CORPUS, rounds=2)
    assert brain.calls == []


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failures = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception:  # noqa: BLE001
            failures += 1
            print(f"  FAIL  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failures}/{len(fns)} passed")
    raise SystemExit(1 if failures else 0)


# ── the load-bearing header (the coverage check) ─────────────────────────────

def _corpus_of(*keys):
    from rabbithole.models import Author, Candidate
    return ([Candidate(title=f"Paper {k}", authors=[Author(family=f"Auth{k}")], year=2020)
             for k in keys],
            {i: k for i, k in enumerate(keys)})


def test_top_source_count_is_a_corpus_share_floored_and_capped():
    assert summarize.top_source_count(181) == 9          # 5% of the corpus
    assert summarize.top_source_count(40) == 5           # floor: a small review still gets a list
    assert summarize.top_source_count(600) == 15         # ceiling: a huge one stays scannable
    assert summarize.top_source_count(0) == 5


def test_top_sources_ranks_by_the_argument_each_source_carries():
    """Importance is words of review prose devoted to a source, not citation count — a paper the
    review builds a paragraph on outranks one it name-checks in a list."""
    corpus, citekeys = _corpus_of("heavy", "light", "unused")
    narrative = (
        "## T\n\n"
        "The framework establishes that coordinated pricing reshapes firm-level investment "
        "across every sector the model covers, and that the effect compounds over time [@heavy]. "
        "Also [@light].\n")
    top = summarize.top_sources(narrative, corpus, citekeys)
    assert [k for k, _c, _w in top] == ["heavy", "light"]     # uncited source cannot rank
    assert top[0][2] > top[1][2]


def test_top_sources_block_carries_a_reason_per_source():
    corpus, citekeys = _corpus_of("a1", "b2")
    narrative = "## T\n\nA claim [@a1]. Another claim [@b2].\n"

    class _B:
        backend = "ollama"
        def coordinator(self, prompt, sys="", **kw):
            assert "a1" in prompt and "evidence:" in prompt   # grounded in the review's own prose
            return '{"a1": "supplies the pricing mechanism the argument turns on", ' \
                   '"b2": "supplies the leakage counter-case"}'
    block = summarize.top_sources_block(_B(), _Cfg(), narrative, corpus, citekeys)
    assert block.startswith("## Most load-bearing sources (top 5% of 2)")
    assert "[@a1] — supplies the pricing mechanism" in block
    assert "[@b2] — supplies the leakage counter-case" in block


def test_top_sources_block_still_lists_when_the_rationale_call_fails():
    """The ranking is deterministic; only the sentences need a model. A failed call must not
    cost the coverage check."""
    corpus, citekeys = _corpus_of("a1")
    narrative = "## T\n\nA claim [@a1].\n"

    class _B:
        backend = "ollama"
        def coordinator(self, *a, **kw):
            raise RuntimeError("ollama down")
    block = summarize.top_sources_block(_B(), _Cfg(), narrative, corpus, citekeys)
    assert "[@a1]" in block and "words of the review's argument" in block


def test_top_sources_block_is_empty_when_nothing_is_cited():
    corpus, citekeys = _corpus_of("a1")
    assert summarize.top_sources_block(None, _Cfg(), "## T\n\nNo citations here.\n",
                                       corpus, citekeys) == ""


# ── the curated-tier screen ──────────────────────────────────────────────────

def _bib_corpus():
    from rabbithole.models import Author, Candidate
    return [Candidate(title="Cited work", authors=[Author(family="Able")], year=2020),
            Candidate(title="Off topic", authors=[Author(family="Baker")], year=2004),
            Candidate(title="Curated", authors=[Author(family="Carter")], year=2021)]


def test_a_source_whose_own_annotation_disqualifies_it_leaves_the_curated_list():
    """Pacala & Socolow shipped as a 'curated source' under an annotation calling it
    'largely irrelevant to the specific review focus'."""
    located = {
        0: [{"claim": "Supplies the pricing mechanism.", "location": "p.3", "quote": "q"}],
        1: [{"claim": "While foundational, this paper is largely irrelevant to the specific "
                      "review focus, as it lacks any analysis of firm-level innovation.",
             "location": "p.2", "quote": "q"}],
        2: [{"claim": "Offers a directly comparable leakage estimate.", "location": "p.1",
             "quote": "q"}],
    }
    bib = summarize.bibliography(_bib_corpus(), located, cited_indices={0})
    assert "### Additional curated sources" in bib
    assert "Carter" in bib.split("### Additional curated sources")[1].split("###")[0]
    assert "### Screened out of the curated list" in bib
    screened = bib.split("### Screened out of the curated list")[1]
    assert "Baker" in screened and "largely irrelevant to" in screened
    assert "remain in the corpus" in screened, "a screen records a judgement, it deletes nothing"


def test_the_screen_never_touches_a_source_the_review_actually_cites():
    """A source the narrative uses has earned its place however its annotation is worded."""
    located = {0: [{"claim": "This does not address household impacts, but supplies the "
                             "pricing mechanism.", "location": "p.3", "quote": "q"}]}
    bib = summarize.bibliography(_bib_corpus(), located, cited_indices={0})
    assert "Able" in bib.split("### Cited in the review")[1]
    assert "Screened out" not in bib


def test_the_screen_can_be_turned_off():
    located = {1: [{"claim": "largely irrelevant to this review.", "location": "p.1",
                    "quote": "q"}]}
    bib = summarize.bibliography(_bib_corpus(), located, cited_indices=set(),
                                 screen_curated=False)
    assert "Screened out" not in bib and "Baker" in bib


def test_self_disqualified_needs_a_real_phrase_not_a_hedge():
    assert summarize.self_disqualified([{"claim": "This is only partly relevant but useful."}]) == ""
    assert summarize.self_disqualified([{"claim": "Irrelevant to the review's focus."}])

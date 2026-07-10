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
                 revise=None, reject="{}", vectors=None):
        self.cfg = _BrainCfg()
        self._plan = plan
        self._draft = list(draft or [])
        self._lint = lint
        self._substance = substance
        self._revise = list(revise or [])
        self._reject = reject
        self._vectors = vectors or {}
        self.calls: list[str] = []
        self.revise_prompts: list[str] = []

    def coordinator(self, prompt, sys_prompt, **kw):
        s = sys_prompt.lower()
        if "plan the thematic sections" in s:
            self.calls.append("plan")
            return self._plan
        if "json object mapping each citekey" in s:
            self.calls.append("reject")
            return self._reject
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
    """Only the sections that gain a source are re-drafted, and each orphan goes to the
    section it is closest to — using the similarity already computed for the shortlist."""
    sections = _sections_citing(["a1"], ["b2"])
    keys = ["a1", "b2", "c3", "d4", "e5"]
    # matrix[section][source]: c3, d4, e5 all sit closest to section 1
    matrix = [[1.0, 0.0, 0.0, 0.1, 0.0],
              [0.0, 1.0, 0.5, 0.9, 0.5]]
    brain = _FakeBrain(revise=["claim [@b2]. and now [@d4] and [@c3] and [@e5]."],
                       reject="{}")
    rejected = summarize._place_orphans(brain, _Cfg(), sections, matrix, keys, FULL,
                                        "SYS", CORPUS, rounds=1)
    assert brain.calls.count("revise") == 1, "section 0 gained nothing; do not re-draft it"
    assert sections[0].text == "claim [@a1]."     # untouched
    assert "[@d4]" in sections[1].text
    assert rejected == {}
    # the orphan's own digest line must reach the reviser
    assert "[@d4]" in brain.revise_prompts[0]


def test_survivors_must_be_rejected_by_name():
    sections = _sections_citing(["a1", "b2", "c3", "d4"])
    keys = ["a1", "b2", "c3", "d4", "e5"]
    matrix = [[1, 1, 1, 1, 1]]
    brain = _FakeBrain(revise=["claim [@a1] [@b2] [@c3] [@d4]."],   # refuses to take e5
                       reject='{"e5": "measures a different construct entirely"}')
    rejected = summarize._place_orphans(brain, _Cfg(), sections, matrix, keys, FULL,
                                        "SYS", CORPUS, rounds=1)
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
    brain = _FakeBrain(revise=["claim [@a1]."], reject="{}")
    rejected = summarize._place_orphans(brain, _Cfg(), sections, [[1] * 5],
                                        sorted(CORPUS), FULL, "SYS", CORPUS, rounds=1)
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

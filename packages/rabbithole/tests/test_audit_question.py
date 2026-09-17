"""What the audit judges against, and what invalidates its cache.

Two faults, one cause. `gather_topics` carries the asks a cycle exists to cover, and the
audit — the third consumer of "what is this review about", after query generation and the
ranker — never read them. Judging without them inverts the guard: a paper fetched FOR an ask,
against a question that no longer names the ask, looks exactly like a source sharing
vocabulary without transferring, which is what this verb quarantines.

And the cache signature hashed the literal string, so reordering a clause or de-duplicating
one re-judged the whole corpus — 236 papers at ~17 minutes each on elephantRoom.

Runnable two ways:
    pytest tests/test_audit_question.py
    python tests/test_audit_question.py
"""

from __future__ import annotations

from rabbithole.audit import _clauses, _sig

_T = "Climate mitigation collective action"


# ── the signature survives edits that leave the question alone ───────────────

def test_reordering_clauses_keeps_the_cache():
    assert _sig(_T, "carbon taxes; climate clubs") == _sig(_T, "climate clubs; carbon taxes")


def test_case_and_punctuation_keep_the_cache():
    assert _sig(_T, "Carbon Taxes; Climate Clubs!") == _sig(_T, "carbon taxes, climate clubs")


def test_a_duplicated_clause_keeps_the_cache():
    """`_append_focus` leaked duplicates for several cycles; each one re-judged the corpus."""
    assert _sig(_T, "a; b; b") == _sig(_T, "a; b")


def test_semicolons_and_commas_separate_alike():
    assert _clauses("a, b; c") == ["a", "b", "c"]


# ── but a real change to the question still invalidates it ───────────────────

def test_a_new_clause_invalidates():
    assert _sig(_T, "carbon taxes") != _sig(_T, "carbon taxes; supply chain dependency")


def test_a_removed_clause_invalidates():
    """Narrowing the question is a real change — verdicts were made against the wider one."""
    assert _sig(_T, "carbon taxes; households") != _sig(_T, "carbon taxes")


def test_a_different_topic_invalidates():
    assert _sig("topic one", "same focus") != _sig("topic two", "same focus")


def test_the_signature_is_stable_across_processes():
    """Not builtin hash(): it is salted per process, which would discard the cache every run
    and make the incremental checkpoints useless for resuming."""
    import subprocess, sys
    got = subprocess.run(
        [sys.executable, "-c",
         "from rabbithole.audit import _sig; print(_sig('t', 'a; b'))"],
        capture_output=True, text=True).stdout.strip()
    assert got == _sig("t", "a; b")


# ── the question includes this cycle's asks ──────────────────────────────────

def test_an_ask_is_one_unit_not_split_on_its_commas():
    """An ask is a prose sentence. Splitting it on its commas would make the signature turn on
    where a subordinate clause happens to fall, which is not a change to the question."""
    ask = "Sugarscape and artificial society models. Analyses and extensions, including "\
          "wealth distribution, inheritance and the agent life course."
    other = "Sugarscape and artificial society models. Analyses and extensions; including "\
            "wealth distribution; inheritance and the agent life course."
    assert _sig(_T, "f", [ask]) == _sig(_T, "f", [other]), "punctuation inside an ask is wording"
    assert _sig(_T, "f", [ask]) != _sig(_T, "f", [ask + " And bootstrap resampling."])


def test_adding_an_ask_invalidates():
    assert _sig(_T, "f", ["ask one"]) != _sig(_T, "f", ["ask one", "ask two"])


def test_ask_order_does_not_matter():
    assert _sig(_T, "f", ["a", "b"]) == _sig(_T, "f", ["b", "a"])


def test_an_ask_is_not_confusable_with_a_focus_clause():
    """The two channels are separated in the payload, so moving a strand from one to the other
    is a real change — which is what happened on elephantRoom, and why re-judging was right."""
    assert _sig(_T, "carbon taxes; households", []) != _sig(_T, "carbon taxes", ["households"])


def test_the_asks_reach_the_question(tmp_path, monkeypatch):
    """The regression this closes: a paper gathered for an ask, judged against a question that
    does not mention the ask, is indistinguishable from a false friend."""
    import types
    from rabbithole import audit

    cfg = types.SimpleNamespace(
        topic="prosopography on ABM output", focus="opinion dynamics; narrative cognition",
        gather_topics=["Sugarscape and artificial society models",
                       "Bootstrap and resampling for simulation output"])
    question = "; ".join([f for f in [cfg.focus or ""] +
                          [str(t) for t in (cfg.gather_topics or [])] if f.strip()])
    clauses = _clauses(question)
    assert any("sugarscape" in c for c in clauses)
    assert any("bootstrap" in c or "resampling" in c for c in clauses)
    assert audit._sig(cfg.topic, cfg.focus, cfg.gather_topics) != audit._sig(cfg.topic, cfg.focus)


def test_a_project_with_no_asks_is_unchanged():
    """Most cycles have none; those must not see a different signature for it."""
    focus = "opinion dynamics; narrative cognition"
    assert _sig(_T, focus, []) == _sig(_T, focus)


if __name__ == "__main__":
    import traceback, tempfile
    from pathlib import Path

    class _MP:
        def setattr(self, o, n, v): setattr(o, n, v)

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failures = 0
    for fn in fns:
        try:
            n = fn.__code__.co_argcount
            with tempfile.TemporaryDirectory() as td:
                fn(*[Path(td), _MP()][:n])
            print(f"  PASS  {fn.__name__}")
        except Exception:  # noqa: BLE001
            failures += 1
            print(f"  FAIL  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failures}/{len(fns)} passed")
    raise SystemExit(1 if failures else 0)


# ── widening the question is cheap; rewriting it is not ──────────────────────

class _Paths:
    def __init__(self, tmp): self.output = tmp


def _write(paths, sig, units, verdicts):
    from rabbithole import audit
    audit._save_cache(paths, sig, verdicts, units)


def test_adding_an_ask_keeps_the_transfer_verdicts(tmp_path):
    """A paper that transferred to the narrower question still transfers to the wider one —
    nothing its verdict rested on was taken away. Only the quarantined few can change.
    elephantRoom would have re-judged 1 paper instead of 236."""
    from rabbithole import audit
    paths = _Paths(tmp_path)
    old_units = audit._units("t", "stated question", ["ask one"])
    _write(paths, audit._sig("t", "stated question", ["ask one"]), old_units,
           {"A": {"kind": "transfer"}, "B": {"kind": "transfer"},
            "C": {"kind": "false_friend"}})
    new_asks = ["ask one", "ask two"]
    kept = audit._load_cache(paths, audit._sig("t", "stated question", new_asks),
                             audit._units("t", "stated question", new_asks))
    assert set(kept) == {"A", "B"}, "transfers kept, the quarantined one re-judged"


def test_removing_a_clause_discards_everything(tmp_path):
    """Verdicts made against a wider question do not hold for a narrower one."""
    from rabbithole import audit
    paths = _Paths(tmp_path)
    _write(paths, audit._sig("t", "a; b", []), audit._units("t", "a; b", []),
           {"A": {"kind": "transfer"}})
    assert audit._load_cache(paths, audit._sig("t", "a", []),
                             audit._units("t", "a", [])) == {}


def test_rewording_the_question_discards_everything(tmp_path):
    from rabbithole import audit
    paths = _Paths(tmp_path)
    _write(paths, audit._sig("t", "carbon taxes", []), audit._units("t", "carbon taxes", []),
           {"A": {"kind": "transfer"}})
    assert audit._load_cache(paths, audit._sig("t", "household savings", []),
                             audit._units("t", "household savings", [])) == {}


def test_an_unchanged_question_keeps_every_verdict(tmp_path):
    from rabbithole import audit
    paths = _Paths(tmp_path)
    sig, units = audit._sig("t", "q", ["a"]), audit._units("t", "q", ["a"])
    _write(paths, sig, units, {"A": {"kind": "transfer"}, "B": {"kind": "false_friend"}})
    assert set(audit._load_cache(paths, sig, units)) == {"A", "B"}


def test_a_cache_written_before_units_existed_is_discarded(tmp_path):
    """Old cache files carry no unit list; there is nothing to compare, so they go."""
    from rabbithole import audit
    (tmp_path / "audit_cache.json").write_text(
        '{"sig": "old", "verdicts": {"A": {"kind": "transfer"}}}')
    assert audit._load_cache(_Paths(tmp_path), "new", audit._units("t", "q", [])) == {}


def test_moving_a_strand_from_stated_to_asks_is_a_real_change(tmp_path):
    """The two channels are separate regions of the payload — which is why elephantRoom's
    re-judge was correct when four strands moved out of the focus."""
    from rabbithole import audit
    assert audit._units("t", "q; households", []) != audit._units("t", "q", ["households"])


# ── the question must survive the trip to the model ──────────────────────────

def test_judge_item_sizes_the_window_to_the_whole_prompt():
    """The bug this file's first half fixed created a second one. Once the question became
    the research prompt plus the asks (5,005 chars on DigiPros), it no longer fit the 2048
    `judge_item` had pinned for the old one-line focus — and `_prompt` puts the question
    FIRST, so the head Ollama discards is precisely the question. 154 papers were judged
    against a question the model could not see; Stauffer 2007 was quarantined 9/10 as a
    "Schelling model" by a paper that demonstrates on Schelling.
    """
    from rabbithole.audit import judge_item, _SYS
    from rabbithole.brain import Brain

    seen = {}

    class _Brain:
        def coordinator(self, prompt, system="", num_ctx=16384, think=True):
            seen["prompt"], seen["system"], seen["num_ctx"] = prompt, system, num_ctx
            return '{"verdict": "TRANSFER", "confidence": 8}'

    # DigiPros's real shape: a research prompt plus nine asks, ~5,000 characters
    question = "; ".join(f"ask {i}: " + "narrative extraction from agent trajectories " * 11
                         for i in range(9))
    assert len(question) > 4500, "the question that broke it was 5,005 chars"

    judge_item(_Brain(), _T, question, key="k", label="Stauffer 2007",
               title="Social percolation and the Schelling model",
               abstract="x" * 1500, keywords=("segregation",))

    # what Brain will actually send, floor plus any growth it decides on
    final = Brain._fit_context(Brain.__new__(Brain), seen["prompt"], seen["system"],
                               seen["num_ctx"], "test")
    est = (len(seen["prompt"]) + len(seen["system"])) // 4
    assert est <= int(final * 0.65), f"{est} tokens asked of a {final} window"
    assert est > int(2048 * 0.65), "the prompt that broke it must still overflow 2048"


def test_judge_item_asks_for_no_more_window_than_it_needs():
    """Opposite failure, same cost: the KV cache is linear in num_ctx, and this runs once
    per paper. Defaulting to the coordinator's 16384 would buy 8x the VRAM for nothing."""
    from rabbithole.audit import judge_item

    seen = {}

    class _Brain:
        def coordinator(self, prompt, system="", num_ctx=16384, think=True):
            seen["num_ctx"] = num_ctx
            return '{"verdict": "TRANSFER", "confidence": 8}'

    judge_item(_Brain(), _T, "one short focus line", key="k", label="l",
               title="t", abstract="short")
    assert seen["num_ctx"] == 4096


# ── the needs are the test, not the paper's own contribution ─────────────────

def _rendered(asks, background="A method for extracting agent narratives from ABM output.",
              title="Social percolation and the Schelling model", abstract="x"):
    from rabbithole.audit import _prompt
    return _prompt(_T, background, asks, title, abstract, ("segregation",))


def test_the_asks_are_presented_as_needs_not_fused_into_the_question():
    """`stated` and the asks were joined with '; ' into one string. The author's statement says
    what the work CONTRIBUTES; an ask says what literature it NEEDS. Fused, the model compared
    contribution to contribution and quarantined 43 of the first 49 DigiPros papers at 9/10."""
    out = _rendered(["schelling segregation model scholarship",
                     "sequence analysis of individual trajectories"])
    assert "[1] schelling segregation model scholarship" in out
    assert "[2] sequence analysis of individual trajectories" in out


def test_the_background_is_labelled_as_context_and_not_as_a_template():
    out = _rendered(["schelling segregation model scholarship"])
    head, _, tail = out.partition("Background")
    assert tail, "the author's statement must be labelled as background"
    assert "NOT a checklist the source must match" in tail
    # the needs lead, the statement follows: the test comes before the context for it
    assert "[1] schelling segregation model scholarship" in head


def test_the_test_is_restated_last_where_it_is_most_salient():
    out = _rendered(["schelling segregation model scholarship"])
    assert out.rstrip().endswith("Verdict JSON:")
    assert "usable source for at least one of the numbered points" in out.split("Candidate")[-1]


def test_a_first_cycle_with_no_asks_still_states_a_need():
    """No gather_topics yet. The statement is then the only description of need there is, so it
    serves as one — but still as a need, never as a template the source has to match."""
    out = _rendered([], background="A method for extracting agent narratives.")
    assert "[1] A method for extracting agent narratives." in out
    assert "NOT a checklist the source must match" in out


def test_the_system_prompt_does_not_ask_for_a_matching_contribution():
    """The reasons on every DigiPros quarantine contrasted the paper's subject against the
    review's framing — a difference in ROLE, not word sense. Stauffer 2007 was dropped 9/10
    because it studies segregation while the paper only demonstrates on Schelling, which is
    true of nearly every good source a methodological paper can have."""
    from rabbithole.audit import _SYS
    assert "USABLE SOURCE FOR AT LEAST ONE" in _SYS
    assert "demonstrates upon" in _SYS          # that role difference is named as normal
    assert "serving NONE of the stated needs" in _SYS
    assert "same argument" in _SYS              # and explicitly ruled out as the test


# ── a framing change is not a question change ────────────────────────────────

def test_a_framing_bump_discards_every_cached_verdict(tmp_path):
    """The signature hashes the question's WORDS, which this change left alone. Without a
    separate version, DigiPros's 49 verdicts — decided under the old test — would be reused
    verbatim, and the widening rule would have kept them as 'strictly wider'."""
    import json
    from types import SimpleNamespace
    from rabbithole import audit

    paths = SimpleNamespace(output=tmp_path)
    sig, units = "same-sig", ["t:x", "q:y"]
    (tmp_path / "audit_cache.json").write_text(json.dumps(
        {"sig": sig, "framing": audit._FRAMING - 1, "units": units,
         "verdicts": {"K1": {"kind": "false_friend", "confidence": 9.0},
                      "K2": {"kind": "transfer", "confidence": 8.0}}}))

    assert audit._load_cache(paths, sig, units) == {}


def test_the_cache_still_survives_a_pure_rewording(tmp_path):
    """The expensive thing is a whole-corpus re-judge; the framing guard must not make every
    run one. Same framing, same question, reordered clauses -> the cache is kept."""
    from types import SimpleNamespace
    from rabbithole import audit

    paths = SimpleNamespace(output=tmp_path)
    units = audit._units(_T, "carbon taxes; climate clubs")
    sig = audit._sig(_T, "carbon taxes; climate clubs")
    audit._save_cache(paths, sig, {"K1": {"kind": "transfer", "confidence": 8.0}}, units)

    sig2 = audit._sig(_T, "climate clubs, carbon taxes")
    units2 = audit._units(_T, "climate clubs, carbon taxes")
    assert audit._load_cache(paths, sig2, units2).keys() == {"K1"}


def test_judge_item_does_not_pay_for_a_reasoning_chain():
    """The coordinator reasons by default. For this judgement it was pure cost: over 8
    DigiPros papers judged both ways (2026-09-17) the keep/quarantine verdict agreed 8/8
    and confidence moved by at most a point, while the cost went 1,273s to 112s per item —
    54 hours against 5 for a 154-paper corpus. The numbered needs in `_prompt` already make
    the model restate the question, which is what the chain was spending its time on.
    """
    from rabbithole.audit import judge_item

    seen = {}

    class _Brain:
        def coordinator(self, prompt, system="", num_ctx=16384, think=True):
            seen["think"] = think
            return '{"verdict": "TRANSFER", "confidence": 9}'

    judge_item(_Brain(), _T, "a focus line", key="k", label="l", title="t", abstract="a")
    assert seen["think"] is False

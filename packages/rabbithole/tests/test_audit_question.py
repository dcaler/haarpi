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

"""Control-flow tests for the redline per-paragraph adversary (no GPU / no LLM).

A fake brain returns scripted outputs so we can exercise every branch of
`_redline_para_adversary`: the deterministic citation guards (author-year, dropped
citekey, uncited), the re-revise loop, and the audit's CORPUS routing.

Runnable two ways:
    pytest tests/test_revise_adversary.py
    python tests/test_revise_adversary.py
"""

from __future__ import annotations

from rabbithole import revise


class _FakeCfg:
    topic = "tonal stability"
    focus = "chord distance metrics"


class _BrainCfg:
    critique_rounds = 3


class _FakeBrain:
    """Scripts coordinator outputs, routing by whether the system prompt is the audit one."""

    def __init__(self, revise_outputs, audit_outputs):
        self.cfg = _BrainCfg()
        self._revise = list(revise_outputs)
        self._audit = list(audit_outputs)
        self.revise_calls = 0
        self.audit_calls = 0

    def coordinator(self, prompt, sys_prompt, **kw):
        if "audit" in sys_prompt.lower():
            self.audit_calls += 1
            return self._audit.pop(0)
        self.revise_calls += 1
        return self._revise.pop(0)


DIGEST = "- [@bowling2018] vocal similarity predicts consonance\n- [@x1] some source"


def test_author_year_guard_then_clean():
    """Round 1 emits author-year (and drops the citekey); the guard forces a clean round 2."""
    brain = _FakeBrain(
        revise_outputs=["Beta falls, per Bowling et al. (2018).",
                        "Beta falls under load [@bowling2018]."],
        audit_outputs=["OK"])
    text, outcome = revise._redline_para_adversary(
        brain, _FakeCfg(), "Beta rises [@bowling2018].", ["quantify this"], DIGEST)
    assert outcome == "edited"
    assert text == "Beta falls under load [@bowling2018]."
    assert brain.revise_calls == 2      # guard rejected round 1, accepted round 2
    assert brain.audit_calls == 1       # audit only runs once guards pass


def test_uncited_guard_then_clean():
    brain = _FakeBrain(
        revise_outputs=["Now with no citation at all.",
                        "Now grounded [@x1]."],
        audit_outputs=["OK"])
    text, outcome = revise._redline_para_adversary(
        brain, _FakeCfg(), "Old point [@x1].", ["clarify"], DIGEST)
    assert outcome == "edited"
    assert text == "Now grounded [@x1]."
    assert brain.revise_calls == 2


def test_corpus_routing_when_audit_says_corpus():
    """A clean rewrite whose audit returns CORPUS must NOT be applied — route it instead."""
    brain = _FakeBrain(
        revise_outputs=["A summary of the data [@x1]."],
        audit_outputs=["CORPUS: this asks for a table; prose can't satisfy it."])
    text, outcome = revise._redline_para_adversary(
        brain, _FakeCfg(), "Lots of numbers here [@x1].", ["a table is better"], DIGEST)
    assert outcome == "corpus"
    assert text is None


def test_clean_first_try():
    brain = _FakeBrain(
        revise_outputs=["Revised cleanly [@x1]."],
        audit_outputs=["OK"])
    text, outcome = revise._redline_para_adversary(
        brain, _FakeCfg(), "Old [@x1].", ["reword"], DIGEST)
    assert outcome == "edited"
    assert brain.revise_calls == 1 and brain.audit_calls == 1


def test_exhausted_rounds_with_dirty_output_is_skipped():
    """If every round stays author-year, don't emit a malformed edit."""
    brain = _FakeBrain(
        revise_outputs=["Smith (2021) says x.", "Smith (2021) still.", "Smith (2021) again."],
        audit_outputs=[])
    text, outcome = revise._redline_para_adversary(
        brain, _FakeCfg(), "Old [@x1].", ["expand"], DIGEST)
    assert outcome == "skipped"
    assert text is None


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

"""Control-flow tests for the redline per-paragraph adversary (no GPU / no LLM).

A fake brain returns scripted outputs so we can exercise every branch of
`_redline_para_adversary`: the deterministic guards (author-year, dropped citekey, uncited,
dropped/invented equation, minimal-edit), the re-revise loop, the audit's CORPUS routing with
its reason class, and the fail-closed exits.

The reviser returns CHANGED SENTENCES KEYED BY INDEX, so a scripted output here is a JSON
object — and the set of sentences it touched is known exactly, not inferred from a diff.

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


class _ExplodingAudit(_FakeBrain):
    def coordinator(self, prompt, sys_prompt, **kw):
        if "audit" in sys_prompt.lower():
            self.audit_calls += 1
            raise RuntimeError("brain unreachable")
        self.revise_calls += 1
        return self._revise.pop(0)


DIGEST = "- [@bowling2018] vocal similarity predicts consonance\n- [@x1] some source"

# Three sentences; a comment anchored to the second one.
PARA = "Alpha rises [@x1]. Beta rises [@bowling2018]. Gamma is stable [@x1]."
ANCHOR = {1}


def _run(brain, paragraph=PARA, comments=("quantify this",), anchored=ANCHOR):
    return revise._redline_para_adversary(
        brain, _FakeCfg(), paragraph, list(comments), DIGEST, anchored=set(anchored))


# ── sentence-edit plumbing ───────────────────────────────────────────────────

def test_untouched_sentences_are_copied_byte_for_byte():
    units = revise.guards.sentence_units(PARA)
    out = revise._apply_sentence_edits(units, {"2": "Beta falls by 42% [@bowling2018]."})
    assert out == ("Alpha rises [@x1]. Beta falls by 42% [@bowling2018]. "
                   "Gamma is stable [@x1].")


def test_null_deletes_a_sentence():
    units = revise.guards.sentence_units(PARA)
    out = revise._apply_sentence_edits(units, {"2": None})
    assert out == "Alpha rises [@x1]. Gamma is stable [@x1]."


def test_out_of_range_and_malformed_keys_are_errors():
    edits, errors = revise._parse_sentence_edits('{"9": "x"}', 3)
    assert edits == {} and errors
    edits, errors = revise._parse_sentence_edits("not json at all", 3)
    assert edits == {} and errors


# ── deterministic guards run first, and short-circuit the audit ──────────────

def test_author_year_guard_then_clean():
    """Round 1 emits author-year prose; the guard forces a clean round 2. The audit — the
    expensive call — never sees the broken paragraph."""
    brain = _FakeBrain(
        revise_outputs=['{"2": "Beta falls, per Bowling et al. (2018)."}',
                        '{"2": "Beta falls under load [@bowling2018]."}'],
        audit_outputs=["OK"])
    text, outcome = _run(brain)
    assert outcome == "edited"
    assert text == "Alpha rises [@x1]. Beta falls under load [@bowling2018]. Gamma is stable [@x1]."
    assert brain.revise_calls == 2
    assert brain.audit_calls == 1


def test_dropped_citekey_guard():
    """Rewriting sentence 2 without its citekey unverifies the claim it carried."""
    brain = _FakeBrain(
        revise_outputs=['{"2": "Beta falls under load."}',
                        '{"2": "Beta falls under load [@bowling2018]."}'],
        audit_outputs=["OK"])
    text, outcome = _run(brain)
    assert outcome == "edited"
    assert "[@bowling2018]" in text
    assert brain.revise_calls == 2


def test_uncited_guard():
    brain = _FakeBrain(
        revise_outputs=['{"1": null, "2": null, "3": "No citation at all."}',
                        '{"2": "Now grounded [@bowling2018]."}'],
        audit_outputs=["OK"])
    text, outcome = _run(brain)
    assert outcome == "edited"
    assert brain.revise_calls == 2


# ── equations: an atom rabbitHole must carry but never author ────────────────

MATH_PARA = "Alpha rises [@x1]. Correlation was ⟦m:1⟧ across conditions [@bowling2018]."


def test_dropped_equation_guard():
    brain = _FakeBrain(
        revise_outputs=['{"2": "Correlation was strong across conditions [@bowling2018]."}',
                        '{"2": "The correlation ⟦m:1⟧ held across conditions [@bowling2018]."}'],
        audit_outputs=["OK"])
    text, outcome = _run(brain, paragraph=MATH_PARA, anchored={1})
    assert outcome == "edited"
    assert "⟦m:1⟧" in text
    assert brain.revise_calls == 2


def test_invented_equation_guard():
    brain = _FakeBrain(
        revise_outputs=['{"2": "Correlation ⟦m:1⟧ and ⟦m:7⟧ held [@bowling2018]."}',
                        '{"2": "Correlation ⟦m:1⟧ held [@bowling2018]."}'],
        audit_outputs=["OK"])
    text, outcome = _run(brain, paragraph=MATH_PARA, anchored={1})
    assert outcome == "edited"
    assert "⟦m:7⟧" not in text
    assert brain.revise_calls == 2


# ── minimality: computed, not judged ─────────────────────────────────────────

def test_minimal_edit_guard_rejects_collateral_rewriting():
    """The comment anchors to sentence 2. Rewriting 1 and 3 discards grounding nobody asked
    to change — the defect that turned every redline into a whole-paragraph replacement."""
    brain = _FakeBrain(
        revise_outputs=['{"1": "Alpha soars [@x1].", "2": "Beta falls [@bowling2018].", '
                        '"3": "Gamma holds [@x1]."}',
                        '{"2": "Beta falls by 42% [@bowling2018]."}'],
        audit_outputs=["OK"])
    text, outcome = _run(brain)
    assert outcome == "edited"
    assert text.startswith("Alpha rises [@x1].")     # sentence 1 restored verbatim
    assert text.endswith("Gamma is stable [@x1].")   # sentence 3 restored verbatim
    assert brain.revise_calls == 2
    assert brain.audit_calls == 1


def test_minimal_edit_guard_inactive_when_whole_paragraph_anchored():
    """A reviewer who selected the whole paragraph licenses rewriting all of it; flagging
    that would be a false positive."""
    brain = _FakeBrain(
        revise_outputs=['{"1": "Alpha soars [@x1].", "2": "Beta falls [@bowling2018].", '
                        '"3": "Gamma holds [@x1]."}'],
        audit_outputs=["OK"])
    text, outcome = _run(brain, anchored={0, 1, 2})
    assert outcome == "edited"
    assert brain.revise_calls == 1


# ── the audit's remaining job: meaning, and a reason class ───────────────────

def test_corpus_routing_carries_the_reason_class():
    """A clean rewrite whose audit returns CORPUS must NOT be applied — and the class is what
    lets the reply say "a table isn't a prose edit" instead of "gather more sources"."""
    brain = _FakeBrain(
        revise_outputs=['{"2": "A summary of the data [@bowling2018]."}'],
        audit_outputs=["CORPUS: table: this asks for a table; prose can't satisfy it."])
    text, outcome = _run(brain, comments=["a table is better for this much numerical data"])
    assert outcome == "corpus:table"
    assert text is None


def test_corpus_class_defaults_to_sources():
    brain = _FakeBrain(
        revise_outputs=['{"2": "Beta falls [@bowling2018]."}'],
        audit_outputs=["CORPUS: needs a paper we don't have"])
    _text, outcome = _run(brain)
    assert outcome == "corpus:sources"


def test_clean_first_try():
    brain = _FakeBrain(
        revise_outputs=['{"2": "Beta falls by 42% [@bowling2018]."}'],
        audit_outputs=["OK"])
    _text, outcome = _run(brain)
    assert outcome == "edited"
    assert brain.revise_calls == 1 and brain.audit_calls == 1


# ── fail closed ──────────────────────────────────────────────────────────────

def test_exhausted_rounds_is_skipped_not_a_dirty_edit():
    """Every round drops the citekey. The old code emitted the last attempt anyway as long as
    it had *some* citation, so a source silently left the review while the reply claimed the
    comment was addressed."""
    brain = _FakeBrain(
        revise_outputs=['{"2": "Beta falls [@x1]."}'] * 3,
        audit_outputs=[])
    text, outcome = _run(brain)
    assert outcome == "skipped"
    assert text is None
    assert brain.audit_calls == 0


def test_audit_failure_fails_closed():
    """A guard-clean rewrite proves the text is verifiable, not that it answers the comment.
    Claiming otherwise is the fabricated reply the adversary exists to prevent."""
    brain = _ExplodingAudit(
        revise_outputs=['{"2": "Beta falls by 42% [@bowling2018]."}'],
        audit_outputs=[])
    text, outcome = _run(brain)
    assert outcome == "skipped"
    assert text is None


def test_empty_edit_object_is_skipped():
    """The reviser cannot both change nothing and have addressed the comment."""
    brain = _FakeBrain(revise_outputs=["{}"], audit_outputs=[])
    text, outcome = _run(brain)
    assert outcome == "skipped"
    assert text is None


# ── triage: a source the reviewer NAMES, made citeable before the reviser runs ──

class _RecordingBrain(_FakeBrain):
    """Captures the revise prompts so a test can prove a promoted line reached the reviser."""

    def __init__(self, revise_outputs, audit_outputs):
        super().__init__(revise_outputs, audit_outputs)
        self.revise_prompts: list[str] = []

    def coordinator(self, prompt, sys_prompt, **kw):
        if "audit" not in sys_prompt.lower():
            self.revise_prompts.append(prompt)
        return super().coordinator(prompt, sys_prompt, **kw)


def _run_named(brain, comments, source_lines=None, paragraph=PARA, anchored=ANCHOR):
    return revise._redline_para_adversary(
        brain, _FakeCfg(), paragraph, list(comments), DIGEST,
        anchored=set(anchored), source_lines=source_lines or {})


def test_named_citekeys_reads_bare_and_bracketed_forms():
    # bare is what reviewers actually write; bracketed is the formal form; both are caught
    assert revise._named_citekeys(["add info from @doblingerGovernments2019 - it's in Zotero"]) \
        == {"doblingerGovernments2019"}
    assert revise._named_citekeys(["see [@smith2021] here"]) == {"smith2021"}
    # a plain @-mention or an address is not a citekey (no year) — not routed as a source
    assert revise._named_citekeys(["ping @dave about this", "mail a@b.com"]) == set()


def test_a_named_source_in_the_corpus_is_promoted_and_reaches_the_reviser():
    """The source is in the corpus but not in this paragraph's (budget-capped) digest. Naming
    it must surface it so the reviser can cite it — not route it away as missing."""
    promoted = {"newsource2021": "- [@newsource2021] a promoted evidence line"}
    brain = _RecordingBrain(
        revise_outputs=['{"2": "Beta rises [@bowling2018], echoed by [@newsource2021]."}'],
        audit_outputs=["OK"])
    text, outcome = _run_named(brain, ["tie this to @newsource2021"], source_lines=promoted)
    assert outcome == "edited"
    assert "[@newsource2021] a promoted evidence line" in brain.revise_prompts[0]


def test_a_named_source_in_neither_digest_nor_corpus_routes_as_missing():
    """The whole point of triage: classify BEFORE attempting. A source that cannot be made
    citeable is routed honestly, and the reviser is never even called."""
    brain = _FakeBrain(revise_outputs=[], audit_outputs=[])   # any call would raise
    text, outcome = _run_named(brain, ["bring in @ghostpaper2000"], source_lines={})
    assert outcome == "corpus:sources"
    assert text is None
    assert brain.revise_calls == 0 and brain.audit_calls == 0


def test_a_named_source_already_in_the_digest_is_left_to_the_normal_flow():
    """[@bowling2018] is already citeable here, so triage does not treat it as a pull — the
    comment is a normal prose edit, not a routed missing source."""
    brain = _FakeBrain(
        revise_outputs=['{"2": "Beta falls sharply [@bowling2018]."}'],
        audit_outputs=["OK"])
    text, outcome = _run_named(brain, ["lean harder on @bowling2018"], source_lines={})
    assert outcome == "edited"


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

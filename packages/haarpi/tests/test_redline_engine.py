"""The shared redline engine, exercised through a fake policy and a scripted brain.

The engine owns the loop, the four primals, the triage stage, and the fail-closed contract —
so these tests pin exactly those, with a policy that carries no tool vocabulary. The real
rabbitHole/raconteur guards and prompts are tested in their own suites; here the policy is a
stand-in whose only job is to let the loop run.
"""

from dataclasses import dataclass

from haarpi.redline_engine import (
    Disposition, Evidence, EvidenceLine, ParaContext, RedlinePolicy,
    apply_sentence_edits, number_sentences, parse_sentence_edits,
    redline_paragraph, route_class_of, routed, triage,
)


# ── a Finding stand-in: the engine only ever reads .imperative ────────────────

@dataclass
class Finding:
    imperative: str


class FakeBrain:
    """Replays a scripted list of coordinator replies, in order, and records the prompts.

    Two calls per successful round (revise, then audit); one per round that fails a guard
    (revise only). A script shorter than the calls made raises — a silent StopIteration would
    look like a model returning empty."""

    def __init__(self, replies):
        self._replies = list(replies)
        self.calls = []

    def coordinator(self, user, system, num_ctx=0):
        self.calls.append((system, user))
        if not self._replies:
            raise AssertionError("brain called more times than the script provides")
        r = self._replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class FakePolicy:
    """A minimal policy. `resolvable` names the citekeys `resolve_named_source` can pull in;
    `guard` is a callable returning findings so a test can force a guard failure."""

    author = "tester"
    route_classes = ("sources", "table", "section")

    def __init__(self, known=None, resolvable=None, guard=None):
        self._known = set(known or ())
        self._resolvable = set(resolvable or ())
        self._guard = guard or (lambda *a: [])
        self.resolved = []

    def evidence_for(self, ctx):
        return Evidence(known=set(self._known), context="EVIDENCE")

    def resolve_named_source(self, citekey):
        if citekey in self._resolvable:
            self.resolved.append(citekey)
            return EvidenceLine(citekey=citekey, text=f"[@{citekey}] a pulled source")
        return None

    def revise_system(self):
        return "SYS-REVISE"

    def audit_system(self):
        return "SYS-AUDIT"

    def revise_user(self, ctx, evidence, numbered_sentences, comment_block):
        return f"{numbered_sentences}\n{comment_block}\n{evidence.context}"

    def audit_user(self, ctx, revised, comment_block):
        return f"{revised}\n{comment_block}"

    def guard_findings(self, old_text, new_text, touched, ctx, evidence):
        return self._guard(old_text, new_text, touched, ctx, evidence)


def _ctx(text="One sentence here. And a second one here.", **kw):
    return ParaContext(heading="H", text=text, comments=["do the thing"], **kw)


# ── the policy is honoured as a protocol ──────────────────────────────────────

def test_fake_policy_satisfies_the_protocol():
    assert isinstance(FakePolicy(), RedlinePolicy)


# ── sentence-edit mechanics (pure) ────────────────────────────────────────────

def test_untouched_sentences_are_copied_byte_for_byte():
    units = ["First one. ", "Second one. ", "Third one."]
    out = apply_sentence_edits(units, {"2": "REPLACED."})
    assert out == "First one. REPLACED. Third one."
    assert "First one. " in out and "Third one" in out  # neighbours literally survive


def test_a_null_deletes_a_sentence():
    units = ["Keep. ", "Drop. ", "Keep too."]
    assert apply_sentence_edits(units, {"2": None}) == "Keep. Keep too."


def test_numbering_marks_only_the_anchored_sentence():
    rendered = number_sentences(["Alpha.", "Beta."], anchored={1})
    lines = rendered.splitlines()
    assert lines[0].startswith("  1.") and lines[1].startswith("▶ 2.")


def test_malformed_json_is_an_error_not_an_empty_edit():
    edits, copy, errors = parse_sentence_edits("not json at all", 3)
    assert edits == {} and errors and "JSON object" in errors[0]


def test_an_out_of_range_sentence_number_is_rejected():
    _e, _c, errors = parse_sentence_edits('{"9": "x"}', 3)
    assert errors and "between 1 and 3" in errors[0]


def test_copyedits_on_a_nonexistent_span_are_dropped():
    _e, copy, _err = parse_sentence_edits(
        '{"1": "x", "copyedits": {"a:1": "fix"}}', 2, authored={"⟦a:1⟧": "orig"})
    assert copy == {"⟦a:1⟧": "fix"}
    _e2, copy2, _err2 = parse_sentence_edits(
        '{"1": "x", "copyedits": {"a:7": "fix"}}', 2, authored={"⟦a:1⟧": "orig"})
    assert copy2 == {}   # a:7 is not an authored span → dropped, not argued with


# ── triage: read and classify BEFORE attempting ───────────────────────────────

def test_a_heading_comment_is_routed_without_an_attempt():
    r = triage(_ctx(is_heading=True), Evidence(set(), ""), FakePolicy())
    assert not r.proceed and route_class_of(r.disposition) == "section"


def test_a_named_key_already_citeable_needs_no_pull():
    pol = FakePolicy(known={"smith2020"})
    r = triage(_ctx(named_keys={"smith2020"}), pol.evidence_for(_ctx()), pol)
    assert r.proceed and r.extra_evidence == [] and pol.resolved == []


def test_a_named_key_not_citeable_is_pulled_in():
    pol = FakePolicy(resolvable={"doblinger2019"})
    r = triage(_ctx(named_keys={"doblinger2019"}), Evidence(set(), ""), pol)
    assert r.proceed and pol.resolved == ["doblinger2019"]
    assert r.extra_evidence[0].citekey == "doblinger2019"


def test_a_named_key_that_cannot_be_pulled_is_routed_as_missing_source():
    pol = FakePolicy()   # resolves nothing
    r = triage(_ctx(named_keys={"ghost2000"}), Evidence(set(), ""), pol)
    assert not r.proceed and route_class_of(r.disposition) == "sources"


# ── the full loop ─────────────────────────────────────────────────────────────

def test_a_clean_edit_returns_edited():
    brain = FakeBrain(['{"1": "A rewritten first sentence."}', "OK"])
    new, disp, _ = redline_paragraph(brain, _ctx(anchored={0}), FakePolicy())
    assert disp == Disposition.EDITED.value
    assert "rewritten first sentence" in new


def test_the_pulled_source_reaches_the_revise_prompt():
    brain = FakeBrain(['{"1": "Now cites [@doblinger2019]."}', "OK"])
    pol = FakePolicy(resolvable={"doblinger2019"})
    new, disp, _ = redline_paragraph(
        brain, _ctx(anchored={0}, named_keys={"doblinger2019"}), pol)
    assert disp == Disposition.EDITED.value
    # the revise prompt (first call's user) carried the pulled evidence line
    revise_user = brain.calls[0][1]
    assert "a pulled source" in revise_user


def test_an_empty_edit_object_is_a_skip_not_a_silent_success():
    brain = FakeBrain(["{}"])
    new, disp, _ = redline_paragraph(brain, _ctx(), FakePolicy())
    assert new is None and disp == Disposition.SKIPPED.value


def test_a_guard_failure_feeds_a_refined_reround_then_succeeds():
    calls = {"n": 0}

    def guard(old, new, touched, ctx, ev):
        calls["n"] += 1
        return [Finding("restore the citation")] if calls["n"] == 1 else []

    brain = FakeBrain(['{"1": "first try"}', '{"1": "second try [@smith2020]"}', "OK"])
    new, disp, _ = redline_paragraph(
        brain, _ctx(anchored={0}), FakePolicy(guard=guard))
    assert disp == Disposition.EDITED.value and "second try" in new
    # the critique from the guard was appended to the second revise prompt
    assert "restore the citation" in brain.calls[1][1]


def test_persistent_guard_failure_exhausts_rounds_and_fails_closed():
    brain = FakeBrain(['{"1": "x"}', '{"1": "y"}'])   # two rounds, both fail the guard
    pol = FakePolicy(guard=lambda *a: [Finding("still broken")])
    new, disp, _ = redline_paragraph(brain, _ctx(anchored={0}), pol, rounds=2)
    assert new is None and disp == Disposition.SKIPPED.value


def test_the_audit_can_route_a_comment_a_prose_edit_cannot_satisfy():
    brain = FakeBrain(['{"1": "an attempt"}', "ROUTE: table: needs a table"])
    new, disp, _ = redline_paragraph(brain, _ctx(anchored={0}), FakePolicy())
    assert new is None and route_class_of(disp) == "table"


def test_an_unknown_route_class_falls_back_to_the_first_declared():
    brain = FakeBrain(['{"1": "an attempt"}', "ROUTE: nonsense: ?"])
    new, disp, _ = redline_paragraph(brain, _ctx(anchored={0}), FakePolicy())
    assert route_class_of(disp) == "sources"   # route_classes[0]


def test_a_brain_exception_fails_closed():
    brain = FakeBrain([RuntimeError("model down")])
    new, disp, _ = redline_paragraph(brain, _ctx(), FakePolicy())
    assert new is None and disp == Disposition.SKIPPED.value


def test_an_empty_paragraph_is_skipped():
    new, disp, _ = redline_paragraph(FakeBrain([]), _ctx(text="   "), FakePolicy())
    assert new is None and disp == Disposition.SKIPPED.value


def test_copyedits_travel_out_even_when_the_comment_is_skipped():
    # the reviser proposes a copyedit on the author's span, then changes no sentence
    brain = FakeBrain(['{"copyedits": {"a:1": "their fixed sentence"}}'])
    new, disp, copy = redline_paragraph(
        brain, _ctx(anchored={0}, authored={"⟦a:1⟧": "their sentence"}), FakePolicy())
    assert disp == Disposition.SKIPPED.value
    assert copy == {"⟦a:1⟧": "their fixed sentence"}

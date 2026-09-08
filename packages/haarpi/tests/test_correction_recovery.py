"""Two fusions the planner still had, both found by running it for real.

`haarpi next` on elephantRoom's markup reported "1×section, 1×correct" for four comments:
three separate "add a section on X" requests collapsed into ONE task, and a correction whose
wrong term and right term were the same string.

The section fusion: `_promote_explicit_sections` split section asks out of OTHER task types but
passed a task already typed `section` straight through — so when the model classified all three
correctly and grouped them, they stayed grouped, and two of the three requests were answered by
whatever the first one produced.

The correction: a reviewer states the CORRECT name and nothing else — the wrong one is on the
page in front of them. Asked for a pair, the model fills both fields with the correct term; the
substitution runs, matches itself, reports a hit, and the wrong name survives another cycle
under a plan record saying a correction was applied. That is how "Dosi-Stiglitz-Keynes" — a
model name that does not exist — outlived five cycles of being flagged.

Runnable two ways:
    pytest tests/test_correction_recovery.py
    python tests/test_correction_recovery.py
"""

from __future__ import annotations

import json

from haarpi import planner


# ── section asks stay separate however they were grouped ─────────────────────

_ASKS = ["I'd like to add an entire section on household distributional equity.",
         "We can also then introduce a section on consumption smoothing.",
         "I'd also like to add a section on supply chain dependency reduction."]


def test_a_correctly_grouped_section_task_is_still_split_per_comment():
    """The model got the classification right and the planner punished it for it."""
    tasks = planner._normalise_tasks(
        [{"comments": [1, 2, 3], "need": "section", "query": "several things"}], _ASKS)
    assert planner._tasks_assessment(tasks) == "3×section"
    assert all(len(t["comments"]) == 1 for t in tasks)


def test_each_split_section_carries_its_own_comment_as_its_query():
    """The query is what steers the gather and the section plan; a shared one aims at neither."""
    tasks = planner._normalise_tasks(
        [{"comments": [1, 2, 3], "need": "section", "query": "several things"}], _ASKS)
    assert sorted(t["query"] for t in tasks) == sorted(a.strip() for a in _ASKS)


def test_a_non_section_comment_grouped_under_section_stays_together():
    """Only the unambiguous section asks stand alone; the rest keep the task the model made."""
    texts = _ASKS[:1] + ["please tighten the prose in this paragraph"]
    tasks = planner._normalise_tasks(
        [{"comments": [1, 2], "need": "section", "query": "q"}], texts)
    needs = sorted((t["need"], len(t["comments"])) for t in tasks)
    assert needs == [("section", 1), ("section", 1)] or needs == [("section", 1), ("section", 1)]


# ── a correction that names only the right term ──────────────────────────────

_DSK = "Dystopian Schumpeter-meeting-Keynes (DSK) model"
_NOTE = f"Do you mean the {_DSK}? better get the name correct."


def test_a_self_substitution_is_not_accepted_as_a_correction():
    """wrong == right runs, matches itself, and reports success while changing nothing."""
    tasks = planner._normalise_tasks(
        [{"comments": [1], "need": "correct", "wrong": _DSK, "right": _DSK}], [_NOTE])
    t = tasks[0]
    assert t["need"] == "correct"
    assert t["wrong"] == "", "held open for recovery against the draft, not guessed"
    assert t["right"] == _DSK


def test_a_correction_with_no_right_term_degrades_to_an_edit():
    tasks = planner._normalise_tasks(
        [{"comments": [1], "need": "correct", "wrong": "", "right": ""}], [_NOTE])
    assert tasks[0]["need"] == "edit"


def test_same_term_ignores_case_and_punctuation():
    assert planner._same_term("Dosi-Stiglitz-Keynes", "dosi stiglitz keynes") is True
    assert planner._same_term("Dosi-Stiglitz-Keynes", "Dystopian Schumpeter") is False
    assert planner._same_term("", "") is False, "two blanks are not a matching pair"


# ── recovering the wrong term from the draft ─────────────────────────────────

class _Stub:
    def __init__(self, *a, **kw): pass

    def coordinator(self, prompt, sys, **kw):
        for line in prompt.splitlines():
            if line.strip().startswith("- ") and "Dosi" in line:
                return json.dumps({"wrong": line.strip()[2:]})
        return json.dumps({"wrong": "NONE"})


def _docx(tmp_path, paras):
    from docx import Document
    d = Document()
    for t in paras:
        d.add_paragraph(t)
    fp = tmp_path / "markup.docx"
    d.save(str(fp))
    return fp


def test_the_wrong_term_is_found_in_the_draft(tmp_path, monkeypatch):
    import haarpi.brain
    monkeypatch.setattr(haarpi.brain, "Brain", _Stub)
    fp = _docx(tmp_path, ["Models grounded in the Dosi-Stiglitz-Keynes framework describe "
                          "innovation probabilities.",
                          "The Dosi-Stiglitz-Keynes framework appears again here."])
    assert planner._recover_wrong_term(fp, _DSK, _NOTE, {}) == "Dosi-Stiglitz-Keynes"


def test_a_hyphenated_name_is_not_fragmented(tmp_path, monkeypatch):
    """Two regex branches raced and split the very name this exists to find: after a sentence-
    initial "The", "The Dosi-Stiglitz-Keynes" came back as "The Dosi" plus "Stiglitz-Keynes",
    so the model was choosing among pieces of the answer rather than the answer."""
    import haarpi.brain
    monkeypatch.setattr(haarpi.brain, "Brain", _Stub)
    fp = _docx(tmp_path, ["The Dosi-Stiglitz-Keynes framework is described here.",
                          "The Dosi-Stiglitz-Keynes framework again."])
    assert planner._recover_wrong_term(fp, _DSK, _NOTE, {}) == "Dosi-Stiglitz-Keynes"


def test_a_fragment_of_the_correct_name_is_never_offered(tmp_path, monkeypatch):
    """The live run picked 'The Dystopian Schumpeter' — a prefix of the RIGHT name — which
    would have rewritten correct text into 'Dystopian Schumpeter-meeting-Keynes (DSK)
    model-meeting-Keynes (DSK) model'. Worse than the no-op it replaced. A wrong name has to
    SAY something the right one does not."""
    import haarpi.brain
    captured = {}

    class _Capture(_Stub):
        def coordinator(self, prompt, sys, **kw):
            captured["p"] = prompt
            return json.dumps({"wrong": "NONE"})

    monkeypatch.setattr(haarpi.brain, "Brain", _Capture)
    fp = _docx(tmp_path, ["The Dystopian Schumpeter-meeting-Keynes (DSK) model demonstrates.",
                          "The Dosi-Stiglitz-Keynes framework describes innovation."])
    planner._recover_wrong_term(fp, _DSK, _NOTE, {})
    offered = [l.strip()[2:] for l in captured["p"].splitlines() if l.strip().startswith("- ")]
    assert "Dosi-Stiglitz-Keynes" in offered
    assert not any("Dystopian" in o or "Schumpeter" in o for o in offered), offered


def test_significant_words_ignore_articles_and_generic_nouns():
    assert planner._significant("The Dystopian Schumpeter model") == {"dystopian", "schumpeter"}
    assert planner._significant("Dosi-Stiglitz-Keynes") == {"dosi", "stiglitz", "keynes"}


def test_a_term_the_draft_does_not_use_is_refused(tmp_path, monkeypatch):
    """A hallucinated pair would substitute across the whole document — the one edit with no
    local blast radius — so only a term actually present is accepted."""
    import haarpi.brain

    class _Liar(_Stub):
        def coordinator(self, prompt, sys, **kw):
            return json.dumps({"wrong": "Some Name Not In The Draft"})

    monkeypatch.setattr(haarpi.brain, "Brain", _Liar)
    fp = _docx(tmp_path, ["The Dosi-Stiglitz-Keynes framework is described here."])
    assert planner._recover_wrong_term(fp, _DSK, _NOTE, {}) == ""


def test_the_bibliography_is_not_searched_for_the_wrong_term(tmp_path, monkeypatch):
    """Its claims are passages QUOTED from the sources; a term found only there is the
    source's word, not the review's, and correcting it would falsify the record."""
    import haarpi.brain
    monkeypatch.setattr(haarpi.brain, "Brain", _Stub)
    fp = _docx(tmp_path, ["Clean narrative with no model name in it at all.",
                          "Annotated Bibliography",
                          "Quoted claim mentioning the Dosi-Stiglitz-Keynes framework."])
    assert planner._recover_wrong_term(fp, _DSK, _NOTE, {}) == ""


def test_a_brain_failure_leaves_it_as_an_edit(tmp_path, monkeypatch):
    import haarpi.brain

    class _Broken:
        def __init__(self, *a, **kw): raise RuntimeError("ollama down")

    monkeypatch.setattr(haarpi.brain, "Brain", _Broken)
    fp = _docx(tmp_path, ["The Dosi-Stiglitz-Keynes framework is described here."])
    assert planner._recover_wrong_term(fp, _DSK, _NOTE, {}) == ""


if __name__ == "__main__":
    import tempfile, traceback
    from pathlib import Path

    class _MP:
        def setattr(self, obj, name, val): setattr(obj, name, val)

    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failures = 0
    for fn in fns:
        try:
            n = fn.__code__.co_argcount
            with tempfile.TemporaryDirectory() as td:
                fn(*([Path(td), _MP()][:n]))
            print(f"  PASS  {fn.__name__}")
        except Exception:  # noqa: BLE001
            failures += 1
            print(f"  FAIL  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failures}/{len(fns)} passed")
    raise SystemExit(1 if failures else 0)


# ── the dry run has to show what it decided ──────────────────────────────────

def test_the_decision_lines_are_built_before_the_dry_run_return():
    """A dry run exists to be READ before committing a multi-day cycle, so the topics it will
    search, the sections it will write and the term it will substitute must print. They were
    appended after the early return, so `--dry-run` gave the tier and the chain and stopped —
    silent about every choice an author would want to stop it on.
    """
    import inspect
    src = inspect.getsource(planner.run_next)
    ret = src.index("if dry_run:\n        print")
    before = src[:ret]
    for line in ("section to draft:", "gather topics:", "correction:"):
        assert line in before, f"{line!r} is not printed by --dry-run"
    # Side effects still report only once the work is actually done.
    after = src[ret:]
    assert "correction applied:" in after and "steering config:" in after

"""A redline cannot add a section — so the chain must end in `report`, not `revise`.

Every tier's pipeline used to end in `revise`, the in-place redline: it rewrites the paragraph
a comment anchors to. A comment like "I want a section on X" was classified correctly as
gap_fill, gathered the right sources, and then handed them to a step structurally incapable of
using them. `report` appeared in no chain at all.

The audit's `CORPUS: section` verdict is the signal that swaps the re-draft step, and the
project focus is the only channel carrying the reviewer's ask into `report` — which re-plans
from the corpus and never reads the annotated docx.

Runnable two ways:
    pytest tests/test_plan_chain.py
    python tests/test_plan_chain.py
"""

from __future__ import annotations

from rabbithole import plan, revise


# ── chain selection ──────────────────────────────────────────────────────────

def test_cosmetic_stays_a_redline():
    assert plan._chain_for("cosmetic", {}) == ["revise", "comment"]


def test_gap_fill_redrafts_with_revise_by_default():
    assert plan._chain_for("gap_fill", {}) == ["gather", "collect", "revise", "comment"]


def test_a_section_request_swaps_revise_for_report():
    """The corpus may already hold the evidence; what a redline cannot do is restructure."""
    assert plan._chain_for("cosmetic", {}, needs_report=True) == ["report", "comment"]


def test_a_section_request_survives_a_gather():
    assert plan._chain_for("gap_fill", {}, needs_report=True) == [
        "gather", "collect", "report", "comment"]


def test_a_section_request_survives_a_redirection():
    assert plan._chain_for("redirection", {}, needs_report=True) == [
        "gather", "collect", "report", "comment"]


def test_pasted_references_still_get_ingest_and_collect_before_report():
    """`collect` must precede the re-draft whatever the re-draft is — the human uploads PDFs."""
    steps = plan._chain_for("cosmetic", {"added_references": True}, needs_report=True)
    assert steps == ["ingest", "collect", "report", "comment"]
    assert steps.index("collect") < steps.index("report")


def test_pasted_references_without_a_section_still_end_in_revise():
    steps = plan._chain_for("cosmetic", {"added_references": True})
    assert steps == ["ingest", "collect", "revise", "comment"]


def test_report_is_a_known_step_with_a_command():
    assert plan._STEP["report"]["verb"] == "report"
    assert plan._STEP["report"]["human"] is False
    # report never queues, so unlike revise it needs no --no-queue guard.
    # Commands are queued in umbrella form: on the shared runner box the old
    # standalone stack owns the bare names (oddjob coexistence).
    assert plan._build_command("report") == "haarpi rabbithole report"
    assert plan._build_command("revise") == "haarpi rabbithole revise --no-queue"


# ── the reviewer's ask reaches `report` through the focus line ───────────────

def test_section_focus_uses_the_reviewers_own_words():
    out = plan.section_focus(["I want to add a section discussing Beethoven's 5th"])
    assert "Beethoven's 5th" in out
    assert out.startswith("Develop a dedicated section addressing")


def test_section_focus_permits_the_planner_to_say_no():
    """A section the evidence cannot ground must not be forced — the guards would reject it."""
    assert "why the evidence does not support one" in plan.section_focus(["add a section"])


def test_section_focus_collapses_whitespace_and_truncates():
    out = plan.section_focus(["a" * 500, "  spaced\n\nout  "])
    assert "a" * 240 in out and "a" * 241 not in out
    assert "spaced out" in out


def test_section_focus_is_empty_when_nothing_was_asked():
    assert plan.section_focus([]) == ""
    assert plan.section_focus(["", "   "]) == ""


def test_append_focus_chains_onto_an_existing_line():
    class _Cfg:
        focus = "existing focus"
    cfg = _Cfg()
    plan._append_focus(cfg, "", "added")
    assert cfg.focus == "existing focus; added"


def test_append_focus_handles_an_empty_focus():
    class _Cfg:
        focus = ""
    cfg = _Cfg()
    plan._append_focus(cfg, "only")
    assert cfg.focus == "only"


# ── only section comments trigger the swap ───────────────────────────────────

CMAP = {
    "1": {"text": "add a section on rhythm", "author": "DCR"},
    "2": {"text": "a table is better here", "author": "DCR"},
    "3": {"text": "include the Huron paper", "author": "DCR"},
    "4": {"text": "reword this", "author": "DCR"},
}


def test_only_corpus_section_outcomes_become_section_asks():
    outcomes = {"1": "corpus:section", "2": "corpus:table",
                "3": "corpus:sources", "4": "edited"}
    assert revise._section_comments(outcomes, CMAP) == ["add a section on rhythm"]


def test_a_comment_with_no_body_is_not_an_ask():
    assert revise._section_comments({"9": "corpus:section"}, CMAP) == []
    assert revise._section_comments({"1": "corpus:section"}, {"1": {"text": ""}}) == []


def test_no_section_comments_means_no_report_step():
    outcomes = {"2": "corpus:table", "3": "corpus:sources", "4": "edited"}
    asks = revise._section_comments(outcomes, CMAP)
    assert asks == []
    assert plan._chain_for("gap_fill", {}, needs_report=bool(asks)) == [
        "gather", "collect", "revise", "comment"]


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

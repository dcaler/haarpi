"""razzle.compose — the LLM authors a deck spec from the narrative + figures + claims.

Pins the parse → validate → normalise path (valid roles, a leading title slide, figure refs that
exist), the garbage fallback, and venue sizing. Fake brain — no model in the test.
"""

from __future__ import annotations

import json

from razzle import compose, formats


class _Brain:
    def __init__(self, reply):
        self._r = reply

    def coordinator(self, prompt, system="", **kw):
        return self._r


def test_compose_normalises_a_deck_spec():
    reply = json.dumps({"slides": [
        {"role": "title", "title": "A talk", "subtitle": "the spine"},
        {"role": "figure", "title": "Pipeline", "figure": "ladder", "citation": "[Ref 1]", "notes": "n"},
        {"role": "content", "title": "Points", "bullets": ["a", "b"]},
        {"role": "figure", "title": "Missing", "figure": "nope"}]})   # figure we don't have
    slides = compose.compose(_Brain(reply), "narrative here",
                             [{"id": "ladder", "caption": "the ladder"}])
    assert slides[0]["role"] == "title"
    assert slides[1]["role"] == "figure" and slides[1]["figure"] == "ladder"
    assert slides[1]["citation"] == "[Ref 1]" and "caption" not in slides[1]   # citations-only slot
    assert slides[2]["body"] == ["a", "b"]
    assert slides[3]["role"] == "content" and "figure" not in slides[3]   # missing → demoted
    assert all("notes" not in s for s in slides)          # a deck carries no speaker notes


def test_notes_never_survive_normalisation():
    """The model may still emit `notes` out of habit. They must not reach the spec — the whole
    point of removing them is that the essay has nowhere to go but the speaker's mouth."""
    reply = json.dumps({"slides": [
        {"role": "content", "title": "T", "bullets": ["a"], "notes": "a paragraph of prose"}]})
    slides = compose.compose(_Brain(reply), "n", [])
    assert all("notes" not in s for s in slides)


def test_bullets_are_capped_but_never_truncated():
    """The COUNT is enforced here; the WORDING is the prompt's job. Dropping a fourth bullet loses
    a point the author can restore — truncating a bullet mid-phrase makes it mean something else."""
    long_bullet = "a bullet written far past any sensible budget for a projected slide"
    reply = json.dumps({"slides": [
        {"role": "content", "title": "T", "bullets": ["a", "b", "c", "d", "e"]},
        {"role": "content", "title": "U", "bullets": [long_bullet]}]})
    slides = compose.compose(_Brain(reply), "n", [])
    assert slides[1]["body"] == ["a", "b", "c"][:compose.MAX_BULLETS]
    assert len(slides[1]["body"]) == compose.MAX_BULLETS
    assert slides[2]["body"] == [long_bullet]             # left intact, not mangled


def test_split_carries_both_bullets_and_a_figure():
    """`split` is the answer to a deck that is all text: a point beside its evidence."""
    reply = json.dumps({"slides": [
        {"role": "split", "title": "Point", "bullets": ["a", "b"], "figure": "ladder"},
        {"role": "split", "title": "No figure", "bullets": ["a"], "figure": "nope"}]})
    slides = compose.compose(_Brain(reply), "n", [{"id": "ladder", "caption": "c"}])
    assert slides[1]["role"] == "split" and slides[1]["figure"] == "ladder"
    assert slides[1]["body"] == ["a", "b"]
    # a split whose figure does not exist keeps its bullets and degrades to a plain slide
    assert slides[2]["role"] == "content" and "figure" not in slides[2]
    assert slides[2]["body"] == ["a"]


def test_the_prompt_states_the_budgets_and_bans_notes():
    """The budgets only bite if they reach the model. Pin that they are IN the prompt, and that the
    prompt does not still ask for the speaker notes the renderer now drops."""
    seen = {}

    class _Spy:
        def coordinator(self, prompt, system="", **kw):
            seen["p"], seen["s"] = prompt, system
            return "{}"

    compose.compose(_Spy(), "n", [])
    assert str(compose.MAX_BULLETS) in seen["p"]
    assert str(compose.MAX_BULLET_WORDS) in seen["p"]
    assert str(compose.MAX_TITLE_WORDS) in seen["p"]
    assert "NO speaker notes" in seen["p"]
    assert "detail goes in" not in seen["p"]              # the old instruction, reversed


def test_compose_always_leads_with_a_title_slide():
    reply = json.dumps({"slides": [{"role": "content", "title": "First"}]})
    slides = compose.compose(_Brain(reply), "n", [])
    assert slides[0]["role"] == "title"


def test_compose_falls_back_on_garbage():
    slides = compose.compose(_Brain("I can't do that"), "n", [])
    assert len(slides) == 1 and slides[0]["role"] == "title"


def test_compose_respects_max_slides_override():
    reply = json.dumps({"slides": [{"role": "content", "title": str(i)} for i in range(30)]})
    slides = compose.compose(_Brain(reply), "n", [], max_slides=5)
    assert len(slides) == 5 and slides[0]["role"] == "title"


def test_format_slide_budget_at_one_slide_per_minute():
    assert formats.slide_budget("longtalk") == 18
    assert formats.slide_budget("shorttalk") == 11
    assert formats.slide_budget("lecture") == 45
    assert formats.slide_budget("poster") is None      # not a timed talk


def test_compose_sizes_the_deck_to_the_format():
    reply = json.dumps({"slides": [{"role": "content", "title": str(i)} for i in range(60)]})
    assert len(compose.compose(_Brain(reply), "n", [], fmt="shorttalk")) == 11
    assert len(compose.compose(_Brain(reply), "n", [], fmt="longtalk")) == 18
    assert len(compose.compose(_Brain(reply), "n", [], fmt="lecture")) == 45


def test_normalise_accepts_a_hand_authored_spec_that_uses_body():
    """`razzle render` normalises a spec a SESSION wrote, and those use `body` — the key the
    renderer reads. Reading only `bullets` would have stripped every bullet out of the deck."""
    slides = compose.normalise([{"role": "content", "title": "T",
                                 "body": ["a", "b", "c", "d"], "notes": "an essay"}], set())
    assert slides[1]["body"] == ["a", "b", "c"][:compose.MAX_BULLETS]
    assert "notes" not in slides[1]

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
        {"role": "figure", "title": "Pipeline", "figure": "ladder", "caption": "c", "notes": "n"},
        {"role": "content", "title": "Points", "bullets": ["a", "b"]},
        {"role": "figure", "title": "Missing", "figure": "nope"}]})   # figure we don't have
    slides = compose.compose(_Brain(reply), "narrative here",
                             [{"id": "ladder", "caption": "the ladder"}])
    assert slides[0]["role"] == "title"
    assert slides[1]["role"] == "figure" and slides[1]["figure"] == "ladder"
    assert slides[2]["body"] == ["a", "b"]
    assert slides[3]["role"] == "content" and "figure" not in slides[3]   # missing → demoted


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

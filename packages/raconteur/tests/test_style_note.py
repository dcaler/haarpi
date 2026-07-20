"""The trained voice is a mean; a given paper is rarely written at it.

The css2026 draft was styled, faithfully, into the author's energy-policy register — the
profile is measured across his whole corpus and every excerpt in it is a policy paper — for
a manuscript whose own brief asked for "rigorous but playful" and warned against
"gatekeeping language". Nothing in the pipeline could say so: one global profile, no
per-project override, and the project config field that looked like the lever
(`style_paper_keys`) gates nothing at all.

Also here: a profile written by an older version loads, reaches the prompt, and looks
healthy while being silently degraded — and the staleness check compares paper keys, which
a format change does not touch.
"""

from __future__ import annotations

import inspect

from raconteur import paper, style
from raconteur.config import ProjectConfig


def test_the_note_reaches_the_prompt_and_wins_ties():
    block = paper._style_block("MEASURED VOICE", "playful, not stuffy")
    assert "MEASURED VOICE" in block
    assert "playful, not stuffy" in block
    assert block.index("MEASURED VOICE") < block.index("playful, not stuffy"), \
        "the note must come last — the profile is long enough to drown it"
    assert "this wins" in block


def test_a_note_alone_is_enough_to_produce_a_block():
    """A project can want a register without a trained profile existing at all."""
    assert "playful" in paper._style_block("", "playful")


def test_no_profile_and_no_note_is_no_block():
    assert paper._style_block("", "") == ""


def test_both_drafting_paths_pass_the_note():
    for fn in (paper._draft_paper, paper._revise_paper):
        assert "_style_block(style_profile, cfg.style_note)" in inspect.getsource(fn), \
            fn.__name__


def test_the_note_survives_a_config_round_trip(tmp_path):
    (tmp_path / "paper").mkdir()
    cfg = ProjectConfig(short_title="X", title="X")
    cfg.style_note = "rigorous but playful"
    cfg.save(tmp_path)
    assert ProjectConfig.load(tmp_path).style_note == "rigorous but playful"


# ── an old-format profile must be detectable ─────────────────────────────────

def test_a_profile_without_a_signature_is_not_current(monkeypatch, tmp_path):
    """No signature means style_block bails, load_style_profile dumps the raw body, and
    load_style_signature returns {} — so no style guard can fire. It still looks fine."""
    p = tmp_path / "style_profile.md"
    p.write_text("---\nauthor: X\npaper_keys: [A]\n---\n\n## Style Profile\n\nprose\n")
    monkeypatch.setattr(style, "STYLE_PROFILE_PATH", p)
    assert not style.profile_is_current({"author": "X", "paper_keys": ["A"]})


def test_a_profile_without_tagged_exemplars_is_not_current(monkeypatch, tmp_path):
    p = tmp_path / "style_profile.md"
    p.write_text("---\nauthor: X\n---\n\n## Representative Excerpts\n\n1. prose\n")
    monkeypatch.setattr(style, "STYLE_PROFILE_PATH", p)
    assert not style.profile_is_current({"signature": {"corpus_words": 10}})


def test_a_current_profile_is_current(monkeypatch, tmp_path):
    p = tmp_path / "style_profile.md"
    p.write_text("---\nauthor: X\n---\n\n## Voice — exemplars\n\n> some real prose here\n")
    monkeypatch.setattr(style, "STYLE_PROFILE_PATH", p)
    assert style.profile_is_current({"signature": {"corpus_words": 10}})


def test_an_unchanged_key_set_no_longer_blocks_a_format_retrain():
    """The staleness check compares PAPER KEYS. A format change leaves them identical, so
    the one profile that most needs retraining reported itself up to date."""
    src = inspect.getsource(style.run)
    assert "and current_format" in src, \
        "the up-to-date early return must also require a current format"


def test_the_profiles_own_keys_are_a_valid_training_source():
    """style_paper_keys is empty in every project config that predates it; falling to the
    interactive Zotero search there is what made a trained profile look absent."""
    src = inspect.getsource(style.run)
    assert "cfg.style_paper_keys or sorted(existing_keys)" in src

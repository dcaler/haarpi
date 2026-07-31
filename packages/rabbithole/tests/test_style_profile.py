"""rabbitHole's binding to the shared style engine — and the format upgrade it brings.

The retrain-forever regressions (a PDF-less paper, a key that resolves to no Zotero item) are
now covered exhaustively in haarpi/tests/test_style_engine.py, against the one engine both tools
share. What matters HERE is that rabbitHole is wired to that engine: its `needs_training` is the
engine's, it reads and writes the neutral profile path, and — the upgrade — an old analysis-only
profile (no measured signature, the kind rabbitHole used to write) now correctly reports that it
needs retraining, so rabbitHole's synthesis moves onto the measured voice.
"""

from __future__ import annotations

import pytest

from haarpi import style as hstyle
from rabbithole import style


@pytest.fixture(autouse=True)
def _neutral(tmp_path, monkeypatch):
    """Point the engine's path globals at tmp; disable the legacy fallback so nothing leaks."""
    monkeypatch.setattr(hstyle, "STYLE_PROFILE_PATH", tmp_path / "style_profile.md")
    monkeypatch.setattr(hstyle, "LEGACY_PROFILE_PATHS", ())
    return tmp_path


def _write(keys, *, signature=True, used=(), skipped=()):
    """Write a profile. With a signature it is the current MEASURED format; without one it is the
    old analysis-only shape rabbitHole used to produce."""
    hstyle.write_profile(
        "A. Author", list(keys), list(used), list(skipped),
        "Writes in short declarative sentences.",
        signature={"corpus_words": 3000} if signature else None,
        exemplars=["A passage of the author's own prose."] if signature else None)


def test_needs_training_is_the_shared_engine_function():
    assert style.needs_training is hstyle.needs_training


def test_absent_profile_needs_training():
    assert style.needs_training(["A", "B"]) is True


def test_profile_trained_against_every_named_paper_is_left_alone():
    _write(["A", "B", "C"])
    assert style.needs_training(["A", "B"]) is False


def test_a_newly_named_paper_triggers_a_retrain():
    _write(["A", "B"])
    assert style.needs_training(["A", "B", "C"]) is True


def test_a_skipped_paper_does_not_retrain_forever():
    """'B' had no PDF — attempted, skipped, but recorded in paper_keys, so the config's keys are
    a subset and the next run does not retrain."""
    _write(["A", "B"], used=["A"], skipped=["B"])
    assert style.needs_training(["A", "B"]) is False


def test_no_configured_keys_means_an_existing_measured_profile_suffices():
    _write([])
    assert style.needs_training([]) is False
    assert style.needs_training(None) is False


def test_an_analysis_only_profile_now_needs_a_retrain():
    """The upgrade. rabbitHole's old profile carried no measured signature; even with every key
    accounted for, it must retrain onto the measured format the synthesis prompt now expects."""
    _write(["A", "B"], signature=False)
    assert style.needs_training(["A", "B"]) is True


def test_load_style_profile_returns_the_measured_block():
    _write(["A"])
    block = style.load_style_profile()
    assert block, "a trained profile must render a non-empty style block"
    assert "voice" in block.lower() or "style" in block.lower()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

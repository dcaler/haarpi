"""The style profile must not retrain itself forever.

`needs_training` asks whether the config names a paper the profile was never trained against.
`_write_profile` recorded only the papers that yielded usable prose — so a paper with no PDF
attachment was attempted, skipped, and forgotten, making the subset check unsatisfiable. Every
`report` and every `revise` re-fetched the whole Zotero style collection, skipped the same
papers, and rewrote the same profile. Twenty minutes of coordinator time, per run, forever.

Runnable two ways:
    pytest tests/test_style_profile.py
    python tests/test_style_profile.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from rabbithole import style


def _profile(tmp: Path, keys, used=(), skipped=()) -> Path:
    import yaml
    fm = yaml.safe_dump({"author": "A", "last_updated": "260708", "paper_keys": list(keys),
                         "papers_used": list(used), "papers_skipped": list(skipped)},
                        default_flow_style=False).strip()
    p = tmp / "style_profile.md"
    p.write_text(f"---\n{fm}\n---\n\nWrites in short declarative sentences.\n",
                 encoding="utf-8")
    return p


@pytest.fixture
def profile_at(monkeypatch):
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)

        def _set(keys=(), exists=True, **kw):
            path = _profile(tmp, keys, **kw) if exists else tmp / "absent.md"
            monkeypatch.setattr(style, "STYLE_PROFILE_PATH", path)
            return path
        yield _set


def test_absent_profile_needs_training(profile_at):
    profile_at(exists=False)
    assert style.needs_training(["A", "B"]) is True


def test_profile_trained_against_every_named_paper_is_left_alone(profile_at):
    profile_at(keys=["A", "B", "C"])
    assert style.needs_training(["A", "B"]) is False


def test_a_newly_named_paper_triggers_a_retrain(profile_at):
    profile_at(keys=["A", "B"])
    assert style.needs_training(["A", "B", "C"]) is True


def test_a_skipped_paper_does_not_retrain_forever(profile_at):
    """The regression. 'B' has no PDF, so it is attempted and skipped — but it IS recorded in
    paper_keys, so the next run sees the config's keys as a subset and does not retrain."""
    profile_at(keys=["A", "B"], used=["A"], skipped=["B"])
    assert style.needs_training(["A", "B"]) is False


def test_the_old_behaviour_would_have_looped(profile_at):
    """Recording only the papers that produced prose makes the subset check unsatisfiable."""
    profile_at(keys=["A"], used=["A"], skipped=["B"])   # 'B' attempted but not recorded
    assert style.needs_training(["A", "B"]) is True     # -> retrains, skips B, writes ["A"]…


def test_no_configured_keys_means_an_existing_profile_suffices(profile_at):
    profile_at(keys=[])
    assert style.needs_training([]) is False
    assert style.needs_training(None) is False


def test_write_profile_records_attempted_keys_not_just_used(monkeypatch):
    with tempfile.TemporaryDirectory() as t:
        path = Path(t) / "style_profile.md"
        monkeypatch.setattr(style, "STYLE_PROFILE_PATH", path)
        style._write_profile("A. Author", ["K1", "K2", "K3"], ["paper one"],
                             "Terse.", ["paper two", "paper three"])
        meta = style._load_existing_meta()
        assert meta["paper_keys"] == ["K1", "K2", "K3"]     # everything attempted
        assert meta["papers_used"] == ["paper one"]
        assert meta["papers_skipped"] == ["paper two", "paper three"]
        # and the round trip satisfies needs_training
        assert style.needs_training(["K1", "K2", "K3"]) is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

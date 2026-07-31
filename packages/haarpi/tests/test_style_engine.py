"""The shared style engine — the retrain decision, the two bugs it closes, and the loop.

Both fixes that the per-tool copies never shared live here:
  * a paper with a PDF-less-but-present item, and
  * a requested key that resolves to NO Zotero item at all (the pydsk case),
must both count as TRAINED-AGAINST, or `needs_training` is unsatisfiable and the profile
retrains on every run forever.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from haarpi import style


@pytest.fixture(autouse=True)
def _neutral(tmp_path, monkeypatch):
    """Every test writes to tmp, never the real ~/.config/haarpi/style_profile.md."""
    monkeypatch.setattr(style, "STYLE_PROFILE_PATH", tmp_path / "haarpi" / "style_profile.md")
    monkeypatch.setattr(style, "LEGACY_PROFILE_PATHS", (tmp_path / "raconteur" / "style_profile.md",))
    return tmp_path


# A block of real-enough prose: >500 words, paragraph-shaped, with connectives so the
# signature and exemplars come out non-empty.
_SENT = ("The model however suggests that adoption may depend on local context; "
         "for example, households that see a rebate respond more than those that do not. "
         "Moreover, the effect is likely to vary, and thus we find substantial heterogeneity. ")
# Paragraphs land in the 45–110 word exemplar band and start uppercase, so pick_exemplars
# returns real passages; twelve of them clears the >500-word floor comfortably.
_GOOD_PROSE = "\n\n".join(_SENT * 2 for _ in range(12))


class FakePolicy:
    def __init__(self, author="A. Author", requested_keys=None, prose=None,
                 items=None, selected=None):
        self.author = author
        self.requested_keys = requested_keys or []
        self._prose = prose or {}          # key -> prose (missing/short key => skipped)
        self._items = items or []          # for search_author
        self._selected = selected          # for select_items
        self.logs: list[str] = []
        self.saved_keys: list[str] | None = None

    def items_by_keys(self, keys):
        # models Zotero: only the keys we know about come back
        return [{"data": {"key": k, "title": f"Paper {k}",
                          "creators": [{"creatorType": "author", "lastName": "Author"}]}}
                for k in keys if k in self._prose]

    def search_author(self, name):
        return self._items

    def select_items(self, items):
        return self._selected if self._selected is not None else items

    def prose_for(self, item, tmpdir):
        return self._prose.get(item.get("data", {}).get("key", ""), "")

    def analyze(self, author, exemplars):
        return "The author writes in measured, hedged sentences."

    def save_keys(self, keys):
        self.saved_keys = list(keys)

    def log(self, msg):
        self.logs.append(msg)


def test_absent_profile_needs_training():
    assert style.needs_training(["K1"]) is True


def test_a_requested_key_never_trained_against_needs_training():
    style.write_profile("A", ["K1"], ["Paper K1"], [], "an analysis",
                        signature={"corpus_words": 3000}, exemplars=["A passage."])
    assert style.needs_training(["K1", "K2"]) is True   # K2 was never trained against


def test_subset_of_trained_keys_does_not_need_training():
    style.write_profile("A", ["K1", "K2"], ["Paper K1"], [], "an analysis",
                        signature={"corpus_words": 3000}, exemplars=["A passage."])
    assert style.needs_training(["K1"]) is False
    assert style.needs_training(["K1", "K2"]) is False


def test_unresolved_keys_are_recorded_and_stop_the_loop():
    """The pydsk case: 3 keys requested, only 1 resolves to a Zotero item. The engine must
    record all 3 as trained-against so needs_training is satisfiable on the next run."""
    policy = FakePolicy(requested_keys=["K1", "DEAD2", "DEAD3"],
                        prose={"K1": _GOOD_PROSE})     # DEAD2/DEAD3 resolve to nothing
    rc = style.run(policy)
    assert rc == 0
    meta = style.load_meta()
    assert set(meta["paper_keys"]) == {"K1", "DEAD2", "DEAD3"}
    # the whole point: the very next run sees nothing new to train
    assert style.needs_training(["K1", "DEAD2", "DEAD3"]) is False


def test_pdfless_present_key_is_still_trained_against():
    """A key that resolves to an item but yields no usable prose is skipped, yet still counts
    as trained-against (the 07-08 fix), so it does not re-trigger training either."""
    policy = FakePolicy(requested_keys=["K1", "NOPDF"],
                        prose={"K1": _GOOD_PROSE, "NOPDF": "too short"})
    assert style.run(policy) == 0
    meta = style.load_meta()
    assert set(meta["paper_keys"]) == {"K1", "NOPDF"}
    assert "NOPDF" not in " ".join(meta["papers_used"])
    assert style.needs_training(["K1", "NOPDF"]) is False


def test_analysis_only_profile_is_not_current_format():
    style.write_profile("A", ["K1"], ["Paper K1"], [], "analysis only",
                        signature=None, exemplars=None)      # no signature, no exemplars
    assert style.profile_is_current() is False
    assert style.needs_training(["K1"]) is True              # format triggers a retrain


def test_reads_fall_back_to_legacy_path(monkeypatch, tmp_path):
    legacy = style.LEGACY_PROFILE_PATHS[0]
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("---\nauthor: Legacy\npaper_keys:\n- OLD1\n"
                      "signature:\n  corpus_words: 4000\n---\n\n## Voice — exemplars\n\n> A line.\n",
                      encoding="utf-8")
    assert style.STYLE_PROFILE_PATH.exists() is False
    assert style.load_meta().get("author") == "Legacy"
    assert style.needs_training(["OLD1"]) is False           # legacy profile is honored


def test_writes_only_to_neutral_path_never_legacy():
    style.write_profile("A", ["K1"], ["Paper K1"], [], "x",
                        signature={"corpus_words": 3000}, exemplars=["A passage."])
    assert style.STYLE_PROFILE_PATH.exists()
    assert style.LEGACY_PROFILE_PATHS[0].exists() is False


def test_train_records_used_and_skipped_and_measures():
    policy = FakePolicy(requested_keys=["K1", "K2"],
                        prose={"K1": _GOOD_PROSE, "K2": "nope"})
    style.run(policy)
    meta, body = style.read_profile()
    assert meta["papers_used"] and any("Paper K1" in u for u in meta["papers_used"])
    assert meta["papers_skipped"] and any("Paper K2" in s for s in meta["papers_skipped"])
    assert meta.get("signature", {}).get("corpus_words", 0) > 0
    assert "## Voice — exemplars" in body


def test_up_to_date_short_circuits_without_fetching():
    style.write_profile("A. Author", ["K1"], ["Paper K1"], [], "an analysis",
                        signature={"corpus_words": 3000}, exemplars=["A passage."])

    class Boom(FakePolicy):
        def items_by_keys(self, keys):
            raise AssertionError("must not fetch when up to date")

    policy = Boom(author="A. Author", requested_keys=["K1"])
    assert style.run(policy) == 0
    assert any("up to date" in m for m in policy.logs)

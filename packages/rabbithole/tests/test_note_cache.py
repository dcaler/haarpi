"""The per-paper note cache belongs to the paper, not to its position in a list.

`work/annotations/057.json` was keyed by corpus index, and the cache-hit path never checked
that the cached note described the paper at that index. De-duplicating one Zotero item shifts
every paper after it by one, so 26 papers silently inherited their neighbour's notes — the
same failure class as a truncated prompt: the output looks founded and is not.

Runnable two ways:
    pytest tests/test_note_cache.py
    python tests/test_note_cache.py
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

from rabbithole import summarize
from rabbithole.models import Candidate


class _FakeBrain:
    def __init__(self, reply):
        self.reply, self.calls = reply, 0

    def coordinator(self, prompt, system="", **kw):
        self.calls += 1
        return self.reply


def test_locate_reruns_an_empty_cache_but_trusts_a_grounded_one(tmp_path):
    # a source located before it was cited caches [] (no statements); once cited it must RE-locate,
    # else the annotated bibliography reports 'no supporting passage' for a paper that now has one.
    paths = SimpleNamespace(work=tmp_path)
    corpus = [Candidate(title="Grants and R&D", year=2019, abstract="Grants raised firm R&D.")]
    narrative = "Public grants raise private R&D investment [@key1]."
    ck = {0: "key1"}
    fp = tmp_path / "located" / f"{summarize._located_filename('key1')}.json"
    fp.parent.mkdir(parents=True, exist_ok=True)

    fp.write_text("[]")                                        # stale empty cache (pre-citation)
    brain = _FakeBrain('[{"claim": "Grants raise R&D", "quote": "Grants raised firm R&D.", "location": "abstract"}]')
    out = summarize.locate_claims(brain, narrative, corpus, [{}], None, paths, citekeys=ck)
    assert out[0] and out[0][0]["claim"] == "Grants raise R&D"   # empty cache ignored -> re-located
    assert brain.calls == 1                                      # it actually re-ran locate

    grounded = _FakeBrain("[]")                                  # now a non-empty cache exists
    out2 = summarize.locate_claims(grounded, narrative, corpus, [{}], None, paths, citekeys=ck)
    assert out2[0][0]["claim"] == "Grants raise R&D" and grounded.calls == 0   # trusted, not re-run


def test_paper_text_falls_back_to_abstract_when_no_pdf_or_fulltext():
    # a reviewer-added source (the `cite` path) lives as metadata + abstract only; its abstract must
    # be the text located against, else the annotated bibliography reports 'no supporting passage'.
    assert summarize._paper_text(Candidate(abstract="Grants raised R&D by easing firm liquidity.")) \
        == "Grants raised R&D by easing firm liquidity."
    assert summarize._paper_text(Candidate(fulltext="full body", abstract="abs")) == "full body"
    assert summarize._paper_text(Candidate()) == ""


class _Paper:
    def __init__(self, last, year, title="T"):
        self.first_author_last = last
        self.year = year
        self.title = title
        self.venue = "V"
        self.cited_by_count = 0

    def author_year(self):
        return f"{self.first_author_last}, {self.year}"


class _Cfg:
    topic = "t"
    focus = "f"


class _NoReadBrain:
    """Any coordinator call means a cached note was thrown away."""

    class cfg:
        critique_rounds = 1

    def coordinator(self, *a, **kw):
        raise AssertionError("re-read a paper whose notes were already cached")


def _write(d: Path, name: str, paper: str, argument: str, v: int = 1):
    (d / name).write_text(json.dumps({"_paper": paper, "_v": v, "argument": argument,
                                      "findings": "", "themes": []}), encoding="utf-8")


# ── legacy index re-keying ───────────────────────────────────────────────────

def test_legacy_notes_indexed_by_the_paper_they_describe():
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        _write(d, "000.json", "Schelling, 1971", "segregation")
        _write(d, "001.json", "Simon, 1956", "satisficing")
        legacy = summarize._legacy_notes_by_paper(d)
        assert set(legacy) == {"Schelling, 1971", "Simon, 1956"}
        assert legacy["Simon, 1956"]["argument"] == "satisficing"


def test_ambiguous_label_is_dropped_rather_than_guessed_at():
    """Two papers with the same author and year cannot be told apart by label, so neither
    note is trusted — they are re-read instead of silently mis-attached."""
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        _write(d, "000.json", "Zhang, 2011", "one")
        _write(d, "001.json", "Zhang, 2011", "other")
        _write(d, "002.json", "Simon, 1956", "satisficing")
        legacy = summarize._legacy_notes_by_paper(d)
        assert set(legacy) == {"Simon, 1956"}


def test_unidentifiable_note_is_never_trusted():
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        (d / "000.json").write_text('{"argument": "no _paper field"}', encoding="utf-8")
        (d / "001.json").write_text("not json at all", encoding="utf-8")
        assert summarize._legacy_notes_by_paper(d) == {}


# ── the whole point: a shifted corpus must not mis-attach notes ──────────────

def _read(corpus, citekeys, d):
    paths = SimpleNamespace(annotations_dir=d)
    return summarize.read_notes(_NoReadBrain(), corpus, _Cfg(), paths, citekeys=citekeys)


def test_dedup_shifts_the_corpus_and_notes_still_follow_their_paper():
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        # cache written when the corpus held four papers
        for i, (last, yr) in enumerate([("Schelling", 1971), ("Zhang", 2011),
                                        ("Clark", 2008), ("Kirby", 1925)]):
            _write(d, f"{i:03d}.json", f"{last}, {yr}", last.lower())

        # the duplicate at index 1 is removed; everything after shifts down one
        corpus = [_Paper("Schelling", 1971), _Paper("Clark", 2008), _Paper("Kirby", 1925)]
        citekeys = {0: "schelling1971", 1: "clark2008", 2: "kirby1925"}

        notes = _read(corpus, citekeys, d)

        assert [n["argument"] for n in notes] == ["schelling", "clark", "kirby"]
        assert [n["_paper"] for n in notes] == ["Schelling, 1971", "Clark, 2008", "Kirby, 1925"]
        # and the migration re-keyed them, so the next run does not depend on order at all
        assert (d / "clark2008.json").exists()
        assert json.loads((d / "kirby1925.json").read_text())["argument"] == "kirby"


def test_citekey_cache_is_preferred_over_a_stale_legacy_file():
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        _write(d, "000.json", "Schelling, 1971", "stale positional note")
        _write(d, "schelling1971.json", "Schelling, 1971", "current note")
        notes = _read([_Paper("Schelling", 1971)], {0: "schelling1971"}, d)
        assert notes[0]["argument"] == "current note"


def test_a_citekey_with_path_characters_is_made_filesystem_safe():
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        _write(d, "000.json", "C., 1920", "new scales")
        notes = _read([_Paper("C.", 1920)], {0: "c./New1920"}, d)
        assert notes[0]["argument"] == "new scales"
        assert not (d / "c.").exists(), "citekey must not create a directory"


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

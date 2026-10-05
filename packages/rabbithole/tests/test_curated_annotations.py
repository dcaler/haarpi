"""The curated tier of the annotated bibliography holds facts whole.

An uncited source is grounded from its own note. DigiPros 261005 shipped 85 of 119 curated
entries cut off mid-sentence in "…": the note's relevance/findings/argument were joined with
"; ", the locate splitter only breaks after [.!?] + whitespace, so ".; " never split and the
three fields became ONE claim that overran the display cap.

Runnable two ways:
    pytest tests/test_curated_annotations.py
    python tests/test_curated_annotations.py
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from rabbithole import chroma, summarize
from rabbithole.models import Candidate


class _FakeCollection:
    """One paper's chunks; every query returns them all, in order."""

    def __init__(self, chunks: list[str]):
        self.chunks = chunks

    def get(self, where=None, include=None, limit=None):
        return {"ids": [str(i) for i in range(len(self.chunks))]}

    def query(self, query_embeddings, where, n_results, include):
        docs = self.chunks[:n_results]
        return {"documents": [docs],
                "metadatas": [[{"chunk_idx": i, "page": i + 1} for i in range(len(docs))]]}


class _EmbedBrain:
    def embed(self, text):
        return [1.0, 0.0]


_NOTE = {
    "relevance": "It shows how prosopographical databases encode uncertainty; ",
    "findings": "Precision and certainty are modelled as separate quality dimensions. "
                "A case study of 1,200 medieval careers validates the schema.",
    "argument": "Conceptual models should carry data quality explicitly",
}


def test_note_fields_become_separate_statements():
    s = summarize._note_statements(_NOTE)
    assert ";" not in s
    assert s.endswith("explicitly.")                    # unpunctuated field is closed
    assert "uncertainty. Precision" in s                # trailing '; ' replaced by a full stop


def test_each_note_field_is_its_own_claim_and_none_overruns_the_display_cap():
    coll = _FakeCollection([f"Chunk {i} text about databases and quality." for i in range(6)])
    items = chroma.locate_direct(coll, _EmbedBrain(), "akoka2022",
                                 summarize._note_statements(_NOTE))
    claims = [it["claim"] for it in items]
    assert len(claims) == 4                             # 1 relevance + 2 findings + 1 argument
    assert all(len(c) <= summarize._CLAIM_DISPLAY_CHARS for c in claims)
    assert not any(".;" in c for c in claims)


def test_a_pre_versioned_located_cache_is_relocated(tmp_path):
    # The v1 cache holds the glued claims. Trusting it would keep the truncation forever.
    paths = SimpleNamespace(work=tmp_path)
    corpus = [Candidate(title="Grants and R&D", year=2019, abstract="Grants raised firm R&D.")]
    narrative = "Public grants raise private R&D investment [@key1]."
    fp = tmp_path / "located" / f"{summarize._located_filename('key1')}.json"
    fp.parent.mkdir(parents=True)
    fp.write_text(json.dumps([{"claim": "old glued claim.; more", "quote": "q", "location": "p.1"}]))

    class _Brain:
        calls = 0

        def coordinator(self, prompt, system="", **kw):
            self.calls += 1
            return '[{"claim": "Grants raise R&D", "quote": "Grants raised firm R&D.", "location": "abstract"}]'

    brain = _Brain()
    out = summarize.locate_claims(brain, narrative, corpus, [{}], None, paths, citekeys={0: "key1"})
    assert brain.calls == 1 and out[0][0]["claim"] == "Grants raise R&D"
    stored = json.loads(fp.read_text())
    assert stored["_v"] == summarize._LOCATED_VERSION and stored["items"][0]["claim"] == "Grants raise R&D"


if __name__ == "__main__":
    import sys
    import tempfile
    from pathlib import Path
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                if "tmp_path" in fn.__code__.co_varnames[:fn.__code__.co_argcount]:
                    with tempfile.TemporaryDirectory() as d:
                        fn(Path(d))
                else:
                    fn()
                print(f"PASS {name}")
            except AssertionError as e:
                failed += 1
                print(f"FAIL {name}: {e}")
    sys.exit(1 if failed else 0)

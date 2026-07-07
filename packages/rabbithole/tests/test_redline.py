"""Unit tests for the deterministic redline machinery (GPU-free).

Runnable two ways:
    pytest tests/test_redline.py
    python tests/test_redline.py          # no pytest needed
"""

from __future__ import annotations

from docx import Document
from docx.oxml.ns import qn

from rabbithole import redline
from rabbithole.summarize import bibliography


# ── helpers ──────────────────────────────────────────────────────────────────

def _para(text: str):
    return Document().add_paragraph(text)


def _accepted(p_el) -> str:
    """Text with all tracked changes accepted (insertions kept, deletions dropped)."""
    out = []
    for r in p_el.iter(qn("w:r")):
        if r.getparent().tag == qn("w:del"):
            continue
        for t in r.iter(qn("w:t")):
            out.append(t.text or "")
    return "".join(out)


def _rejected(p_el) -> str:
    """Text with all tracked changes rejected (deletions kept, insertions dropped)."""
    out = []
    for r in p_el.iter(qn("w:r")):
        if r.getparent().tag == qn("w:ins"):
            continue
        for t in r.iter(qn("w:t")):
            out.append(t.text or "")
        for t in r.iter(qn("w:delText")):
            out.append(t.text or "")
    return "".join(out)


def _n(p_el, tag: str) -> int:
    return len(p_el.findall(qn(tag)))


# ── _sentence_units ──────────────────────────────────────────────────────────

def test_sentence_units_lossless():
    for text in ("One. Two. Three.",
                 "Single sentence with no boundary",
                 "Q? A! Done.",
                 "Trailing whitespace.  Kept spacing here.  "):
        assert "".join(redline._sentence_units(text)) == text
    assert redline._sentence_units("") == []


def test_sentence_units_count():
    assert len(redline._sentence_units("A stable. B falls. C rises.")) == 3


# ── tracked_replace_sentencewise ─────────────────────────────────────────────

def test_single_sentence_change_is_surgical():
    p = _para("Alpha rises [@alpha2020]. Beta falls sharply [@beta2019]. "
              "Gamma is stable [@gamma2021].")
    new = ("Alpha rises [@alpha2020]. Beta falls by 42% under load [@beta2019]. "
           "Gamma is stable [@gamma2021].")
    changed = redline.tracked_replace_sentencewise(p._p, new, "rabbitHole", redline._Ids(10))

    assert changed is True
    # exactly one sentence changed -> one del + one ins, not a whole-paragraph rewrite
    assert _n(p._p, "w:ins") == 1
    assert _n(p._p, "w:del") == 1
    # accepted == new, rejected == original
    assert _accepted(p._p) == new
    # every citekey survives the accepted text; the two untouched ones verbatim
    for ck in ("[@alpha2020]", "[@beta2019]", "[@gamma2021]"):
        assert ck in _accepted(p._p)
    assert "42%" in _accepted(p._p)


def test_citekeys_in_untouched_sentences_are_preserved():
    """The core fix: rewriting one sentence must not drop citekeys in the others."""
    p = _para("First point [@a1]. Second point [@b2]. Third point [@c3].")
    new = "First point [@a1]. Second point, now expanded [@b2]. Third point [@c3]."
    redline.tracked_replace_sentencewise(p._p, new, "rabbitHole", redline._Ids(1))
    acc = _accepted(p._p)
    assert "[@a1]" in acc and "[@b2]" in acc and "[@c3]" in acc


def test_noop_when_unchanged():
    p = _para("Nothing changes here [@x1].")
    changed = redline.tracked_replace_sentencewise(
        p._p, "Nothing changes here [@x1].", "rabbitHole", redline._Ids(1))
    assert changed is False
    assert _n(p._p, "w:ins") == 0 and _n(p._p, "w:del") == 0


def test_empty_paragraph_is_noop():
    p = _para("")
    assert redline.tracked_replace_sentencewise(
        p._p, "New text [@x1].", "rabbitHole", redline._Ids(1)) is False


# ── two-tier bibliography + redline parser round-trip ────────────────────────

class _FakeSource:
    def __init__(self, last: str, citation: str):
        self.first_author_last = last
        self._citation = citation

    def full_citation(self) -> str:
        return self._citation


def test_bibliography_two_tier_split():
    corpus = [_FakeSource("Bowling", "Bowling, D. (2018)."),
              _FakeSource("Zhang", "Zhang, J. (2011)."),
              _FakeSource("Mehr", "Mehr, S. (2025).")]
    located = {0: [{"claim": "vocal similarity", "location": "p.3", "quote": "q"}],
               1: [{"claim": "tipping", "location": "p.7", "quote": "q2"}],
               2: [{"claim": "core systems", "location": "p.1", "quote": "q3"}]}
    md = bibliography(corpus, located, cited_indices={1})

    assert "### Cited in the review" in md
    assert "### Additional curated sources" in md
    # Zhang is cited; Bowling & Mehr are additional
    cited_part, extra_part = md.split("### Additional curated sources")
    assert "Zhang" in cited_part and "Bowling" not in cited_part
    assert "Bowling" in extra_part and "Mehr" in extra_part


def test_bibliography_legacy_single_list():
    corpus = [_FakeSource("Zhang", "Zhang, J. (2011).")]
    md = bibliography(corpus, {0: [{"claim": "c", "location": "", "quote": ""}]})
    assert "### Cited in the review" not in md
    assert "## Annotated Bibliography" in md


def test_redline_parser_preserves_tiers():
    corpus = [_FakeSource("Bowling", "Bowling, D. (2018)."),
              _FakeSource("Zhang", "Zhang, J. (2011).")]
    located = {0: [{"claim": "vocal", "location": "p.3", "quote": "q"}],
               1: [{"claim": "tipping", "location": "p.7", "quote": "q2"}]}
    md = bibliography(corpus, located, cited_indices={1})
    heading, items = redline._parse_bibliography_md(md)
    kinds = [k for k, _ in items]
    assert kinds == ["sub", "entry", "sub", "entry"]
    subs = [p for k, p in items if k == "sub"]
    assert subs == ["Cited in the review", "Additional curated sources"]


# ── plain-python runner ──────────────────────────────────────────────────────

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

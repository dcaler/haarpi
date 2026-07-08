"""Unit tests for the deterministic redline machinery (GPU-free).

Runnable two ways:
    pytest tests/test_redline.py
    python tests/test_redline.py          # no pytest needed
"""

from __future__ import annotations

from docx import Document
from docx.oxml.ns import qn
from lxml import etree

from rabbithole import guards, redline
from rabbithole.summarize import bibliography

_MATH = "http://schemas.openxmlformats.org/officeDocument/2006/math"


# ── helpers ──────────────────────────────────────────────────────────────────

def _para(text: str):
    return Document().add_paragraph(text)


def _omath(text: str):
    """A minimal <m:oMath> element carrying `text` — an equation, as Word stores it:
    a sibling of the text runs, not inside one. Built with lxml because python-docx's
    OxmlElement only knows its own registered prefixes, and `m` is not one of them."""
    om = etree.SubElement(etree.Element("root"), f"{{{_MATH}}}oMath")
    t = etree.SubElement(etree.SubElement(om, f"{{{_MATH}}}r"), f"{{{_MATH}}}t")
    t.text = text
    return om


def _para_with_math(before: str, equation: str, after: str):
    """A paragraph whose prose is interrupted by an equation, as pandoc renders stats."""
    p = _para(before)
    p._p.append(_omath(equation))
    p._p.append(redline._text_run(after))
    return p


def _math_texts(p_el) -> list[str]:
    return ["".join(t.text or "" for t in om.iter(f"{{{_MATH}}}t"))
            for om in p_el.iter(f"{{{_MATH}}}oMath")]


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


# ── sentence_units (canonical in guards; redline diffs on it) ────────────────

def test_sentence_units_lossless():
    for text in ("One. Two. Three.",
                 "Single sentence with no boundary",
                 "Q? A! Done.",
                 "Trailing whitespace.  Kept spacing here.  "):
        assert "".join(guards.sentence_units(text)) == text
    assert guards.sentence_units("") == []


def test_sentence_units_count():
    assert len(guards.sentence_units("A stable. B falls. C rises.")) == 3


# ── the paragraph atom stream ────────────────────────────────────────────────

def test_serialize_paragraph_sentinels_math():
    """An equation is a sibling of the text runs. A w:t-only paragraph model is blind to
    it, which is what collapsed every sentence diff to a whole-paragraph replacement."""
    p = _para_with_math("Correlation was ", "ρ=0.95", " across conditions [@a1].")
    text, smap, consumed = redline.serialize_paragraph(p._p)
    assert text == "Correlation was ⟦m:1⟧ across conditions [@a1]."
    assert list(smap) == ["⟦m:1⟧"]
    assert len(consumed) == 3  # run, oMath, run
    assert guards.sentinels(text) == ["⟦m:1⟧"]


def test_untouched_sentence_with_math_stays_verbatim():
    """The whole point: a sentence carrying an equation now matches, so it is not redlined
    and its numbers are not severed from the claim they verify."""
    p = _para_with_math("Alpha rises [@a1]. Correlation was ", "ρ=0.95",
                        " here [@b2]. Gamma is stable [@c3].")
    old = redline.paragraph_text(p._p)
    new = old.replace("Gamma is stable [@c3].", "Gamma is stable under load [@c3].")

    assert redline.tracked_replace_sentencewise(
        p._p, new, "rabbitHole", redline._Ids(1)) is True
    assert _n(p._p, "w:ins") == 1 and _n(p._p, "w:del") == 1
    # the equation survives, in place, untouched by any tracked change
    assert _math_texts(p._p) == ["ρ=0.95"]
    om = next(p._p.iter(f"{{{_MATH}}}oMath"))
    assert om.getparent().tag == qn("w:p"), "equation must not be inside w:ins/w:del"
    assert "ρ" not in _accepted(p._p)  # math lives in m:t, never copied into prose


def test_math_survives_a_rewrite_of_its_own_sentence():
    """rabbitHole cannot author an equation, so it never deletes or inserts one: the prose
    around the atom is redlined and the atom is re-laid as accepted content, in place."""
    p = _para_with_math("Correlation was ", "ρ=0.95", " across conditions [@a1].")
    old = redline.paragraph_text(p._p)
    new = "The measured correlation was ⟦m:1⟧ across every condition [@a1]."

    assert redline.tracked_replace_sentencewise(
        p._p, new, "rabbitHole", redline._Ids(1)) is True
    assert _math_texts(p._p) == ["ρ=0.95"]
    om = next(p._p.iter(f"{{{_MATH}}}oMath"))
    assert om.getparent().tag == qn("w:p")
    assert "[@a1]" in _accepted(p._p)


def test_math_is_never_lost_when_the_reviser_drops_the_sentinel():
    """Backstop for the guarded case: even if a rewrite omits the sentinel entirely, the
    equation is re-laid rather than stranded or deleted."""
    p = _para_with_math("Correlation was ", "ρ=0.95", " across conditions [@a1].")
    new = "Correlation was strong across conditions [@a1]."
    redline.tracked_replace_sentencewise(p._p, new, "rabbitHole", redline._Ids(1))
    assert _math_texts(p._p) == ["ρ=0.95"]


def test_dropped_sentinel_is_a_guard_failure():
    old = "Correlation was ⟦m:1⟧ across conditions [@a1]."
    new = "Correlation was strong across conditions [@a1]."
    findings = guards.dropped_sentinels(old, new)
    assert len(findings) == 1 and findings[0].kind == "dropped-equation"
    assert "⟦m:1⟧" in findings[0].imperative
    assert guards.dropped_sentinels(old, old) == []


# ── comment anchors: which sentences a comment actually bears on ─────────────

def test_comment_spans_locate_the_anchored_sentence():
    p = _para("First point [@a1]. Second point [@b2]. Third point [@c3].")
    text = redline.paragraph_text(p._p)
    # a reviewer highlighting "Second point [@b2]." — offsets into the serialized text
    start = text.index("Second")
    span = (start, start + len("Second point [@b2]."))
    assert redline.anchored_sentences(text, span) == {1}


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

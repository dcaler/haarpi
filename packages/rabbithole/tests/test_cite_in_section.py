"""Cite-in-section: a comment left on a section HEADING that names sources already in the
corpus ("I've added Doblinger 2019 and Howell 2017 to Zotero, cite them") must be answered by
citing those sources in the section's body — not skipped as a "find more" ask.

These pin the two pure pieces the routing is built from:
  * `_cite_targets` — resolve the named works to corpus citekeys (the DRvehicle case);
  * `redline.first_body_paragraph_under` — where in the section the citation lands.

The LLM rewrite itself (the reviser) is covered by test_revise_adversary; the routing is the
composition of these two — non-empty targets AND a body paragraph -> cite in the section.

Runnable two ways:
    pytest tests/test_cite_in_section.py
    python tests/test_cite_in_section.py
"""

from __future__ import annotations

from docx import Document

from rabbithole import redline, revise
from rabbithole.models import Author, Candidate


# ── _cite_targets: name a corpus paper by author-year or [@key] ────────────────

def _corpus():
    return [
        Candidate(title="Grants vs loans", authors=[Author(family="Doblinger")], year=2019),
        Candidate(title="Firm risk", authors=[Author(family="Howell")], year=2017),
        Candidate(title="Unrelated", authors=[Author(family="Zhang")], year=2020),
    ]


_CITEKEYS = {0: "doblinger2019", 1: "howell2017", 2: "zhang2020"}


def test_author_year_mentions_resolve_to_corpus_citekeys():
    """The DRvehicle comment, verbatim — the two named papers resolve, the third does not."""
    comment = ["I've added Doblinger 2019 and Howell 2017 to the zotero collection. cite them"]
    assert revise._cite_targets(comment, _corpus(), _CITEKEYS) == ["doblinger2019", "howell2017"]


def test_explicit_citekey_mentions_resolve():
    comment = ["cite [@zhang2020] here"]
    assert revise._cite_targets(comment, _corpus(), _CITEKEYS) == ["zhang2020"]


def test_a_surname_without_the_matching_year_does_not_resolve():
    """Author-year needs BOTH — 'Doblinger 2021' must not pull in the 2019 Doblinger."""
    assert revise._cite_targets(["cite Doblinger 2021"], _corpus(), _CITEKEYS) == []


def test_a_named_paper_not_in_the_corpus_yields_nothing():
    """A work `build` never embedded can't be cited — it falls through to the sources route."""
    assert revise._cite_targets(["cite Parrish 2016"], _corpus(), _CITEKEYS) == []


def test_a_bare_surname_substring_does_not_false_match():
    """Word-boundary matching: 'Howells' (a different author) must not match 'Howell'."""
    assert revise._cite_targets(["see Howells 2017"], _corpus(), _CITEKEYS) == []


# ── first_body_paragraph_under: where the citation lands ───────────────────────

def _doc_to(tmp_path, rows):
    """rows: list of (style, text). style '' = body ('Normal')."""
    doc = Document()
    for style, text in rows:
        doc.add_paragraph(text, style=style or None)
    p = tmp_path / "d.docx"
    doc.save(str(p))
    return p


def test_returns_first_body_paragraph_after_the_heading(tmp_path):
    p = _doc_to(tmp_path, [
        ("Heading 2", "Grants versus loans reshape firm risk"),   # 0
        ("", "Firms choose between grants and loans."),           # 1  <- first body
        ("", "The choice reshapes their risk profile."),          # 2
        ("Heading 2", "Next section"),                            # 3
        ("", "Something else."),                                  # 4
    ])
    assert redline.first_body_paragraph_under(p, 0) == {
        "para": 1, "text": "Firms choose between grants and loans."}
    # under the SECOND heading, its own section's first body paragraph
    assert redline.first_body_paragraph_under(p, 3)["para"] == 4


def test_skips_empty_paragraphs(tmp_path):
    p = _doc_to(tmp_path, [
        ("Heading 2", "H"), ("", "   "), ("", ""), ("", "real body")])
    assert redline.first_body_paragraph_under(p, 0)["para"] == 3


def test_none_when_heading_is_immediately_followed_by_another_heading(tmp_path):
    p = _doc_to(tmp_path, [("Heading 2", "A"), ("Heading 2", "B"), ("", "body")])
    assert redline.first_body_paragraph_under(p, 0) is None


def test_none_when_heading_is_last(tmp_path):
    p = _doc_to(tmp_path, [("", "body"), ("Heading 2", "trailing heading")])
    assert redline.first_body_paragraph_under(p, 1) is None


# ── the routing decision is exactly the composition of the two ─────────────────

def test_routing_fires_only_when_a_named_corpus_paper_and_a_body_paragraph_both_exist(tmp_path):
    p = _doc_to(tmp_path, [
        ("Heading 2", "Grants versus loans reshape firm risk"),
        ("", "Firms choose between grants and loans.")])
    comment = ["I've added Doblinger 2019 to the zotero collection. cite them"]
    targets = revise._cite_targets(comment, _corpus(), _CITEKEYS)
    body = redline.first_body_paragraph_under(p, 0)
    assert targets == ["doblinger2019"] and body is not None      # -> cite in section

    # a "find more" heading comment names nothing citeable -> no targets -> falls through
    assert revise._cite_targets(["find more on firm financing"], _corpus(), _CITEKEYS) == []


if __name__ == "__main__":
    import tempfile, traceback
    from pathlib import Path
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failures = 0
    for name, fn in fns:
        try:
            if fn.__code__.co_argcount:
                with tempfile.TemporaryDirectory() as d:
                    fn(Path(d))
            else:
                fn()
            print(f"  PASS  {name}")
        except Exception:  # noqa: BLE001
            failures += 1
            print(f"  FAIL  {name}")
            traceback.print_exc()
    print(f"\n{len(fns) - failures}/{len(fns)} passed")
    raise SystemExit(1 if failures else 0)

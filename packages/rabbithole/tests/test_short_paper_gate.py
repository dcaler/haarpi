"""`looks_like_fulltext` is a capability check, not a source policy.

It exists to catch an abstract-only or preview PDF — a teaser instead of the paper.
Source policy belongs at `gather`; what a person filed in the collection is theirs to
decide (see test_ingest_trusts_the_collection.py). So this gate must not double as a way
of excluding short work: a genuine 3-page paper is a paper.

The floor was 600 words, which rejected a fully-extracted 3-page JASSS corrigendum at
564 — a 36-word miss that said nothing about whether we had the whole document.
"""
from rabbithole.pdfs import SHORT_PAPER_MIN_WORDS, looks_like_fulltext


def _doc(words, tail=""):
    return " ".join(["word"] * words) + (" " + tail if tail else "")


def test_the_corrigendum_gets_in():
    """The real case: 564 words over 3 pages, references section present, extracted
    completely. DigiPros' report run skipped it as 'no usable full text'."""
    assert looks_like_fulltext(_doc(564, "References"), 3)


def test_a_bare_abstract_still_does_not():
    """An abstract runs 150-300 words. Even one that says 'references' in passing is
    still not the paper."""
    assert not looks_like_fulltext(_doc(250, "references"), 1)
    assert not looks_like_fulltext(_doc(300, "see references"), 2)


def test_the_references_section_is_the_discriminator():
    """Word count alone admits nothing below the long-form floor — a preview that runs
    long is still a preview if it stops before the bibliography."""
    assert not looks_like_fulltext(_doc(1400), 3)
    assert looks_like_fulltext(_doc(1400, "Bibliography"), 3)


def test_the_long_form_routes_are_untouched():
    assert looks_like_fulltext(_doc(1500), 1)
    assert looks_like_fulltext(_doc(10), 4)


def test_the_floor_sits_above_abstract_length():
    """Pins the intent rather than the number: comfortably clear of a 300-word abstract,
    and nowhere near paper length."""
    assert 300 < SHORT_PAPER_MIN_WORDS < 600

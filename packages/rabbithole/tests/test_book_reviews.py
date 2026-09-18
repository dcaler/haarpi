"""A review OF A BOOK is not literature, and nothing in the metadata says so.

Every source types these `journal-article`, so they clear the type gate; then the word
"review" in the title floats them up the ranking AND puts them on the deep snowball seed
list, where one of them spends 25 reference pulls on a single monograph's bibliography.

The cases below are verbatim titles from the corpora on disk, not invented ones. The
discriminator they establish is the reviewed book's IMPRINT — attribution, publisher,
place, pagination, binding, price — which a real paper has no reason to carry.
"""
from rabbithole import filters
from rabbithole.models import Candidate


def _c(title, item_type="journal-article"):
    return Candidate(title=title, item_type=item_type)


# ── real book reviews that reached real corpora ─────────────────────────────
LABELLED = "Book Reviews : Joshua M. Epstein &amp; Robert Axtell: Growing Artificial Societies: Social Science from the Bottom Up. Washington, DC: Brookings Institution Press an"
BARE = "Growing artificial societies: Social science from the bottom up Joshua M. Epstein and Robert Axtell Cambridge, Mass.: MIT Press, 1996 xvi + 208 pp. Pbk., $18.95; Hbk"
BY_AUTHOR = "Review of Composite-based Structural Equation Modeling: Analyzing Latent and Emergent Variables: by Jörg Henseler, New York, NY, The Guilford Press, 2021, 364 pp., $"
ISBN_FORM = "Janet Z. Giele and Glen H. Elder Jr., (eds) Methods of Life Course Research: Qualitative and Quantitative Approaches, Sage Publications, 1998 ISBN 0 76191437 4."


def test_labelled_book_review_is_caught():
    assert filters.is_book_review(_c(LABELLED))


def test_unlabelled_book_review_is_caught_by_its_imprint():
    """The dangerous one: the title is just the BOOK's title, so no keyword catches it.
    Only the pagination + publisher + binding tail gives it away."""
    assert filters.is_book_review(_c(BARE))
    assert "review" not in BARE.lower()


def test_by_author_and_publisher_imprint():
    assert filters.is_book_review(_c(BY_AUTHOR))


def test_isbn_and_publisher_imprint():
    assert filters.is_book_review(_c(ISBN_FORM))


# ── genuine papers that must survive ────────────────────────────────────────
def test_review_article_is_not_a_book_review():
    """elephantRoom's corpus. Starts with 'Review of' and is a real paper — which is why
    the test cannot be about the word 'review'."""
    assert not filters.is_book_review(
        _c("Review of carbon leakage under regionally differentiated climate policies"))


def test_one_marker_is_not_enough():
    """The real margin: at a threshold of one marker this paper — 'by Including Moral' —
    would be dropped. Two is what separates an imprint from a coincidence."""
    t = "Predicting Recycling Behavior by Including Moral Norms into the Theory of Planned Behavior"
    assert sum(1 for r in filters._IMPRINT_RES if r.search(t)) == 1
    assert not filters.is_book_review(_c(t))


def test_systematic_review_survives():
    c = _c("A systematic review and meta-analysis of sequence analysis in the social sciences")
    assert not filters.is_book_review(c)
    assert filters.is_systematic_review(c)


# ── the consequences, not just the predicate ────────────────────────────────
def test_a_book_review_is_never_a_review_article():
    """`discover` seeds the snowball from `is_review` at 25 references per seed. A book
    review on that list is a deep pull through one monograph's bibliography."""
    assert not filters.is_review(_c(LABELLED))
    assert not filters.is_review(_c(BY_AUTHOR))


def test_the_type_gate_drops_them():
    """One rule for all four call sites — discover's search loop, its snowball merge,
    and the two Zotero ingests all go through here."""
    for t in (LABELLED, BARE, BY_AUTHOR, ISBN_FORM):
        assert not filters.item_type_allowed(_c(t), include_preprints=True,
                                             include_news=True)
    assert filters.item_type_allowed(
        _c("Review of carbon leakage under regionally differentiated climate policies"),
        include_preprints=False, include_news=False)


def test_explicit_item_type_if_a_source_ever_supplies_one():
    assert filters.is_book_review(_c("Something quite ordinary", item_type="book-review"))

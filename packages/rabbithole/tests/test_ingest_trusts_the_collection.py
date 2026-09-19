"""Membership of the Zotero collection IS the decision.

Exclusions belong at `gather`, where rabbitHole is judging what to propose off the open
web and a whole book, an editorial or a review of a book is usually noise. An item that
reached the collection WITH FULL TEXT can only have got there by a person putting it
there, so re-applying the gather policy at ingest overrules the human.

It did real damage: DigiPros' `report` run on 2026-09-19 threw away Epstein & Axtell's
*Growing Artificial Societies* and Epstein's *Generative Social Science* — two of the
project's foundations, deliberately filed, PDFs attached — with `[skip] excluded item
type (book)`.

The full-text requirement is NOT part of this: it is a capability limit, not a policy.
There is nothing to embed without text.
"""
import ast
import inspect

from rabbithole import corpus, filters
from rabbithole.models import Candidate


def test_neither_ingest_path_applies_the_gather_gate():
    """The regression this file exists for: no type policy on either human-curated path."""
    for fn in (corpus._corpus_item_from_zotero, corpus.ingest_from_folder):
        src = inspect.getsource(fn)
        assert "item_type_allowed" not in src, (
            f"{fn.__name__} re-applies gather's type policy to human-curated input")


def test_the_books_digipros_lost_would_pass_the_gather_gate_only_as_candidates():
    """Stated the other way round: these ARE excluded at gather, and that stays true.
    The point is not that books became acceptable candidates — it is that the collection
    is not a candidate list."""
    for t in ("Growing Artificial Societies: Social Science from the Bottom Up",
              "Generative social science: Studies in agent-based computational modeling"):
        assert not filters.item_type_allowed(Candidate(title=t, item_type="book"),
                                             include_preprints=True, include_news=True)


def test_ingest_still_refuses_zotero_plumbing():
    """Attachments and notes are not sources; they are how Zotero stores the PDF."""
    src = inspect.getsource(corpus._corpus_item_from_zotero)
    assert '("attachment", "note")' in src


def test_ingest_still_requires_full_text():
    """The one check that stays, on both paths — nothing to embed without text."""
    for fn in (corpus._corpus_item_from_zotero, corpus.ingest_from_folder):
        assert "looks_like_fulltext" in inspect.getsource(fn)


def test_ingest_does_not_apply_the_other_gather_filters_either():
    """Publisher, date and language were already the human's call on this path. The type
    gate was the last holdout; this pins that the whole module stays out of it.

    Parsed rather than grepped, because the docstrings here NAME these functions to
    explain why they are absent — a substring test would match its own prose.
    """
    tree = ast.parse(inspect.getsource(corpus))
    called = {n.func.attr for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    called |= {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    for gate in ("is_excluded", "within_dates", "is_english", "is_book_review",
                 "item_type_allowed"):
        assert gate not in called, f"corpus.py applies gather's {gate}() to curated input"

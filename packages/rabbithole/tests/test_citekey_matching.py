"""A citekey must identify ONE work. Title alone does not.

DigiPros holds two different works titled exactly "Prosopography", neither with a DOI:

    Stone,           "Prosopography", Daedalus, 1971              -> stoneProsopography1971
    Dogan & Lebaron, "Prosopography", in Intl Orgs & Research Methods, 2023

`backfill_citekeys` matched Better BibTeX keys by DOI then by normalised title with no other
tiebreak, and `_bibtex_key_maps` built that map with `setdefault` — first block wins, the
rest silently discarded. So the 2023 chapter was assigned Stone's key. The narrative citing
it would emit [@stoneProsopography1971], the bibliography would print Stone, and the real
2023 source would be uncitable and invisible.

The rule these tests hold: a wrong match is worse than no match. An unmatched record falls
through to a generated {last}{year} key, which is correct and unique.
"""
from rabbithole import corpus as corpus_mod, guards
from rabbithole.models import Author, Candidate
from rabbithole.summarize import _make_citekeys, _patch_bibtex_keys

BIB = """
@article{stoneProsopography1971,
  title = {Prosopography},
  author = {Stone, Lawrence},
  journal = {Daedalus},
  date = {1971},
}

@incollection{doganProsopography2023,
  title = {Prosopography},
  author = {Dogan, Mattei and Lebaron, Frédéric},
  booktitle = {International Organizations and Research Methods},
  date = {2023},
  urldate = {2026-09-19},
}

@article{hatnaSchelling2015,
  title = {Schelling Segregation Revisited},
  author = {Hatna, Erez},
  doi = {10.1234/abc},
  date = {2015},
}
"""


def _c(title, last, year, doi=""):
    return Candidate(title=title, authors=[Author(family=last)], year=year, doi=doi)


def test_the_two_prosopographies_get_their_own_keys():
    """The case itself, end to end through the backfill's matcher."""
    by_doi, by_ty, by_title = corpus_mod._bibtex_key_maps(BIB)
    stone = _c("Prosopography", "Stone", 1971)
    dogan = _c("Prosopography", "Dogan", 2023)
    assert by_ty[(stone.title_key, "1971")] == "stoneProsopography1971"
    assert by_ty[(dogan.title_key, "2023")] == "doganProsopography2023"


def test_a_title_two_works_claim_is_dropped_not_awarded_to_the_first():
    """`setdefault` gave it to whichever block came first. Ambiguity now poisons the entry:
    no key at all, so the generator produces a correct one."""
    _, _, by_title = corpus_mod._bibtex_key_maps(BIB)
    assert "prosopography" not in by_title, "an ambiguous title maps to nothing"
    assert by_title["schelling segregation revisited"] == "hatnaSchelling2015"


def test_an_unmatched_record_gets_a_generated_key_not_someone_elses():
    """What the fallthrough is worth: distinct, correct keys rather than one shared wrong one."""
    corpus = [_c("Prosopography", "Stone", 1971), _c("Prosopography", "Dogan", 2023)]
    keys = _make_citekeys(corpus)
    assert keys[0] != keys[1], "two works, two keys"
    assert keys[0] == "stone1971" and keys[1] == "dogan2023"


def test_doi_still_wins_over_any_title():
    by_doi, _, _ = corpus_mod._bibtex_key_maps(BIB)
    assert by_doi["10.1234/abc"] == "hatnaSchelling2015"


def test_unambiguous_drops_a_fingerprint_two_keys_claim():
    assert corpus_mod.unambiguous([("a", "k1"), ("b", "k2")]) == {"a": "k1", "b": "k2"}
    assert corpus_mod.unambiguous([("a", "k1"), ("a", "k2")]) == {}
    assert corpus_mod.unambiguous([("a", "k1"), ("a", "k1")]) == {"a": "k1"}, "same key is not ambiguity"
    # poisoned stays poisoned even if a third claimant repeats an earlier key
    assert corpus_mod.unambiguous([("a", "k1"), ("a", "k2"), ("a", "k1")]) == {}


def test_the_year_is_not_read_out_of_urldate():
    """`urldate = {2026-09-19}` sits in the Dogan block; `\\bdate` must not match inside it."""
    block = "@incollection{x,\n  date = {2023},\n  urldate = {2026-09-19},\n}"
    assert corpus_mod._bibtex_year(block) == "2023"
    assert corpus_mod._bibtex_year("@article{x,\n  urldate = {2026-09-19},\n}") == ""


def test_the_export_does_not_stamp_one_works_key_onto_the_other():
    """The same defect backwards: refs.bib is patched to match the narrative's keys, and a
    title-only map put Stone's key on Dogan's block."""
    from rabbithole.corpus import unambiguous
    corpus = [_c("Prosopography", "Stone", 1971), _c("Prosopography", "Dogan", 2023)]
    keys = {0: "stone1971", 1: "dogan2023"}
    titles = [(c.title_key, keys[i]) for i, c in enumerate(corpus)]
    tys = [((c.title_key, str(c.year)), keys[i]) for i, c in enumerate(corpus)]
    out = _patch_bibtex_keys(BIB, {}, unambiguous(titles), unambiguous(tys))
    assert "@article{stone1971," in out
    assert "@incollection{dogan2023," in out
    assert "stoneProsopography1971" not in out and "doganProsopography2023" not in out


def test_the_finding_says_do_not_delete_when_the_works_differ():
    """The old text advised deleting one of two real sources."""
    corpus = [_c("Prosopography", "Stone", 1971), _c("Prosopography", "Dogan", 2023)]
    (f,) = guards.duplicate_citekeys({0: "stoneProsopography1971", 1: "stoneProsopography1971"},
                                     corpus)
    assert "DIFFERENT works" in f.imperative and "Do not delete" in f.imperative


def test_the_finding_still_says_deduplicate_for_one_paper_twice():
    same = [_c("Growing Artificial Societies", "Epstein", 1996),
            _c("Growing artificial societies.", "Epstein", 1996)]
    (f,) = guards.duplicate_citekeys({0: "epstein1996", 1: "epstein1996"}, same)
    assert "De-duplicate" in f.imperative


def test_the_finding_is_unchanged_without_a_corpus_to_judge_from():
    (f,) = guards.duplicate_citekeys({0: "zhang2011", 1: "zhang2011"})
    assert "De-duplicate" in f.imperative

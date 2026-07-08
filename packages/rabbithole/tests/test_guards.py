"""Unit tests for the deterministic guard battery (no GPU / no LLM / no I/O).

Every check here was previously either an LLM judgement call or nothing at all. They are
pure functions over text, which is the point: the polestar — a broad, deep, verifiable
foundation — is mechanical, and mechanical things belong in Python.

Runnable two ways:
    pytest tests/test_guards.py
    python tests/test_guards.py
"""

from __future__ import annotations

from rabbithole import guards

# A narrative shaped like the one the old rules produced: one paragraph per section, each
# resting on two sources, nothing uncited so the only enforced guard was satisfied.
THIN = """\
## Adaptive processing

Listeners track tonal stability automatically [@mehr2025]. Neuromagnetic responses follow \
the tonic [@otsuka2008]. The effect survives cortical modulation [@otsuka2008]. It appears \
early in development [@mehr2025]. It holds across cultures [@mehr2025]. Consonance is \
predicted by vocal resemblance [@otsuka2008]. Judgements repeat across sessions [@otsuka2008].

## Agent-based dynamics

Mild preferences produce clustering [@schelling1971]. Preference targets drive the dynamics \
[@clark2008].
"""

WOVEN = """\
## Adaptive processing

Listeners track tonal stability automatically [@mehr2025], an effect neuromagnetic responses \
confirm at the cortical level [@otsuka2008] and which vocal-resemblance models predict from \
spectra alone [@bowling2018].

Those three lines of evidence diverge on timing [@schelling1971], and the divergence is what \
constrains any agent model [@clark2008].
"""

CORPUS = {"mehr2025", "otsuka2008", "schelling1971", "clark2008", "bowling2018", "zhang2011"}


# ── structure ────────────────────────────────────────────────────────────────

def test_parse_paragraphs_tags_sections():
    paras = guards.parse_paragraphs(THIN)
    assert [p.section for p in paras] == [0, 1]
    assert [p.index for p in paras] == [0, 0]
    assert paras[1].distinct == {"schelling1971", "clark2008"}


def test_parse_paragraphs_ignores_heading_only_blocks():
    assert guards.parse_paragraphs("## Just a heading\n") == []


def test_all_citekeys_splits_grouped_citations():
    assert guards.all_citekeys("A claim [@a; @b] and another [@c].") == ["a", "b", "c"]


def test_sentence_units_are_lossless():
    text = "One. Two! Three?  Four."
    assert "".join(guards.sentence_units(text)) == text


# ── verifiability ────────────────────────────────────────────────────────────

def test_uncited_paragraph_is_a_finding():
    n = "## Theme\n\nA claim with no source at all.\n"
    (f,) = guards.uncited_paragraphs(guards.parse_paragraphs(n))
    assert f.kind == "uncited"


def test_unresolved_key_is_a_finding():
    (f,) = guards.unresolved_keys("A claim [@ghost1999].", CORPUS)
    assert "[@ghost1999]" in f.imperative


def test_author_year_prose_is_a_finding():
    (f,) = guards.author_year_prose("As Schelling (1971) showed, clustering emerges.")
    assert f.kind == "author-year"
    assert not guards.author_year_prose("Clustering emerges [@schelling1971].")


def test_dropped_citekey_is_a_finding():
    (f,) = guards.dropped_citekeys("A [@a1] and B [@b2].", "A [@a1] and B.")
    assert "[@b2]" in f.imperative


def test_duplicate_citekey_is_a_finding():
    """Two corpus entries under one key: the key->index map keeps one, and the bibliography
    can print the poorer record for a source the narrative cites."""
    (f,) = guards.duplicate_citekeys({0: "zhang2011", 1: "zhang2011", 2: "clark2008"})
    assert f.kind == "duplicate-citekey"
    assert "[@zhang2011]" in f.imperative
    assert guards.duplicate_citekeys({0: "a", 1: "b"}) == []


def test_sentinel_integrity():
    old, new = "Correlation ⟦m:1⟧ held [@a1].", "Correlation was strong [@a1]."
    assert guards.dropped_sentinels(old, new)[0].kind == "dropped-equation"
    assert guards.invented_sentinels(old, "Correlation ⟦m:1⟧ and ⟦m:9⟧ [@a1].")
    assert guards.dropped_sentinels(old, old) == []
    assert guards.invented_sentinels(old, old) == []


# ── breadth ──────────────────────────────────────────────────────────────────

def test_short_section_is_a_finding():
    """A heading over one paragraph is an annotated-bibliography entry. The rule said 'at
    least three paragraphs' and nothing ever counted them."""
    findings = guards.short_sections(guards.parse_paragraphs(THIN))
    assert {f.where for f in findings} == {"section 0", "section 1"}


def test_accretion_requires_a_new_source_each_paragraph():
    n = ("## Theme\n\nFirst [@a1] and second [@b2].\n\n"
         "Restating the same evidence [@a1] again [@b2].\n")
    (f,) = guards.accretion_violations(guards.parse_paragraphs(n))
    assert f.kind == "accretion" and f.where == "section 0 para 1"


def test_accretion_satisfied_when_a_new_source_arrives():
    n = ("## Theme\n\nFirst [@a1] and second [@b2].\n\n"
         "Now a third view [@c3] qualifies the first [@a1].\n")
    assert guards.accretion_violations(guards.parse_paragraphs(n)) == []


def test_triangulation_flags_a_single_source_paragraph():
    n = "## Theme\n\nOne claim, one source [@a1]. Restated [@a1].\n"
    (f,) = guards.triangulation_violations(guards.parse_paragraphs(n))
    assert f.kind == "triangulation"


def test_sparse_paragraph_scales_with_length():
    """Seven sentences on two sources clears a flat per-paragraph floor and is still thin.
    The section-1 paragraph (2 sentences, 2 sources) is dense enough and must not fire."""
    findings = guards.sparse_paragraphs(guards.parse_paragraphs(THIN))
    assert [f.where for f in findings] == ["section 0 para 0"]
    assert guards.sparse_paragraphs(guards.parse_paragraphs(WOVEN)) == []


def test_thin_section_counts_distinct_sources():
    (f,) = guards.thin_sections(guards.parse_paragraphs(
        "## Theme\n\nA [@a1] and B [@b2].\n\nMore on A [@a1].\n"))
    assert f.kind == "thin-section"


# ── the disposition ledger ───────────────────────────────────────────────────

def test_disposition_partitions_the_corpus():
    d = guards.disposition(THIN, CORPUS, rejected={"zhang2011": "off topic"})
    assert d.cited == {"mehr2025", "otsuka2008", "schelling1971", "clark2008"}
    assert d.rejected == {"zhang2011"}
    assert d.unplaced == {"bowling2018"}
    assert d.total == len(CORPUS)


def test_a_cited_source_cannot_also_be_rejected():
    d = guards.disposition("A claim [@mehr2025].", CORPUS, rejected={"mehr2025": "nope"})
    assert d.cited == {"mehr2025"} and d.rejected == set()


def test_unplaced_findings_carry_the_digest_line():
    d = guards.disposition(THIN, CORPUS)
    (f,) = guards.unplaced_findings(d, {"bowling2018": "[@bowling2018] vocal similarity"})
    assert f.kind == "unplaced-source"
    assert "vocal similarity" in f.imperative


def test_no_finding_when_every_source_is_decided():
    d = guards.disposition(THIN, {"mehr2025", "otsuka2008", "schelling1971", "clark2008"})
    assert guards.unplaced_findings(d, {}) == []


# ── minimality ───────────────────────────────────────────────────────────────

def test_touched_indices_finds_the_changed_sentence():
    old = "Alpha rises. Beta falls. Gamma holds."
    new = "Alpha rises. Beta falls by 42%. Gamma holds."
    assert guards.touched_indices(old, new) == {1}


def test_minimal_edit_violation_names_the_collateral_sentences():
    (f,) = guards.minimal_edit_violation(touched={0, 1, 2}, anchored={1}, n_sentences=3)
    assert f.kind == "minimal-edit"
    assert "1, 3" in f.imperative      # 1-indexed for the reviser


def test_minimal_edit_clean_when_only_the_anchor_moved():
    assert guards.minimal_edit_violation({1}, {1}, 3) == []


def test_minimal_edit_inactive_when_the_whole_paragraph_is_anchored():
    """A reviewer who selected the whole paragraph licenses rewriting all of it. Flagging
    that would be a false positive, so the guard says nothing."""
    assert guards.minimal_edit_violation({0, 1, 2}, {0, 1, 2}, 3) == []
    assert guards.minimal_edit_violation({0, 1, 2}, set(), 3) == []


def test_minimal_edit_active_on_a_two_sentence_paragraph():
    """Anchoring one of two sentences is not a whole-paragraph selection. An 'all but one'
    escape hatch would switch the guard off exactly where a paragraph is short enough for a
    full rewrite to be tempting."""
    (f,) = guards.minimal_edit_violation(touched={0, 1}, anchored={0}, n_sentences=2)
    assert f.kind == "minimal-edit" and "2" in f.imperative


# ── the polestar as a number ─────────────────────────────────────────────────

def test_metrics_line_reports_the_defect():
    m = guards.metrics(THIN, CORPUS)
    assert m.sources_cited == 4 and m.corpus_size == 6
    assert m.unplaced == 2 and m.rejected == 0
    assert m.triangulated == 2 and m.paragraphs == 2
    assert "sources cited 4/6" in m.line()
    assert "unresolved keys 0" in m.line()


def test_metrics_counts_unresolved_keys():
    assert guards.metrics("A claim [@ghost1999].", CORPUS).unresolved == 1


def test_as_critique_renders_imperatives():
    out = guards.as_critique(guards.author_year_prose("Schelling (1971) showed x."), "FIX:")
    assert out.startswith("FIX:\n- ")
    assert guards.as_critique([], "FIX:") == ""


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

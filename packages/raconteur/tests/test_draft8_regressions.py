"""Every defect the css2026 draft-8 manuscript shipped with, pinned.

Draft 8 was the first run after bullets-as-truth. The paragraph-width fix held in four
sections of six and the manuscript still came out of the pipeline 5,365 words against a
5,000-word inclusive cap, with a 358-word abstract, two figures numbered 1, 26 padding
citations, and five sentences pasted verbatim between sections. Each of those had either no
guard at all or a guard that could not see far enough to fire.

Measured against the outline the draft was ACTUALLY given — the release in outline/output/,
minted from the author's redline, not the pre-redline _ra.md — Background, Results and
Discussion all sit outside their bullet-derived bands. The counts here are from that file.

The through-line is the polestar: Python decides THAT something is wrong, precisely, and
states it as an imperative. Where a check ran at the wrong altitude — per section when the
fact is document-wide, or per document when the repair is cheapest per section — it was not
enforcing anything.
"""

from __future__ import annotations

import inspect

from raconteur import guards, paper


def _outline(bullets: dict[str, int], section: str = "Methods") -> str:
    body = "".join(f"- bullet {i}\n" for i in range(bullets.get(section, 0)))
    return f"# T\n\n## {section}\n\n{body}"


# ── the two-authorities bug, one level up ────────────────────────────────────

def test_section_lengths_judges_against_the_bullets_not_the_share():
    """Two answers to one question. The draft prompt reads a section's band off its BULLETS
    and section_lengths read it off the SHARE; they agree only while the outline's bullet
    count matches the share it was derived from, and letting the author change that is the
    entire point of bullets-as-truth.

    On css2026 they happened to coincide (Background: four bullets and a 15% share both give
    480–720), so this was latent there rather than the cause of that draft's overrun."""
    src = inspect.getsource(guards.section_lengths)
    assert "section_band(" in src
    assert "section_target(" not in src


def test_the_two_bands_diverge_the_moment_the_author_edits_a_bullet():
    """What "latent" means: add one bullet to Background and the share is silently wrong."""
    outline = "# T\n\n## Background\n\n" + "".join(f"- b{i}\n" for i in range(8))
    assert guards.section_target("Background", 4000) == (480, 720)
    assert guards.section_band("Background", outline, 4000) == (960, 1440)
    body = "## Background\n\n" + ("word " * 1100)
    assert not guards.section_lengths(body, outline, budget=4000), \
        "1,100 words is what eight approved bullets ask for; the share must not veto it"


def test_a_section_with_bullets_is_judged_on_them():
    outline = _outline({"Methods": 3})
    body = "## Methods\n\n" + ("word " * 1046)
    fat = [f for f in guards.section_lengths(body, outline, budget=4000)
           if f.kind == "section-fat"]
    assert fat, "1046 words against 3 bullets must be section-fat"
    # 3 bullets x 150 words, +/- 20% -> 360-540, NOT the 480-720 the share would give
    assert "540" in fat[0].imperative


def test_furniture_gets_no_bullet_derived_band():
    """Acknowledgements carries its CRediT role list as bullets — three on css2026 — and a
    bullet band demanded 450 words of a 36-word passthrough the tool may not draft at all."""
    outline = "# T\n\n## Acknowledgements\n\n" + "".join(f"- role {i}\n" for i in range(3))
    assert guards.section_band("Acknowledgements", outline, budget=4000) == (0, 0)
    assert not guards.section_lengths("## Acknowledgements\n\nCRediT statement.",
                                      outline, budget=4000)


# ── altitude: the section loop must see the section's shape ──────────────────

def test_the_section_loop_checks_length_and_paragraph_count():
    """Both were checked only once every section existed, which is the most expensive
    moment to discover them: the section repair is a well-posed rewrite of one section, the
    manuscript repair is the same rewrite with the paper already built around it."""
    src = inspect.getsource(paper._guard_section)
    assert "section_size(" in src
    assert "paragraph_count(" in src
    assert "padded_citations(" in src


def test_the_draft_loop_hands_the_section_its_band_and_bullets():
    src = inspect.getsource(paper._draft_paper)
    assert "band=band if band[0] else None" in src
    assert "bullets=n_bullets" in src


def test_section_size_says_which_way_and_by_how_much():
    fat = guards.section_size("Methods", "word " * 700, (400, 600))
    assert fat and fat[0].kind == "section-fat" and "100" in fat[0].imperative
    thin = guards.section_size("Methods", "word " * 300, (400, 600))
    assert thin and thin[0].kind == "section-thin" and "100" in thin[0].imperative
    assert not guards.section_size("Methods", "word " * 500, (400, 600))
    assert not guards.section_size("Methods", "word " * 500, (0, 0)), "no band, no finding"


# ── figures are numbered across the document, not within a section ───────────

def test_two_figures_numbered_one_in_different_sections_is_caught():
    """Exactly what shipped: the lattice diagram in Methods and the recovery landscape in
    Results both captioned "Figure 1", with three prose references pointing at the wrong
    plot. Per-section, counting from 1, both were correct."""
    cap_m = "chords as quarter-notes on a 1D lattice covering four bars of 4/4 time"
    cap_r = "distance to the target phrase over tolerance x radius, blue closer to the phrase"
    methods = f"Figure 1 shows the lattice.\n\n![Figure 1: {cap_m}](a.png)"
    results = f"Figure 1 shows recovery.\n\n![Figure 1: {cap_r}](b.png)"
    assert not guards.figure_findings(methods, start=1)
    assert not guards.figure_findings(results, start=1), "the old blind spot"
    bad = guards.figure_findings(results, start=2)
    assert [f.kind for f in bad] == ["misnumbered-figure"]
    assert "Figure 2" in bad[0].imperative
    # The prose reference is caught on the NEXT round, once the caption reads Figure 2 and
    # the sentence still points at Figure 1 — the imperative asks for both at once, and the
    # repair loop runs twice, so the pair converges.
    renumbered = results.replace("![Figure 1:", "![Figure 2:")
    assert [f.kind for f in guards.figure_findings(renumbered, start=2)] \
        == ["unintroduced-figure"]


def test_the_document_pass_checks_figure_numbering():
    assert "figure_findings(assembled)" in inspect.getsource(paper._manuscript_findings)


def test_the_figure_offset_follows_document_order_not_write_order():
    """write_order drafts Results before Background; the figure numbers must not."""
    src = inspect.getsource(paper._draft_paper)
    assert "figure_start[heading] = running" in src
    assert "_outline_figure_count(sections[heading])" in src


# ── citation padding ─────────────────────────────────────────────────────────

def _paras(md: str):
    return guards.parse_paragraphs(md)


def test_the_same_source_list_recited_is_padding():
    """26 of draft 8's 48 bracket groups were this."""
    p = ("## Background\n\nOne claim [@a; @b]. Two claim [@a; @b]. Three claim [@b; @a]. "
         "Four claim [@a; @b].")
    out = guards.padded_citations(_paras(p))
    assert out and out[0].kind == "padded-citation"
    assert "3 more time(s)" in out[0].imperative


def test_reordering_the_keys_does_not_make_it_a_new_citation():
    p = "## Background\n\nOne claim [@a; @b; @c]. Two claim [@c; @a; @b]."
    assert guards.padded_citations(_paras(p))


def test_genuinely_different_sources_are_not_padding():
    p = "## Background\n\nOne claim [@a; @b]. Two claim [@c]. Three claim [@a; @d]."
    assert not guards.padded_citations(_paras(p))


# ── the abstract ─────────────────────────────────────────────────────────────

def test_a_long_abstract_is_caught():
    """Drafted by its own prompt after the body, and the one block section_lengths skips —
    so its stated limit was the only thing holding it, and nothing checked."""
    md = "# T\n\n## Abstract\n\n" + ("word " * 358) + "\n\n## Introduction\n\nText."
    out = guards.abstract_length(md, limit=225)
    assert out and out[0].kind == "abstract-long"
    assert "358" in out[0].imperative and "133" in out[0].imperative


def test_an_abstract_at_its_limit_is_fine():
    md = "# T\n\n## Abstract\n\n" + ("word " * 225)
    assert not guards.abstract_length(md, limit=225)


def test_the_abstract_is_found_where_the_assembler_actually_puts_it():
    """_assemble writes a BOLD LABEL, not a heading — a ## Abstract would be numbered "1.
    Abstract" by the reference doc. A guard that looked among the document's sections found
    nothing and reported clean, which is how the 358-word abstract passed a check written
    for it. This is the guard-blind-spot bug in miniature: looking in the wrong place is
    indistinguishable from finding nothing wrong."""
    md = "# T\n\n**Abstract**\n\n" + ("word " * 358) + "\n\n## Introduction\n\nText.\n"
    assert paper._assemble("T", "x", []).count("**Abstract**") == 1, "the shape under test"
    assert len(guards.abstract_body(md).split()) == 358
    out = guards.abstract_length(md, limit=225)
    assert out and out[0].kind == "abstract-long"


def test_the_abstract_body_stops_at_the_first_section():
    md = "# T\n\n**Abstract**\n\nOne two three.\n\n## Introduction\n\n" + ("word " * 900)
    assert guards.abstract_body(md).split() == ["One", "two", "three."]


def test_a_release_has_lost_the_stars_and_the_label_still_reads():
    """release_markdown renders the accepted .docx body as plain paragraphs, so by the time
    a RELEASE is read the bold **Abstract** label is the bare line "Abstract". The
    submission wrapper read one, found no label, and shipped "Placeholder abstract" behind
    a TODO on a paper whose abstract had been written, measured and approved three rungs
    up. A line that says only "abstract" is the label."""
    md = "# T\n\nAbstract\n\nOne two three.\n\n## Introduction\n\nText.\n"
    assert guards.abstract_body(md).split() == ["One", "two", "three."]


def test_the_abstract_limit_reaches_the_document_pass():
    assert "abstract_length(assembled, abstract_limit)" in inspect.getsource(
        paper._manuscript_findings)


# ── sentences pasted between sections ────────────────────────────────────────

def test_a_sentence_repeated_across_sections_is_caught():
    """Five of these shipped, restating the same three numbers in Results, Discussion and
    Conclusion — roughly 150 words of a manuscript already over its cap."""
    s = "The mean advantage across the sweep is negative, so it does not beat chance."
    md = f"# T\n\n## Results\n\n{s}\n\n## Discussion\n\n{s}\n"
    out = guards.echoed_sentences(md)
    assert out and out[0].kind == "echoed-sentence"
    assert "Results" in out[0].where and "Discussion" in out[0].where


def test_a_restated_finding_in_different_words_is_allowed():
    md = ("# T\n\n## Results\n\nThe mean advantage across the sweep is negative overall.\n\n"
          "## Discussion\n\nAveraged over the space, local preference loses to no preference.\n")
    assert not guards.echoed_sentences(md)


def test_a_short_repeated_phrase_is_not_an_echo():
    md = "# T\n\n## Results\n\nThis is clear.\n\n## Discussion\n\nThis is clear.\n"
    assert not guards.echoed_sentences(md)


def test_the_references_list_is_not_an_echo_source():
    """A bibliography legitimately repeats; it is generated, not written."""
    e = "Smith, J. 2020. A Paper About Things That Are Interesting. Journal of Things."
    md = f"# T\n\n## References\n\n{e}\n\n{e}\n"
    assert not guards.echoed_sentences(md)

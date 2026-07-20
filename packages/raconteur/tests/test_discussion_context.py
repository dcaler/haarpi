"""What each section is handed, and in what order the sections are written.

Sections are WRITTEN in dependency order and ASSEMBLED in document order. A section that
interprets or restates is given the thing it interprets — in the words the paper actually
used — rather than a second reading of the same source material.

    Background    <- the literature
    Methods       <- the methods writeup, plus the literature for the method's provenance
    Results       <- the results digest
    Discussion    <- the literature + Results AS WRITTEN
    Conclusion    <- Results + Discussion AS WRITTEN
    Introduction  <- the narrative's motivation + Conclusion AS WRITTEN

Two defects this pins. The Introduction was asked to preview the result and given no
results at all — it previewed findings it had never seen. And the Conclusion was handed the
literature and the results digest, the same raw inputs as a 900-word Discussion, then asked
for two paragraphs; it came back at 682 words, then 784, three drafts running.
"""

from raconteur import guards, paper

LIT = "LITREV-BODY " * 200
CODE = "METHODS-WRITEUP " * 50
RES = "RESULTS-DIGEST " * 2000
NARRATIVE = "NARRATIVE-SPINE " * 20


def ctx(heading, written=None, narrative=""):
    return paper._context_for_section(heading, LIT, CODE, RES, written, narrative)


# ── the write order ──────────────────────────────────────────────────────────

def test_sections_are_written_in_dependency_order():
    doc = ["1. Introduction", "2. Background", "3. Methods", "4. Results",
           "5. Discussion", "6. Conclusion"]
    assert paper.write_order(doc) == [
        "2. Background", "3. Methods", "4. Results",
        "5. Discussion", "6. Conclusion", "1. Introduction"]


def test_an_unrecognised_section_is_written_last_and_depends_on_nothing():
    """budget_kind falls through to "other", which would make a venue-mandated "Data
    Availability" indistinguishable from a Discussion — and a Discussion is handed the
    Results as written. A keyword miss must not inherit another section's inputs."""
    doc = ["1. Introduction", "2. Data Availability", "3. Results"]
    assert paper.write_order(doc)[-1] == "2. Data Availability"
    assert paper._context_for_section("2. Data Availability", LIT, CODE, RES,
                                      {"results": "WRITTEN"}, "") == ""


# ── what each section draws on ───────────────────────────────────────────────

def test_background_gets_the_literature():
    assert "Literature review:" in ctx("2. Background")


def test_methods_may_cite_its_provenance_but_is_not_required_to():
    """The core method here is an offshoot of prior work, and that provenance belongs in
    the text. Citing is still not this section's job, so it carries no floor."""
    got = ctx("3. Methods")
    assert "LITREV-BODY" in got and "derives" in got
    assert "raster writeup" in got
    assert not guards.expects_citations(guards.budget_kind("3. Methods"))
    assert guards.may_cite(guards.budget_kind("3. Methods"))


def test_results_gets_the_digest_and_nothing_second_hand():
    got = ctx("4. Results")
    assert "Results Content:" in got and "RESULTS-DIGEST" in got
    assert "Literature review:" not in got


def test_discussion_reads_the_results_the_paper_actually_wrote():
    got = ctx("5. Discussion", {"results": "THE WRITTEN RESULTS"})
    assert "Literature review:" in got
    assert "AS WRITTEN" in got and "THE WRITTEN RESULTS" in got
    # not the raw digest a second time
    assert "RESULTS-DIGEST" not in got


def test_conclusion_restates_the_paper_rather_than_re_deriving_it():
    got = ctx("6. Conclusion", {"results": "THE WRITTEN RESULTS",
                                "other": "THE WRITTEN DISCUSSION"})
    assert "THE WRITTEN RESULTS" in got and "THE WRITTEN DISCUSSION" in got
    # the raw material is deliberately withheld: it is what made this a third Discussion
    assert "Literature review:" not in got
    assert "RESULTS-DIGEST" not in got
    assert not guards.expects_citations(guards.budget_kind("6. Conclusion"))


def test_the_introduction_previews_the_conclusion_the_paper_reached():
    got = ctx("1. Introduction", {"conclusion": "THE WRITTEN CONCLUSION"},
              narrative=NARRATIVE)
    assert "THE WRITTEN CONCLUSION" in got
    assert "NARRATIVE-SPINE" in got          # the motivation, in the author's framing
    assert "Literature review:" in got


def test_a_section_written_before_its_dependency_simply_has_none():
    """The order guarantees this does not happen; the context must not crash if it does."""
    got = ctx("6. Conclusion", {})
    assert "AS WRITTEN" not in got


def test_a_section_with_no_material_is_empty():
    assert paper._context_for_section("5. Discussion", "", "", "", {}, "") == ""

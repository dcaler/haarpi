"""Two-tier consumption of the literature review: tier-1 thread index, tier-2 harvest, and the
inspectable sidecar. raconteur used to swallow a blind 12k-char prefix of the review; now it
sees every thread's thesis (untruncated) and leaves a record of which threads fed the paper.
"""

from __future__ import annotations

import json
import types

from raconteur import context, harvest, outline

# A review as rabbitHole writes one: a title, a "Narrative Review" wrapper, thesis-headed
# threads, then the annotated bibliography (which is NOT a thread and must be excluded).
REVIEW_MD = """\
# Literature Review: replication of agent-based models

## Narrative Review

## Standardized documentation enables replication
Shared protocols make a model legible to a second team [@grimm2006; @grimm2010]. Later work
tightened the schema [@grimm2020].

## Cross-language translation requires structural equivalence
A port is faithful only when its state transitions match the original [@edmonds2019]. Docking
verifies that alignment [@axtell1996].

## Annotated Bibliography

### Cited in the review
- [@grimm2006] Grimm et al. (2006). The ODD protocol.

### Additional curated sources
- [@wilensky2015] Wilensky (2015). NetLogo.
"""


def _write_review(tmp_path):
    d = tmp_path / "litReview" / "output"
    d.mkdir(parents=True)
    (d / "260810_pydsk_litreview_ra.md").write_text(REVIEW_MD, encoding="utf-8")
    return tmp_path


# ── tier 1: thread parsing ────────────────────────────────────────────────────

def test_threads_are_the_narrative_only_not_the_bibliography(tmp_path):
    threads = context.load_litreview_threads(_write_review(tmp_path))
    assert [t.heading for t in threads] == [
        "Standardized documentation enables replication",
        "Cross-language translation requires structural equivalence"]
    # the wrapper "Narrative Review" (no prose) and everything from the bibliography on are gone


def test_a_thread_carries_its_thesis_and_citekeys(tmp_path):
    threads = context.load_litreview_threads(_write_review(tmp_path))
    t = threads[0]
    assert t.thesis == "Shared protocols make a model legible to a second team."   # citekeys stripped
    assert t.citekeys == ("grimm2006", "grimm2010", "grimm2020")                   # ordered, de-duped


def test_a_flat_review_yields_no_threads_so_callers_fall_back(tmp_path):
    d = tmp_path / "litReview" / "output"
    d.mkdir(parents=True)
    (d / "flat.md").write_text("# Review\n\nOne long unstructured essay with no headings.\n")
    assert context.load_litreview_threads(tmp_path) == []


def test_the_index_is_one_compact_line_per_thread(tmp_path):
    threads = context.load_litreview_threads(_write_review(tmp_path))
    idx = context.litreview_index(threads)
    lines = idx.splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("- Standardized documentation enables replication — ")
    assert "@grimm2006" in lines[0] and "@grimm2010" in lines[0]


# ── tier 2: the harvest coverage ──────────────────────────────────────────────

def test_harvest_instruction_is_empty_without_threads():
    assert harvest.harvest_instruction([]) == ""
    assert "litrev_harvest" in harvest.harvest_instruction(["t"])


def test_coverage_binds_valid_headings_and_sweeps_the_rest_to_unused(tmp_path):
    threads = context.load_litreview_threads(_write_review(tmp_path))
    parsed = {"litrev_harvest": {
        "by_pillar": {"Documentation": ["Standardized documentation enables replication"],
                      "Ghost pillar": ["A heading the model invented"]},   # invented → dropped
        "unused": []}}
    by_pillar, unused = harvest._coverage(threads, parsed)
    assert by_pillar == {"Documentation": ["Standardized documentation enables replication"]}
    # the invented heading is gone, and the unbound real thread falls to unused — coverage is total
    assert unused == ["Cross-language translation requires structural equivalence"]


def test_coverage_is_total_even_with_no_harvest_field(tmp_path):
    threads = context.load_litreview_threads(_write_review(tmp_path))
    by_pillar, unused = harvest._coverage(threads, {})
    assert by_pillar == {}
    assert unused == [t.heading for t in threads]         # nothing bound → everything unused


# ── the sidecar ───────────────────────────────────────────────────────────────

def test_sidecar_writes_both_tiers_and_the_analysis(tmp_path):
    threads = context.load_litreview_threads(_write_review(tmp_path))
    parsed = {"contribution": "a faithful port method",
              "litrev_harvest": {"by_pillar": {
                  "Documentation": ["Standardized documentation enables replication"]},
                  "unused": []}}
    out = tmp_path / "paper"
    path = harvest.write_sidecar(out, "outline", threads, parsed)
    assert path == out / "outline.harvest.md"
    text = path.read_text()
    assert "## Tier 1 — thread index" in text
    assert "## Tier 2 — harvest" in text
    assert "### Documentation" in text                    # the bound pillar
    assert "Cross-language translation requires structural equivalence" in text  # under Unused
    assert '"contribution": "a faithful port method"' in text                    # analysis embedded


def test_sidecar_is_skipped_when_there_are_no_threads(tmp_path):
    assert harvest.write_sidecar(tmp_path, "outline", [], {"contribution": "x"}) is None
    assert not (tmp_path / "outline.harvest.md").exists()


# ── the chokepoint: fallback vs the two-tier path ─────────────────────────────

def _brain(analysis_json: str):
    return types.SimpleNamespace(coordinator=lambda *a, **k: analysis_json)


def test_analyze_without_threads_keeps_the_original_prefix_path():
    """No threads → the old truncated-prefix behaviour, no harvest, no sidecar (and no crash)."""
    out = outline._analyze_structure(_brain('{"contribution": "x"}'),
                                     "desc", litrev="some review prose", code="", results="")
    parsed = json.loads(out.split("\n\n", 1)[1])
    assert "litrev_harvest" not in parsed


def test_analyze_with_threads_writes_the_sidecar(tmp_path):
    threads = context.load_litreview_threads(_write_review(tmp_path))
    analysis_json = json.dumps({
        "contribution": "x",
        "litrev_harvest": {"by_pillar": {
            "Docs": ["Standardized documentation enables replication"]}, "unused": []}})
    out = tmp_path / "paper"
    outline._analyze_structure(_brain(analysis_json), "desc", litrev="ignored when threaded",
                               code="", results="", threads=threads, sidecar=(out, "outline"))
    assert (out / "outline.harvest.md").exists()

# The fact base, archived (2026-11-13)

The fact base was rabbitHole's second report format (`report_format: facts`): no prose, a few
facts per source with verbatim quotes and pages, scored 0–3 against the approved topics, and
assessed on a review page rather than by redlining the docx. Claim grounding (`ground.py`,
`gate.py`, `PLAN_claim_grounding.html`) replaced it as the way a literature review is built.
It is archived, not deleted, so it can come back. Nothing here is imported or tested.

The tag **`factbase-retired`** (local) is the whole tree as it was just before the retirement.
Restoring from the tag is the sure route; this folder is for reading.

## What is here

| file | what it did | what called it |
|---|---|---|
| `facts.py` | the original, whole (1550 lines): extraction, quote checking, topic scoring, theme grouping, the docx/md fact-base report, `work/facts.json` | `summarize.run` (`report` on a fact base), `mindmap` (fact-base map), `review_page`, `fact_rework` |
| `fact_rework.py` | answered a fact-base comment by re-running the cached report: re-extract one source, scope a topic and re-score, quarantine a source | `haarpi.planner` (`_fact_rework`, the fact-base rework chain, a paper dispute sent back to the source) |
| `review_page.py` | the fact base's review page (`…_review_ra.html`): verdicts per question and topic, spot-check, embedded map; `rabbitHole reviewpage` | `report` (via `facts.py`), the `reviewpage` verb in `cli.py` |
| `tests/test_fact_base.py`, `tests/test_fact_rework.py`, `tests/test_review_page.py` | rabbitHole's tests for the three modules | — |
| `tests/test_haarpi_fact_base.py` | haarpi's tests of the planner's fact-base code: the fact-base rework chain, the fact kinds in `_normalise_tasks`, the facts snapshot beside a release | — |

## What stayed live

- `rabbithole/facts.py`, trimmed to the helpers the grounding uses: page texts, `find_quote`,
  `locate_passage` and its widening, the reported-work patterns, `resolve_topics`,
  `_SCORE_PROMPT`/`topics_signature`/`_parse_scores` (per-claim topic scores), `page_link`.
- `topics` (the research questions and topics page) and its opening-cycle steps.
- raconteur still reads a `<release>.facts.json` snapshot frozen with a release made before the
  retirement, when the project has no claims release (`raconteur/factbase.py`). It no longer
  reads an unreleased live `work/facts.json`.
- `report_format: facts` still parses; `report` and `mindmap` refuse it with
  `summarize.FACT_BASE_RETIRED`.

## What was removed from live code

- rabbitHole: the `reviewpage` verb; the fact-base branches of `summarize.run` and `mindmap`.
- haarpi planner: `_fact_chain`, `_is_fact_base`, `facts_snapshot_name`, `_snapshot_facts`,
  `_focus_items`, `_fact_rework`, `_FACT_NEEDS`, `_FACT_DECOMPOSE_PROMPT`, and the `fact_base`
  parameter of `decompose`, `_normalise_tasks` and `chain_from_tasks`. A litreview docx is no
  longer read as markup. A paper dispute at the source no longer sends the fact back for a
  re-read (`_apply_disputes`). The upstream-literature tier now ends in `cards-plan`.

## To bring it back

1. `git diff factbase-retired -- packages/rabbithole packages/haarpi packages/raconteur` shows
   every change; `git checkout factbase-retired -- <path>` restores a file.
2. Move `facts.py`, `fact_rework.py` and `review_page.py` back into `rabbithole/rabbithole/`
   (the archived `facts.py` replaces the trimmed one), and the tests back to their packages.
3. Restore the planner code above, the `reviewpage` verb, the fact-base branches in
   `summarize.py` and `mindmap.py`, and raconteur's live-sidecar read in `factbase.find`.

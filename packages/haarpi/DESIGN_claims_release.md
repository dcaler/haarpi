# The claims release: what rabbitHole hands raconteur

Agreed 2026-11-13, when the FP claim-grounding run became the rabbitHole procedure
(`PLAN_claim_grounding.html`). It replaces the fact base (`<release>.facts.json`) as the
contract between the two. raconteur does not import rabbitHole; this file is all it reads.

## Where it lives

rabbitHole's **release** step (procedure step 10) writes it, and only after a passed gate:

    {litrev_dir}/output/{YYMMDD}_{project}_release_ra.claims.json

The newest `*.claims.json` in `output/` is the current release. A later release supersedes
it, and the usual staleness propagation marks drafts built from the older one. raconteur
never reads rabbitHole's `work/`: an unreleased claim can't reach a draft.

## Shape

```json
{
  "format": "claims-release/1",
  "project": "FirmPathways",
  "released": "2026-11-20",
  "gate": {"file": "261113_FirmPathways_gate_DCR.json", "outcome": "pass",
           "sample": 25, "wrong": 0},
  "questions": ["How do cleantech firms navigate ...?"],
  "topics": [{"name": "Sequencing of funding sources", "serves": [1]}, "..."],
  "sources": {"gaoInnovationFirmSurvival2023": {"label": "Gao, 2023",
              "title": "...", "pdf": "zotero://open-pdf/library/items/TRQZD5ES"}},
  "claims": [
    {"id": "gaoInnovationFirmSurvival2023#3",
     "citekey": "gaoInnovationFirmSurvival2023",
     "claim": "Innovation activities in the early stage of start-ups are associated with ...",
     "role": "finding",
     "passages": [{"page": 9, "pages": [9, 9], "text": "The results suggest that ..."}],
     "scores": [0, 3, 2, 0, 0, 1, 0, 0, 0, 0, 0],
     "origin": "llama3.3_latest|gaoInnovationFirmSurvival2023|3"}
  ],
  "credited": [{"idea": "...", "reported_by": "abdel-latifRise2018", "credited_to": "..."}]
}
```

- **`claims`**: every confirmed claim (every check passed, minus any the gate marked wrong).
  - `id` is `{citekey}#{n}`, the same form as a fact id, so raconteur's id stripping and
    trace work unchanged. `origin` is the card's own id.
  - `role` is `finding`, `proposal` or `synthesis`, or `null` where the two verifiers
    disagreed.
  - `passages` are the paper's own words that the claim was checked against, with the page
    holding most of each one and the pages it touches.
  - `scores` holds a 0–3 relevance per topic (3 central, 2 useful), aligned with `topics`.
- **`topics` and `questions`**: the project's, as in its `litrev` config.
- **`sources`**: one entry per cited paper, giving its "Author, Year" label and PDF link.
- **`credited`**: borrowed ideas the credit check gave to their originals (step 8). They are
  recorded for the record; raconteur cites the original, which is in `claims` under its own
  citekey.

## How raconteur reads it (`raconteur/factbase.py`)

- A claim becomes a `Fact`:
  - `text` is the claim; `quote` the passages joined; `page` the first passage's page;
  - `link` is the source's PDF at that page;
  - `weight` is the best score on a topic that serves a question;
  - `role` is the role.
- Only claims with a weight of 2 or more count, as with facts.
- Themes are the topics, with no synthesis bullets: the procedure writes none.
- **The role sets how firmly a claim is stated, never who it's attributed to.**
  - raconteur weaves one narrative through the facts and cites parenthetically (`[@key]`),
    with no "X finds" or "X argues".
  - A finding is stated as established.
  - A proposal is carried as a possibility or a line of argument, never as a result.
  - A synthesis speaks to the shape of a field.
  - After drafting, the trace flags a sentence that states a proposal without hedging.
- If a project has a claims release, raconteur reads it. Otherwise it reads the old fact base
  or the narrative review, as before. Those readers go once every project has been redone
  (task 1603).

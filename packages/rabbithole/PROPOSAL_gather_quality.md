# Proposal: Make rabbitHole `gather` find better papers

*Goal: get rabbitHole's candidate lists closer to the Claude lit-review skill's
`Collections_Claude.docx` — both by removing junk and, especially, by surfacing more
high-value (top-tier journal / well-cited / canonical) papers.*

---

## 1. Why the lists differ today

We have three projects with rabbitHole output (`candidates.docx`), Claude-skill output
(`Collections_Claude.docx`), and a `difference_analysis.md` for each.

- **consGateII** — 27 of 30 rabbitHole papers were off-domain (health-behavior literature,
  not recycling). 3 were predatory/low-tier journals. A PhD thesis, two book chapters, and
  a BMJ editorial were treated as peer-reviewed articles.
- **FirmPathways** — ~48% (24/50) off-topic, wrong geography, or not research papers at all
  (trade-press news, a conference *abstract* mistaken for the journal article, 3 "unknown
  authors" metadata failures). The definitive papers Claude found (Howell 2017 *AER*,
  Lerner 1999, Islam et al.) **don't contain the literal query words** — they need
  conceptual search, not keyword search.
- **SchellingChords** — *not* a failure. The two lists are nearly disjoint only because they
  were run against two different topic configs (`litrev.yaml` vs `litrev_2.yaml`). Both are
  good (rabbitHole's was 40% Tier-1/2). This confirms: when the topic string is crisp, the
  current pipeline already does respectably — the topic string + search strategy is the lever.

### Root causes (mapped to code)

| # | Failure | Where |
|---|---------|-------|
| 1 | **Single broad query → scatter.** One raw topic string is sent to each of 4 APIs. Causes domain drift *and* misses conceptually-relevant papers. | `discover.py:44-67` |
| 2 | **No relevance gate.** LLM relevance judge exists but is OFF by default; embedding floor is 0.0, so top-N is taken regardless of true fit. | `config.py:64-68`, `ranking.py:93-116`, `discover.py:120-123` |
| 3 | **No venue-quality filter.** Only MDPI is excluded; no predatory/low-tier blocklist. | `filters.py:23-32` |
| 4 | **No item-type filter.** News, editorials, abstracts, encyclopedia entries all accepted. | `models.py:45`, `ranking.py:33-41` |
| 5 | **No metadata gate.** "unknown authors" / misattributed-DOI records survive. | — |
| 6 | **Weak topic authoring.** A rambling request isn't sharpened into search-friendly terms. | `wizard.py:78-104` |
| 7 | **Prestige-blind ranking.** `_quality_weight` only knows journal/preprint/other (no tier); citations are just 5% of the score; snowball is shallow and pre-ranking. | `ranking.py:33-79`, `discover.py:85-93` |

**Your main concern — "not enough high-value papers" — is mostly #1, #6, and #7.**
Embedding similarity finds papers that *sound* like the topic, regardless of whether they
are field-defining, and the ranker has no notion of journal tier or seminal status.

---

## 2. The plan

Scope: **all of it (A–I)**. Gather brain: **configurable, default local Ollama**; since
Claude isn't enabled, a `claude`-requested run must warn and fall back to local, never crash.

### Core — reproduces Claude's "many targeted searches + screening"

- **A. Multi-query search via LLM decomposition.** Before searching, the coordinator
  generates ~6–8 targeted sub-queries (conceptual angles, methods, synonyms, domain
  anchors) from topic+focus. Keep the raw topic as one query, lower the per-query cap so
  total volume is similar, let `filters.dedupe` collapse overlap. Falls back to today's
  single-query behavior if generation fails. *New helper + call site at `discover.py:44`.*
- **B. LLM relevance/domain gate ON by default.** Default ranking to `method="llm"` with a
  hard floor (e.g. 6/10) so off-domain papers drop instead of filling the target. Strengthen
  the prompt to check domain match explicitly. *Reuses `ranking._llm_rerank` + the floor at
  `discover.py:120-123`.*

### Quick-win filters — cheap, deterministic, low-risk

- **C. Predatory/low-tier venue blocklist** — mirror the MDPI mechanism in `filters.py`
  with a conservative, user-extensible publisher/DOI-prefix set.
- **D. Item-type filter (chosen in the wizard).** What counts as an acceptable source is
  set at `init` time via a 4-way question (see F), stored in `litrev.yaml`, and enforced
  here. The four modes:
  1. **Peer-reviewed only** — journal-article, book, book-chapter, report/working-paper.
  2. **Peer-reviewed + preprints** — adds arXiv/preprints (still under the arXiv cap).
  3. **Peer-reviewed + news** — adds news / trade press.
  4. **Peer-reviewed + preprints + news** — all of the above.

  (These four are just the 2×2 of two flags, `include_preprints` and `include_news`, which
  keeps the yaml clean and hand-editable.) In every mode, junk types — editorial, erratum,
  abstract, encyclopedia, dataset — stay excluded. Default for a non-interactive run is
  mode 1.
- **E. Metadata-completeness gate** — drop records with no authors or no venue/publisher.

### High-value recall — directly targets "find more top-tier papers"

- **G. Quality-aware ranking.** Replace the binary `_quality_weight` with a venue/prestige
  signal (small, user-extensible `TOP_VENUES` set: Nature, Science, PNAS, PRE/PRL, AER, QJE,
  Research Policy, Cognition, NeuroImage, …) and raise the citation weight (~0.05 → ~0.15–0.20)
  so well-cited, real-journal papers rise to the *top* of the cut, not merely survive it.
  *`ranking.py:33-79`.* **Implemented additions:** (i) the citation term is now citation
  *velocity* — `cites / years-since-publication`, log-normalised — so a recent high-impact
  paper isn't buried under an older one that merely had more years to accrue totals (e.g.
  Trushna 2024 at 16/yr now outscores Rousta 2015 at 12.9/yr, where raw totals had Rousta 3×
  ahead); (ii) a **review bonus** (systematic review 1.0, review 0.6) added to both the
  embedding pre-sort and the post-LLM ordering; (iii) `_quality_weight` no longer drops
  `review`-typed papers below ordinary articles. *`ranking.py:_cites_per_year`, `_cite_score`,
  `_review_bonus`, `_quality_weight`, `rank`, `_llm_rerank`.*
- **H. Citation-trail recall for seminal work (strongest lever for your concern).**
  Following the citation graph is how you reach the well-cited backbone of a field that
  keyword search can't see. Run the snowball from the top *relevance-ranked* seeds (not the
  first 8 by list order), sort pulled references by `cited_by_count`, and add a dedicated
  "most-cited in this area" pass (OpenAlex `sort=cited_by_count:desc` — today it hard-codes
  `relevance_score:desc`). *`discover.py:85-93`, `sources.py:55-82,124-156`.*
- **I. Seminal-name query generation.** Part of A's prompt: explicitly ask the coordinator
  to emit known foundational authors / seminal works / named programs as sub-queries —
  mimicking how the Claude skill "knew the SBIR literature" to find Lerner 1999, Howell 2017.
- **J. Vocabulary feedback loop — learn the field's own terms from the citation graph.**
  Keyword search is locked to the vocabulary of the topic string: a paper that names the
  same behaviour with different words (e.g. "waste **segregation**" / "source separation"
  vs. "recycling" / "sorting") is unreachable no matter how the queries are phrased.
  After the first search + snowball, mine the *surface* vocabulary of the neighbourhood —
  recurring title/keyword terms from the citation-trail results and the user's own Zotero
  collection — diff it against the queries already run, and run **one more search round**
  on the missing terms. Two supporting changes to the snowball (H): seed it from the
  **curated library + most-cited** papers (not most-cited alone), so on-target
  neighbourhoods get explored; and pull forward citers by **recency as well as citation
  count**, so a recent high-value review isn't capped out below older work. *Implemented:
  `_vocabulary_queries` + `_zotero_collection_papers` in `discover.py`; two-sort citers in
  `sources.openalex_snowball`.*

  *Worked case — consGateII.* The best single resource (Trushna et al. 2024, a *Heliyon*
  systematic review of household-waste-**segregation** interventions, 48 cites) was
  unreachable: it ranks **#1** for "household waste segregation interventions" but is **not
  in the top 50** for the configured topic string. It cites Rousta 2015 (already in the
  corpus), so seeding the snowball from the library plus a recency-sorted citer pass pulls
  it in directly (it is the 23rd-most-cited of Rousta's 155 citers — below the old
  cites-only cap of 15, but caught by the recency pass); the vocabulary loop then broadens
  to the rest of the "segregation" cluster.

  *Cost.* Cheap. The citation snowball is pure OpenAlex HTTP (~3 calls/seed, batched, free
  pool — seconds against a 25–76 min run). The vocab loop adds ~1 coordinator call + one
  OpenAlex-only search round (~1–3 min). Crucially the LLM relevance gate is
  **target-bounded** — it scores only the top `max(target*2, 25)` candidates by embedding
  pre-sort — so a wider candidate pool does *not* enlarge the expensive step. The one cost
  trap is routing the extra round through Semantic Scholar (whose 429 backoff swung
  consGateII's runtime 25 → 76 min); the loop stays on OpenAlex by design.
- **K. Reviews as first-class targets *and* snowball seeds — especially early.** A review
  synthesises a field; a **systematic review / meta-analysis** is a screened, curated
  bibliography of it — the single best entry point and the highest-yield snowball seed,
  most of all when a project's collection is still thin. Three changes: (1) a dedicated
  **reviews discovery pass** (OpenAlex `type:review`), run both on the topic and on each
  *learned-vocabulary* term, so a field-defining review whose title uses different words
  (the "segregation" systematic review under a "recycling" topic) becomes reachable —
  relevance-sorted, so a recent on-target review isn't buried under older, more-cited but
  looser-matching ones; (2) **review-seeded snowballing** — reviews (systematic first) head
  the seed list and are snowballed *deeper* (`per_seed` 25 vs 15), because a review's
  reference list is a ready-made field bibliography; (3) the ranking review bonus from G,
  so once found they float up rather than get cut. Detection is conservative
  (`filters.is_review` / `is_systematic_review`: OpenAlex item type + title/abstract
  patterns). *`filters.py`, `discover.py` (reviews pass + seed selection),
  `sources.search_openalex` `extra_filter`.* The design self-adjusts to research stage:
  an empty collection means the snowball is review- and citation-seeded (reviews drive
  discovery early); a rich collection adds the curated library seeds for precision later.

### Upstream — deepest, touches config schema + wizard UX

- **F. Sharpen topic extraction + ask which source types to include.** Two wizard upgrades:
  - **Topic:** upgrade `wizard._extract_topic_focus` to use the coordinator (not the worker)
    and emit a crisp search-friendly topic plus a `domain_anchor` and `exclude_topics`. Add
    those two fields to `ProjectConfig` (default `""` so existing `litrev.yaml` still loads).
    They feed A (seed/negative terms) and B (the domain check).
  - **Source types:** add the 4-way question to the `init` flow (after the research
    description, alongside the target-count question), writing `include_preprints` /
    `include_news` into the yaml for D to enforce. Default selection = mode 1
    (peer-reviewed only).

  *`wizard.py:89-104` (extraction), `wizard.py:141-175` (`_first_run` flow),
  `config.py:51-72` (new fields).*

### Backend wiring

- Reuse `ProjectConfig.brain.backend` (or add an optional `gather_backend` falling back to
  it). In `discover.run`, build the gather `Brain` defensively: if `claude` is requested but
  no Anthropic key (or init raises), warn and construct a local-Ollama `Brain`. Net effect
  today: everything runs local.

---

## 3. Honest expectation-setting

- A–F as originally drafted were weighted toward *precision* (removing junk). Your concern
  is *recall of high-value work*, so **G, H, and I were added specifically for that.**
- Of those, **H (citation-trail + most-cited pass) is the strongest mechanical lever** —
  it pulls canonical, well-cited papers in by graph structure even when their titles don't
  match your keywords.
- **A and I** improve recall of conceptually-relevant high-value papers; **G** ensures the
  prestige that's found floats to the top instead of being buried.
- **B** is precision only — it will not, by itself, find more top-tier papers (and could
  even drop a famous-but-tangential one). It's in the plan to fix the off-domain drift, not
  the prestige gap.

---

## 4. How we'll verify

For the existing projects, **re-run `rabbitHole init` first** (planned) so they pick up F's
sharpened topic + `domain_anchor`/`exclude_topics` and the 4-way source-type selection;
this writes a new `litrev_N.yaml` and leaves the old one as history. Then re-run
`rabbitHole gather` on consGateII and FirmPathways and compare the new `candidates.docx` to
the existing `Collections_Claude.docx`:

- **consGateII:** ≥80% of papers on-domain (recycling/waste), 0 predatory venues, 0 non-article types.
- **FirmPathways:** sharp drop from the ~48% off-topic rate; 0 trade-press/abstract items; 0 "unknown authors".
- **High-value check (the real test of G/H/I):** confirm the canonical anchors Claude had
  (e.g. Howell 2017 *AER*, Lerner 1999) now appear in the rabbitHole list.
- **Top-tier metric:** compare venue-tier and citation profile (count of Tier-1 venues,
  median citation count) against the Claude lists.
- **Regression:** SchellingChords' list is already good — the changes should not degrade it.
- **Cost/runtime:** confirm `gather` with the LLM gate on local Ollama stays within an
  acceptable wall-clock budget, and that a `claude`-requested run with no key warns and
  completes locally.

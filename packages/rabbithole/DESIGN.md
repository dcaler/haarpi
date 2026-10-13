# RAbbithole — Design

*Offline-first Research Assistant: literature reviews + annotated bibliographies,
driven by local LLMs via Ollama.*

---

## Goals

- Produce literature reviews and annotated bibliographies at the quality of the
  Claude `lit-review` skill, with **local LLMs doing all the reasoning, reading,
  and writing** — the web is used only as a data source.
- Run **headless** on a server with Ollama. No browser anywhere.
- Be **portable**: a new topic is a new empty folder + one command.
- Let the "brain" be **swapped between local Ollama and the Claude API** for A/B
  comparison.

## Hard constraints

- **No Chrome / browser automation.** Headless only.
- **Zotero via Web API only** (no desktop app, no MCP). Create collections + read
  PDFs/metadata through the API. Manual PDF upload by the user is fine; rabbitHole
  reads them back.
- **Output Markdown → .docx** (pandoc).
- **Never include MDPI**; every cited source must have a real full-text PDF.

---

## Key decisions & rationale

| Decision | Why |
|---|---|
| **Python orchestrates; LLM does narrow tasks** | LLM-driven agent loops lose state. Python owns I/O, naming, looping, checkpointing. |
| **Swappable "brain" behind one interface** | A/B (all-local vs Claude-coordinator) is a config flag, not a rewrite. |
| **Two roles: coordinator (heavy) + worker (small)** | Large models run one-at-a-time; small models handle embedding and fallback tasks. Workers stay local even when the coordinator is Claude, so paper-reading is always free. |
| **Free APIs for discovery** | OpenAlex/Crossref/Semantic Scholar/arXiv are headless, free, structured, and give citation/OA data. Citation-trail snowballing via OpenAlex expands coverage. |
| **Zotero Web API, read-only for files** | Only *create collections* and *read* PDFs/text; the human uploads. |
| **One collection per project, by key** | The project's collection is pinned in `haarpi.yaml` (`zotero_collection:`) and adopted by every review. A configured key that Zotero no longer has is an error, never a reason to create a replacement; a collection is created only on a project's first gather, and pinned there. Name lookups ignore case, read every page, and refuse when two collections match. gather used to find its collection by name, case-sensitively, and create one on a miss — DigiPros' corpus split twice (`DigiPros_old`, `DigiPros_lessOld`) as its name drifted between "digipros" and "DigiPros". gather also warns when other collections' names look like the project's. |
| **ChromaDB for semantic retrieval** | Long papers are indexed in chunks; the coordinator reads via targeted RAG retrieval instead of brute-force condensing. The locate step uses pure embedding retrieval (no LLM call). |
| **Post-synthesis bibliography** | Annotated bibliography entries are a function of the *finished review* — locate runs after synthesis, over the whole curated corpus. |
| **Citation-integrity guard** | Local models hallucinate citations; every in-text cite is checked against the corpus. |
| **The corpus is the foundation, not a menu** | Every curated source is cited or explicitly rejected with a reason (`work/disposition.json`). A small citation count is a defect, not a stylistic choice — silence is not a decision. |
| **Guards in Python, judgement in the LLM** | Breadth, density, minimality, and verifiability are all mechanically checkable. `guards.py` decides *that* something is wrong and states it as an imperative; the LLM is asked only what code cannot decide. |
| **A paragraph is an atom stream** | Equations and hyperlinks are siblings of the text runs, not inside them. Modelling a paragraph as `w:t` text alone strands them and collapses every sentence diff to a whole-paragraph rewrite. |

---

## Pipeline

Two commands with one human gate in the middle; both are repeatable:

```
rabbitHole gather   ── machine ──▶ candidates.md  (only what's missing from Zotero)
   (you download PDFs, add them to the Zotero collection)   ◀── human gate
rabbitHole report   ── machine ──▶ <project>_litreview_<brain>.{md,docx}
   (you comment on the .docx)                              ◀── human gate
rabbitHole revise   ── machine ──▶ the same document, redlined in place
rabbitHole graft    ── machine ──▶ the same document, plus ONE new section
```

### Three ways to rework a draft, in ascending cost

| Verb | Touches | When |
|---|---|---|
| `revise` | the paragraphs a comment is anchored to | the ask can be met by editing what is there |
| `graft` | nothing existing — inserts a new section | the ask is for a strand the review lacks |
| `report` | every section, re-planned from the corpus | the review is aimed wrong (a redirect) |

Only `report` costs the reviewer a second full read, and only a genuine change of direction
reaches it. `graft` drafts the requested strand and splices it into a copy of the reviewer's own
.docx as a tracked insertion: existing paragraphs are never passed to a model, so they come
through byte-identical and every comment thread survives. Its insertion point is the requesting
comment's own anchor where one survives, the nearest section by embedding otherwise, and the end
of the review as a last resort that is always reported rather than taken quietly.

### report — three stages

| Stage | What happens | LLM call |
|---|---|---|
| **1. Read** | Per-paper notes (argument/methods/findings/themes). ChromaDB indexes each paper; long papers retrieved via 3-query semantic retrieval rather than brute-force condensing. Incremental — resumes from `work/annotations/`. | coordinator × N papers |
| **2. Synthesise** | Sectioned — see below. | coordinator × (1 + ~4 per section) |
| **3. Locate** | For each curated source: embed claim sentences → retrieve the best chunk not already quoted → extract quote + page location, and score the quote against the claim (embedding cosine; under 0.55 the bullet is labelled *weak support*). An uncited source is grounded from its note's fields, each its own statement. Claims are stored **whole** (the bibliography truncates for display); a claim is never dropped for colliding on a chunk. No LLM call — pure embedding arithmetic. Incremental — resumes from `work/located/`, which carries a version so a change to what is located re-grounds the cache. | none (embedding only) |

### The annotated bibliography's three tiers

**Cited in the review** (narrative-linked claims), **Additional curated sources** (grounded via
their own text), and **Screened out of the curated list**. The third tier exists because a review
is read to judge whether the corpus is right, so presenting a source as *curated* is a claim that
it belongs — and one whose own annotation reads "largely irrelevant to the specific review focus"
makes that claim false. Uncited sources that disqualify themselves in their own words move to a
named list. They stay in the corpus and in Zotero: the tier records a judgement, it deletes
nothing. The test is deliberately literal and few; anything subtler is a judgement the rejection
ledger already demands a reason for.

The screen never overrules a better-informed call. A source the corpus audit judged a
*transfer* on a named shared term (Namier on "prosopography"), or one a person locked into the
corpus, is not screened, whatever its note says. A transfer with no term is only the audit's
default and exempts nothing. `is_foundational` is deliberately not an exemption: a note that
only says "no quantitative findings" qualifies, and so does a news piece.

### Coverage against the brief

The narrative can only ever answer the coverage question *yes*: it is written to connect every
source to the brief, and where nothing does, the drafter writes the connection in. So the
load-bearing block ends with the *no*. For each focus item (the brief's `;`-separated clauses)
it reports how many sources sit close to it (embedding of the source's own note ≥ 0.70), which
of the item's two-word terms any source's own notes use, and how many review sentences attribute
those terms to sources that never use them. An item with fewer than 3 close sources, or a term
no source uses, is **THIN**.

A source's *own* notes are its argument, methods, findings, limitations and themes. Its
relevance and gaps lines are left out because both are written against the brief: a gaps line
names a brief term to say the paper lacks it. The list rides inside the load-bearing block under
a bold label, not a heading, because `redline.replace_top_sources` rebuilds that block up to the
next heading: every path that re-ranks the sources re-checks coverage, and none strands a copy.

### The fact base (`report_format: facts`, on test)

An opt-in alternative to the narrative, per project (DigiPros first, 2026-10-09). The narrative
spent ~4 of its ~4.5 hours drafting and polishing prose; the polish fought itself (guards asked for
more sources and the reviser re-cited familiar ones with their numbers), and the paper stage read
only a 12,000-character prefix of the result, never the bibliography where the facts were.

| Step | What happens | LLM |
|---|---|---|
| **extract** | up to 10 atomic facts per paper: the fact, its kind, its page, a verbatim quote. The model is NOT told the review's topic: told it, it answered `[]` for off-brief papers (11 on DigiPros, 2026-10-10) — relevance is `score`'s job | coordinator × N papers (cached in `work/facts/`, versioned) |
| **verify** | the quote is searched for in the paper's own page-marked text (case, whitespace, ligatures, line hyphenation, soft hyphens and quote marks ignored). Found → **verbatim**, and the page is where it was found. Not found → located by embedding search, with a support score, and the model's own page and quote kept (`claimed_page`, `claimed_quote`) to measure looser matching later. **Only verbatim facts count**: a search-placed one is shown in the by-publication list, labelled, and carries no weight, coverage or topic placement — of 30 DigiPros ones at support 0.55–0.75 about 6 supported their fact, and the cosine did not tell which | none |
| **score** | every fact scored 0–3 against every approved **topic** (0 none, 1 background, 2 useful, 3 central) | 1 call per paper (cached in `work/facts/scores/`, keyed by the topic list) |
| **group** | facts scoring 2+ on a topic, then cosine k-means into themes (~10 facts each, ≤8 per topic) | 1 short call per theme: a name and 2–4 synthesis bullets |
| **render** | topics, coverage per research question, the contribution map (embedded), load-bearing sources, facts by topic and theme, every source's facts in full | none |

**Topics, not research questions, are what facts are scored against.** The review's config holds
`topics:`, a person-approved list of single subjects, each with the research questions (focus
items) it serves. Tagging facts against the research questions themselves failed: several are
compound ("the factoid model *applied to simulation output*"), and read strictly the model dropped
core sources (Pasin & Bradley went from 10 tagged facts to 0) while read loosely everything drifted
to the broadest question. Scoring against single subjects was stable: on 12 DigiPros sources
scored twice, 93% of scores were identical and every difference was one point. Coverage per
research question is built from its topics, so a two-subject question shows which half is missing;
a question no topic serves is THIN by definition. Without `topics:`, each focus item stands in as
its own topic, with a warning. The topic list is meant to be approved before `gather` (it is what
gets searched for); that review step is not yet in the pipeline.

**Each fact links to its page:** `zotero://open-pdf/library/items/<attachment key>?page=N` (or
`groups/<id>/…`) opens Zotero's own copy at that page; a source with no PDF in Zotero falls back
to its DOI. Pages are the PDF's physical page index, as the `[p.N]` markers are.

**A synthesis bullet may cite only facts in its own theme**, and must cite something. A bullet
that reaches outside is dropped and counted in the log, so a bridge to a source that never said
it is impossible here rather than flagged. The matcher was checked on 487 quotes already taken
verbatim from DigiPros' papers: 483 found on the recorded page; the 4 misses were fragments.

Importance (load-bearing list and map rings) sums, over each source's verbatim facts, the best score on a topic **that serves a research question**; a topic serving none (DigiPros'
Schelling and Sugarscape search topics) is scored and shown but weighs nothing. The list ranks by
that weight, then by how many research questions the source serves, then verbatim facts. It used to
rank breadth across topics first, and on DigiPros (2026-10-10) that filled the top with
ABM-methods generalists and Schelling papers while no prosopography source made it — a specialist
touches two topics however central it is. A
source with no fact scoring 2+ on any topic is listed last, as citations only — this replaces the
self-exclusion screen. `work/facts.json` carries every fact
with its link, for tools; `mindmap` reads it when the draft says `*Format:* fact base`. The report
draws and embeds the map itself, so `haarpi next` queues no separate `mindmap` after a fact-base
`report`.

**A source with under 2,000 characters of text is not sent to the model.** It is an image-only
scan or a cover page (Bulst 1986: 54 characters; Cioffi-Revilla 2010: an SSRN cover), the model can
only answer nothing, and it used to fail silently as "no facts extracted". It is listed under
*Sources with no usable text* as needing OCR or a better PDF, and recorded in the sidecar.

**Retrieval skips unreadable chunks.** A long paper is read as its best few chunks per query; each
query now takes them from a wider pool, skipping chunks under 200 characters or with almost no
function words (OCR noise). Namier's 700-page *House of Commons* had been read as 12 chunks of
front matter — the scan's cover, "Digitized by the Internet Archive", the title page — because
short chunks match an "introduction / preface" query best. The notes share this retrieval.

**Rework is routing, never rewording.** `haarpi next` sorts each comment on a fact base into a
kind (`fact`, `pdf`, `drop`, `topic`, `topic_scope`, `sources`, …; the table is
`haarpi/comment_kinds.py`), and `fact_rework.apply` invalidates exactly what it names before
the chain is queued: a `fact`/`pdf` comment moves that source's cached facts and scores to
`work/facts/old_rework/`; `drop` quarantines the source in the corpus ledger (locked, by the
human); `topic` adds a topic to a new numbered config and searches for it; `topic_scope` gives
a topic a `covers:` line the scorer reads ("where a topic says what it covers, a fact outside
that scope scores 0 or 1"). The chain always ends in the cached `report` — never `revise` — so
rework costs about an hour plus whatever was invalidated (a topic change re-scores every
fact, ~2.5 h). Which source a comment means is read from where it sits: the `[@key]` in the
anchored text, or the fact's own text for a by-publication bullet; which topic, from the
heading above. The next report opens with the release guide and **"Your comments on the last
version"**, one line per comment, marked done or not against what the run holds.

Not yet done: raconteur still reads the prose prefix.

### Synthesise, section by section

**No call ever sees more evidence than its context can hold.** One-shot whole-corpus synthesis
is structurally impossible: 84 sources is ~31k tokens of digest against a 16k window, and
Ollama *silently discards the head of an oversized prompt*. A review of 84 sources once cited
15 — every one of them from the tail of the digest, because the model never saw the rest, and
nothing in the log said so. No prompt could have fixed that.

| Step | Sees | LLM |
|---|---|---|
| **1. plan** | compact digest of the whole corpus (~250 chars/source) | 1 call — the only one needing a global view, and the only one cheap enough to have one |
| **2. shortlist** | embed each source's compact line + each section's idea → cosine → the ~18 sources bearing on it | none (embedding, like `locate`) |
| **3. draft** | ONE section's shortlist, as full digest lines | 1 call per section |
| **4. polish** | that section + its evidence | lint + peer review + re-draft, per section |
| **5. orphans** | a source no section cited is offered to its nearest section; survivors rejected **by name** | 1 re-draft per section that gains a source, + 1 ledger call |
| **6. repair** | guards over the assembly; each `Finding.section` routes the fix | 1 re-draft per offending section |

Section count follows the material, never the corpus size. A section **re-cites freely** —
foundational work belongs in several, so the shortlist is retrieval with overlap, not a
partition. What it must not do is **re-explain**: each draft is handed what earlier sections
already said about its candidates, and the repeated-fact guard catches what slips through.
Citing a source again for what it adds here is never flagged; the paper stage needs to know
every role a source can play, but each fact once. Importance (`mindmap.evidence_weight`) applies
the same test, so a restated sentence earns its source no weight. The shortlist is deliberately recall-biased (18 offered, ~12 used): cosine on a
six-word heading is a weak signal, so retrieval over-offers and drafting prunes.

`brain._check_context` now warns, loudly and with the call site, whenever a prompt would be
truncated. Silent truncation of the evidence is the worst failure this tool can have: the
output looks founded and is not.

### The guard batteries (`guards.py`)

Pure functions over text. Python decides *that* something is wrong and states it as an
imperative; only what code cannot decide reaches the LLM.

| Family | Guards | Runs on |
|---|---|---|
| **Verifiability** | uncited paragraph · unresolved citekey · author-year prose · dropped citekey · dropped/invented equation · duplicate citekey | every path |
| **Breadth** | disposition (cited-or-rejected) · short section · accretion · triangulation · sparse paragraph · thin section | **synthesis only** |
| **Attribution** | unsupported bridge (a brief term put to sources whose own notes never use it) · repeated fact (a number or point an earlier section already made from the same source) | **synthesis only** |
| **Minimality** | touched sentences ⊆ the sentences the comment anchors to | **redline only** |

Breadth guards must never run on the redline path: a comment like *"explain consonance"*
would make the reviser inject citations into unrelated paragraphs to satisfy accretion.
Breadth demands new sources; minimality forbids collateral change. Both are correct — so
they run on different passes. Comments that genuinely need breadth route to the corpus chain.

The polestar, printed each run and written into the document header:

```
sources cited 62/83 · rejected 21 · unplaced 0 · mean sources/para 3.4 · triangulated 24/26 · unresolved keys 0
```

---

## Module map

```
rabbithole/
  cli.py        entry point: init | gather | build | report | revise | graft | audit | mindmap
  config.py     ProjectConfig (litrev.yaml) + GlobalConfig (~/.config/rabbithole) + paths
  wizard.py     `init` interactive setup
  models.py     Candidate (+ Author): identity/dedup keys, author-year + full citation
  sources.py    OpenAlex, Crossref, Semantic Scholar, arXiv, Unpaywall clients
  filters.py    MDPI/publisher exclusion, date window, de-dup (richest record wins)
  guards.py     deterministic guard batteries: verifiability, breadth, minimality + metrics
  ranking.py    relevance ranking (embedding | citations | llm re-rank)
  discover.py   gather orchestration
  zotero.py     Zotero Web API (create collection, read items, download PDFs, fulltext)
  pdfs.py       text extraction, full-text verification, [p.N] page markers
  corpus.py     report ingest (Zotero collection OR ./pdfs/ fallback)
  chroma.py     ChromaDB helpers: index paper chunks, semantic retrieval, locate_direct
  brain.py      LLM backend: coordinator/worker, embeddings, parallel worker_map
  summarize.py  report pipeline: read_notes → synthesize → locate_claims → bibliography
  facts.py      fact-base report (report_format: facts): extract → verify → group → render
  render.py     assemble Markdown + pandoc → .docx
  notify.py     optional email notifications via local mail program (SLURM MailProg)
```

## Data flow / artifacts (per project folder)

```
litReview/
  litrev.yaml                    project config (versioned: litrev_2.yaml, litrev_3.yaml …)
  candidates.md                  human-readable list (DOIs + links), missing from Zotero
  work/candidates.json           machine candidate records
  work/corpus.json               ingested corpus metadata
  work/annotations/NNN.json      per-paper read notes (resumable)
  work/disposition.json          every curated source: cited / rejected (with reason) / unplaced
  work/located/NNN.json          per-paper located claims (resumable)
  work/facts/<citekey>.json      fact base: per-paper grounded facts (resumable, versioned)
  work/facts.json                fact base: every fact with its page link, and the themes
  work/chroma/                   ChromaDB chunk index (persistent across runs)
  pdfs/                          PDFs for --from-folder mode
  output/<project>_litreview_<brain>.{md,docx}   the review
```

---

## Fidelity to the Claude lit-review skill

- [x] Never include MDPI (DOI prefix `10.3390`, publisher, `mdpi.com`, journal list).
- [x] Only sources with verified full-text PDFs (`looks_like_fulltext`).
- [x] `[@citekey]` pandoc citations, claim-first (the citation is never the sentence's
      subject); bibliography sorted by first author. Author-year prose is a guard failure —
      it is invisible to the citekey-keyed bibliography and silently unverifies its claim.
- [x] Complete metadata (title, authors, year, venue, DOI, URL, abstract, publisher).
- [x] Per-source annotation with page-level location pointer and supporting quote.
- [x] Thematic narrative (synthesise, not summarise; gaps/tensions/directions) +
      annotated bibliography over the whole curated corpus, post-synthesis. The failure to
      avoid is *serial exposition* (sources marched through one at a time), not citation
      density — weaving is citation-dense by construction.
- [x] `.docx` output.
- [x] Citation-integrity check: unmatched cites flagged in document and run log.

---

## Known limitations / future work

- `revise --resynth` is document-scale by nature: it must hold the previous draft, the new
  draft, and the evidence in one call. Above a modest corpus it will overflow the coordinator's
  context (`_check_context` says so). The default redline path has no such limit — it works one
  paragraph at a time. Prefer it.
- `_audit_revise_loop` still asks an LLM to spot **overreach** by eyeballing two drafts. That is
  the document-scale analogue of the minimal-edit guard, which is mechanical at paragraph scale.
  It could be computed: diff paragraph-by-paragraph, and any paragraph that changed without
  carrying an annotation is overreach.
- Sections are drafted independently, each in the author's style. There is no global
  consistency pass; a section only sees its neighbours' headings and the previous section's
  closing sentence.
- Locate quality depends on ChromaDB chunk granularity — very short claim
  sentences may retrieve imprecise chunks.
- The Claude brain A/B path requires `ANTHROPIC_API_KEY` and the `claude` extra.

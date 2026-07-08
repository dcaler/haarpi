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
```

### report — three stages

| Stage | What happens | LLM call |
|---|---|---|
| **1. Read** | Per-paper notes (argument/methods/findings/themes). ChromaDB indexes each paper; long papers retrieved via 3-query semantic retrieval rather than brute-force condensing. Incremental — resumes from `work/annotations/`. | coordinator × N papers |
| **2. Synthesise** | Sectioned — see below. | coordinator × (1 + ~4 per section) |
| **3. Locate** | For each curated source: embed claim sentences → retrieve top-1 chunk → extract quote + page location. No LLM call — pure embedding arithmetic. Incremental — resumes from `work/located/`. | none (embedding only) |

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
partition. The shortlist is deliberately recall-biased (18 offered, ~12 used): cosine on a
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
  cli.py        entry point: init | gather | report
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

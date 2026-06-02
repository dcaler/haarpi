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
| **Post-synthesis bibliography** | Annotated bibliography entries are a function of the *finished review* — locate runs after synthesis, scoped only to cited sources. |
| **Citation-integrity guard** | Local models hallucinate citations; every in-text cite is checked against the corpus. |

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
| **2. Synthesise** | Coordinator writes thematic narrative with full parenthetical citations from the notes digest. | coordinator × 1 |
| **3. Locate** | For each *cited* source only: embed claim sentences → retrieve top-1 chunk → extract quote + page location. No LLM call — pure embedding arithmetic. Incremental — resumes from `work/located/`. | none (embedding only) |

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
  work/located/NNN.json          per-paper located claims (resumable)
  work/chroma/                   ChromaDB chunk index (persistent across runs)
  pdfs/                          PDFs for --from-folder mode
  output/<project>_litreview_<brain>.{md,docx}   the review
```

---

## Fidelity to the Claude lit-review skill

- [x] Never include MDPI (DOI prefix `10.3390`, publisher, `mdpi.com`, journal list).
- [x] Only sources with verified full-text PDFs (`looks_like_fulltext`).
- [x] Full parenthetical author-year citations (claim-first; the citation is never
      the sentence's subject); bibliography sorted by first author.
- [x] Complete metadata (title, authors, year, venue, DOI, URL, abstract, publisher).
- [x] Per-source annotation with page-level location pointer and supporting quote.
- [x] Thematic narrative (synthesise, not summarise; gaps/tensions/directions) +
      annotated bibliography (cited sources only, post-synthesis).
- [x] `.docx` output.
- [x] Citation-integrity check: unmatched cites flagged in document and run log.

---

## Known limitations / future work

- Very large corpora (50+ papers) may benefit from section-by-section synthesis
  (planned: coordinator assigns themes → workers draft → coordinator integrates).
- Locate quality depends on ChromaDB chunk granularity — very short claim
  sentences may retrieve imprecise chunks.
- The Claude brain A/B path requires `ANTHROPIC_API_KEY` and the `claude` extra.

# RAbbithole

An **offline-first Research Assistant**. You give it a topic; it finds the
literature, you grab the PDFs, and it reads everything and writes a
**literature review + annotated bibliography** — all reasoning done by **local
LLMs** via Ollama. No browser, runs headless.

> Two commands with one human step in the middle; both commands are repeatable.

```
rabbitHole init     ▸ walks you through setup, writes litrev.yaml
rabbitHole gather   ▸ lists literature missing from your Zotero collection → candidates.md
   (you download PDFs and add them to the Zotero collection)   ← your turn
rabbitHole report   ▸ reads everything in the collection → review (.md + .docx)
```

Re-run `gather` after shifting focus to refresh the "what's still missing" list;
re-run `report` after adding more PDFs to rebuild the review.

---

## 1. Install

```bash
pip install -e .                # adds the `rabbitHole` command
pip install -e '.[rag]'         # adds ChromaDB (recommended — needed for locate step)
pip install -e '.[claude]'      # add this if you want the Claude brain
```

Requirements: Python ≥3.11, `pandoc` (for .docx output), and Ollama running
locally (or on a reachable host) with your chosen models pulled.

Recommended models (adjust to what you have):
- coordinator: `qwen3.6:27b-16k` (synthesis and reading)
- worker: `qwen3.5:9b-q4_K_M` (fallback tasks)
- embeddings: `mxbai-embed-large`

## 2. One-time machine setup

`rabbitHole init` creates the global config on first run. Or write it yourself
at `~/.config/haarpi/config.toml` (the unified HAARPi config; a legacy per-tool file is still honored underneath it):

```toml
ollama_url = "http://localhost:11434"
contact_email = "you@example.com"   # used for API "polite pools" — recommended

[zotero]                            # optional; without it gather lists all candidates
api_key = "xxxxxxxx"
library_id = "1234567"
library_type = "user"               # "user" or "group"

[anthropic]                         # only for the Claude brain
api_key = "sk-ant-..."

[openalex]                          # free key; recommended (see below)
api_key = "xxxxxxxx"
```

OpenAlex's free daily budget without a key is shared by every machine on your IP (1,000 credits,
10 per search), and one large gather can spend it. A free key from openalex.org raises it to
10,000. rabbitHole sends the key as a header, never in a URL. When the budget runs out, a run
stops and says so, rather than treating every later search as empty.

Anything here can be overridden by environment variables:
`OLLAMA_URL`, `RABBITHOLE_CONTACT_EMAIL`, `ZOTERO_API_KEY`, `ZOTERO_LIBRARY_ID`,
`ZOTERO_LIBRARY_TYPE`, `ANTHROPIC_API_KEY`, `S2_API_KEY`, `OPENALEX_API_KEY`.

Find your Zotero `library_id` and create an API key at
<https://www.zotero.org/settings/keys> (give it write access to create
collections).

## 3. Start a project

```bash
mkdir ~/research/graphene-batteries
cd ~/research/graphene-batteries
rabbitHole init
```

The wizard asks for the topic, focus, target size (default 20–50), date range,
which sources to use, and the brain. It writes `litrev.yaml` inside a `litReview/`
subfolder and optionally chains straight into `gather`.

## 4. gather — list what's missing

```bash
rabbitHole gather
```

Searches OpenAlex, Crossref, Semantic Scholar, and arXiv; de-dupes; drops MDPI
and out-of-range items; expands via citation-trail snowballing; ranks by embedding
similarity; then, if Zotero is configured, **subtracts whatever is already in your
collection** and writes only the still-missing literature to:

- **`litReview/candidates.md`** — ranked, numbered list with DOIs and PDF links.
- **`litReview/work/candidates.json`** — machine-readable candidate records.

If Zotero is configured, gather find-or-creates a collection named after the
project (safe to re-run — it won't duplicate). It also auto-files any candidates
you already own elsewhere in your library into the project collection.

## 5. Your turn (the human gate)

1. Skim `litReview/candidates.md`.
2. Download the PDFs you want and add them to your **Zotero collection**, or drop
   them straight into `litReview/pdfs/` and use `--from-folder`.

## 6. report — read and write the review

```bash
rabbitHole report                  # uses the brain from litrev.yaml
rabbitHole report --brain claude   # A/B: same corpus, Claude as the brain
rabbitHole report --from-folder    # ignore Zotero, read ./litReview/pdfs/ only
```

Three-stage pipeline:

1. **Read** — the coordinator reads each paper (using ChromaDB semantic retrieval
   for long papers) and writes structured notes to `work/annotations/NNN.json`.
   Incremental: re-running skips papers already annotated.
2. **Synthesise** — the coordinator writes a thematic narrative with author-year
   citations from the notes digest.
3. **Locate** — for every curated source, ChromaDB retrieves the most relevant
   passage and extracts the supporting quote and page location; a quote that
   scores low against its claim is labelled *weak support*. This produces the
   annotated bibliography entries.

The review opens with the most load-bearing sources and **coverage against the
brief**: each focus item marked covered or THIN by how much of the corpus
addresses it in its own words.

**Claim grounding (adopted 2026-11-13)** is how a literature review is built. Each paper
gets a card:
- llama3.3 extracts its claims;
- two verifiers from other model families each point to the passage that says a claim;
- code finds that passage in the paper's own words;
- each verifier judges the claim against the other's passage, and whether it is the paper's
  own finding.

Only a claim that passes every check is confirmed. `rabbithole cards <step>` runs one step per
call into a card store shared by every project (`cards/` in the HAARPi tree). Each run plants
its own canaries, and a kind that slips past both verifiers more than one time in ten, even at
the low end of its 95% interval, holds the release. `cards topics` scores each confirmed claim
0–3 against the project's topics, and the scores belong to the project (editing a topic re-scores).
`cards gate` writes the gate page (`gate.py`):
the map, the slip rates, a sample of 25 confirmed claims, and what is missing per topic. A person
saves their answers, and `haarpi next` reads them: a pass releases the claims for raconteur
(`…_litreview.claims.json`, `../haarpi/DESIGN_claims_release.md`).

`rabbitHole topics` drafts the questions and topics from the brief as a review page
(`<date>_<short>_topics_ra.html`); the person accepts, rejects and edits each one and saves
`…_topics_ra_DCR.json` beside it; `rabbitHole topics --apply` writes them into a new
numbered config. A new project's opening cycle runs these first.

**The fact base is retired** (2026-11-13). `report_format: facts` now makes `report` and
`mindmap` refuse with a note. Its modules (`facts.py` as it was, `fact_rework.py`,
`review_page.py`) and their tests are in `archive/factbase/`, with how to bring them back;
the tag `factbase-retired` holds the tree as it was. `facts.py` keeps only the helpers the
live code uses (passage location, topic scoring, the reported-work patterns).

**How a paper's text is read.** Every PDF goes through `pdfs.py`:
- Running headers, footers and page numbers are left out.
- A raised reference number is kept in its sentence as `[44]`, or as `[note:3]` in a paper
  that cites by author and year.
- A unit's exponent stays one (`km²`).

When `facts.locate_passage` hands on the paper's words for a quote:
- the passage is widened to whole sentences;
- a list bullet or a braced section reference (`{4.4}`) ends a sentence;
- a passage that crosses a page drops table rows left before the break;
- it carries the pages it touches.

Output:
```
litReview/output/<date>_<short>_litreview_gate_ra.html     the gate page (and …_gatemap_ra.html)
litReview/output/<date>_<short>_litreview.claims.json      the claims release, on a pass
methodsReview/output/<project>_litreview_<brain>.md|.docx  a methods review's narrative report
```

---

## The brain: local vs Claude

rabbitHole splits LLM work into two roles:

- **coordinator** — judgement-heavy work (reading papers, synthesis). Swappable:
  local Ollama *or* the Claude API.
- **worker** — small tasks (embeddings, fallback condensing). Always local.

**A/B compare** by running report twice — `--brain ollama` then `--brain claude`.
Outputs carry the brain name so they sit side by side in `output/`.

---

## `litrev.yaml` reference

```yaml
project_name: graphene_batteries
topic: graphene anodes for lithium-ion batteries
focus: energy density; cycle life; scalability
target_min: 20
target_max: 50
date_from: 2015          # or null
date_to: 2025            # or null
sources:
  openalex: true
  crossref: true
  semantic_scholar: true
  arxiv: true
ranking:
  method: embedding      # embedding | citations | llm
  rerank_top_n: 0        # >0 with method: llm to re-rank the top N
  max_arxiv_fraction: 0.25  # cap arXiv/preprints to ≤25% of the final list
brain:
  backend: ollama        # ollama | claude
  coordinator_model: qwen3.6:27b-16k
  worker_model: qwen3.5:9b-q4_K_M
  embed_model: mxbai-embed-large
  claude_model: claude-sonnet-4-6
  worker_parallel: 4
zotero:
  collection_key: ''     # filled in automatically by gather
exclude_publishers: []   # extra publishers to drop (MDPI is always excluded)
```

## Rules it always enforces

- **Never includes MDPI** (checked by DOI prefix `10.3390`, publisher name,
  `mdpi.com`, and a known-journal list — re-checked at ingest).
- **Only sources with verified full-text PDFs** make it into the review.
- **Full parenthetical author-year citations** — claim-first, with the citation
  trailing in parentheses; the citation is never the subject of the sentence.
  Bibliography sorted alphabetically.
- **Citation-integrity check**: any in-text citation not matching a corpus item
  is flagged at the end of the document and in the run log.

## Troubleshooting

- *APIs rate-limiting* → set `contact_email` (puts you in the polite pool).
- *No .docx* → install `pandoc`.
- *report says "no usable sources"* → PDFs may be abstract-only previews, or the
  Zotero collection has no PDF attachments yet. Add PDFs and re-run.
- *report with Claude errors* → `pip install '.[claude]'` and set the Anthropic key.
- *Want to start report over* → delete `litReview/work/annotations/`,
  `litReview/work/located/`, and `litReview/work/chroma/`.

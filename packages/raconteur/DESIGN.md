# Raconteur — Design

## Purpose

Raconteur is an offline-first paper writing assistant. You give it a topic;
it generates an outline, drafts the paper, and incorporates your Word revisions
into successive drafts — all reasoning done by local LLMs via Ollama.

The intended loop:

```
raconteur init      →  raconteur.yaml + paper/ directory
raconteur onepager  →  concise narrative one-pager (.md + .docx)
   [you annotate]   →  save your Word file with initials appended
raconteur onepager  →  reads your annotations, revises the one-pager
raconteur outline   →  structured outline, derived from the approved one-pager
raconteur draft     →  first full draft (.md + .docx)
   [you annotate]   →  save your Word file with initials appended
raconteur draft     →  reads your annotations, writes revised draft
raconteur focus N   →  refines one section, minor-version the file
```

The **one-pager** is the first deliverable: the most concise path through the
paper's narrative — high notes only, at most two figures embedded from rayleigh's
output. Like every deliverable it goes through the human edit/revise cycle. It is
a required precursor to `outline`, which uses the approved narrative to design the
full paper; the narrative is then carried into `draft` and `focus` as well.

The one-pager is written in the author's voice: a **required** author style
profile (trained from Zotero publications, stored globally and reused across
projects) is a mandatory input. `onepager` trains it automatically if missing and
hard-errors if it cannot be produced, so author voice is guaranteed from the very
first deliverable onward.

---

## Core Principle

**Python orchestrates; the LLM does narrow tasks.**

Python owns all file I/O, naming, discovery, and control flow. The LLM is called
only for narrow tasks: parsing a research description into structured fields (a
short worker call, made lazily on first use in `onepager`), generating a one-pager,
outline, or draft (long coordinator calls), and refining a section (another
coordinator call). It never decides what files to read or write.

`init` is entirely human-driven: it makes **no LLM call at all** and does not
require Ollama to be running. Its only network access is an optional Zotero lookup
when the author style profile has not yet been trained.

### Guards in Python, judgement in the LLM

The polestar is a **grounded, verifiable manuscript**: every substantive claim traceable
to the material raconteur was given, and every `[@citekey]` resolvable against `refs.bib`.
A Methods paragraph describing an algorithm the writeup never mentions, or a Background
paragraph that cites nothing, is a defect — not a matter of taste.

So Python decides *that* something is wrong, precisely, and states it as an imperative the
model must satisfy. The LLM decides only what cannot be computed. `guards.py` holds those
mechanical checks as pure functions over text and the parsed bib — no I/O, no LLM, no docx,
so they are trivially unit-testable against a real document as a fixture.

Two scoping rules keep the guards from fighting each other:

- **Phase.** Density guards run on *drafting*, where fuller grounding is the goal. Minimality
  guards run on *redlining*, where collateral change is the defect. A guard that is right for
  one is wrong for the other.
- **Section kind.** The citation floor is gated on `guards.section_kind()`. A Methods or
  Results paragraph is grounded in the methods writeup and the results files, not in the
  bibliography; demanding citations there is a category error. Abstracts and front matter are
  exempt too.

`brain._check_context` is the same discipline applied to the prompt itself: Ollama silently
discards the head of an over-length prompt, so Python estimates the token count against
`num_ctx` and warns, naming the offending call site. It never truncates — it makes the
invisible visible.

Every `paper` run ends with a metrics line, the polestar as a number:

```
citekeys resolved 47/47 · uncited body paragraphs 0 · sparse 2 · sections 6
```

---

## File Naming Convention

Every file produced by raconteur follows this pattern:

```
YYMMDD_<short_title>_<initials_chain>.<ext>
```

- **`YYMMDD`** — date the file was created by whoever created it.
- **`short_title`** — set once at `init`, used as the stable identifier for the
  project across all files. No spaces; underscores allowed.
- **`initials_chain`** — a `_`-separated sequence recording who touched the file,
  in order. Raconteur always uses `ra`. The researcher appends their own initials
  when saving their revision.

### Version semantics

| Command | Effect on filename |
|---|---|
| `onepager` | `YYMMDD_title_onepager_ra.md` — fresh, chain is `onepager_ra`, **new date** |
| `outline` | `YYMMDD_title_ra.md` — fresh, chain reset to `ra`, **new date** |
| `draft --resynth` | `YYMMDD_title_ra.md` — fresh, chain reset to `ra`, **new date** |
| `draft` (redline) | `YYMMDD_title_<existing_chain>_ra.docx` — chain extended, **date preserved** |
| `focus` | `YYMMDD_title_<existing_chain>_ra.md` — chain extended, **date preserved** |

**Major versions get a new datestamp and reset the initials chain**; the history of
who revised what is preserved in git, not in the filename. **Minor versions keep the
source file's datestamp and extend the chain**, so the revision lineage within a
cycle stays readable. A new datestamp therefore always means a new revision cycle.

`focus` and the default `draft` redline are minor. `onepager`, `outline`, and
`draft --resynth` are major.

### Example progression

```
260607_trust_ra.docx          ← raconteur's first draft
260607_trust_ra_DCR.docx      ← DCR's revision (researcher saves as)
260608_trust_ra.docx          ← raconteur incorporates DCR's annotations (major → reset)
260608_trust_ra_DCR_ra.docx   ← raconteur refines Methods section (focus → extend)
260608_trust_ra_DCR_ra_DCR.docx  ← DCR annotates again
260609_trust_ra.docx          ← raconteur incorporates new annotations
```

### Discovery rules

- **Latest outline**: newest `*.md` in `paper/` whose last initials are `ra`.
- **User revision**: newest `*.docx` in `paper/` whose last initials are *not* `ra`.
  If one exists, `draft` reads it and revises; otherwise it drafts fresh from the outline.

---

## LLM Architecture

Two roles, same Ollama backend:

**Coordinator** — sequential, high-context (32 768 tokens), temperature 0.4.
Used for one-pager and outline generation, drafting, revision, and section refinement.

**Worker** — can be parallelised (ThreadPoolExecutor), lower-context (8 192 tokens),
temperature 0.1. Used for short structured tasks: parsing the research description
into `topic`/`focus` fields (lazily, on first use in `onepager`), extracting
equations from the methods writeup, and looking up venue format specs in `venue`.

The coordinator model is stored in `raconteur.yaml` so it can be changed per
project. The global config at `~/.config/raconteur/config.toml` sets the default.
Same model defaults as RabbitHole: `qwen3.6:27b-16k` / `qwen3.5:9b-q4_K_M`.

Claude API support is a planned option (the `brain.py` abstraction is designed
for it), but not yet implemented.

---

## Upstream Pipeline

Raconteur is the last stage of the `ra*` toolchain. It expects three upstream
tools to have run to completion before it does:

| Tool | Output | Feeds |
|---|---|---|
| [rabbitHole](https://github.com/dcaler/rabbithole) | `litReview/` | literature review (`output/*.md`, `refs.bib`) |
| [raster](https://github.com/dcaler/raster) | `<date>_methods_<chain>.md` (project root) | methods writeup → methods |
| [rayleigh](https://github.com/dcaler/rayleigh) | `results/` | experiment results → results |

raster writes a purpose-built **methods writeup** for raconteur at the project
root — `<date>_methods_<initials_chain>.md`, chained like raconteur's own files.
raconteur reads the latest such file (highest datestamp) as its methods context;
it no longer reads a `code/` source directory.

At the start of `outline` (and during `init`), `check_prerequisites` verifies
each output is present and **warns loudly** for any that is missing, naming the
tool that should have produced it. The warning is non-fatal: a theory paper may
legitimately have no experiments, so raconteur proceeds with reduced context
rather than erroring. The point is that an absent source is a deliberate choice,
not a silent oversight.

## Context Sources

Given the upstream outputs, raconteur reads three sources before generating an
outline or draft:

**`litReview/output/*.md`** (rabbitHole)
Read the most recently modified file. Truncated to 12 000 characters. Passed to
the LLM as literature review context — informs all sections. `refs.bib` from the
same directory supplies a compact citekey list for citation guidance.

**`<date>_methods_<chain>.md`** (raster, project root)
The latest methods writeup (highest datestamp). Truncated to 20 000 characters.
Passed as methods context — primarily informs the methods section; the outline's
equation-extraction pass mines it for named equations and update rules.

**`results/`** (rayleigh)
Reads `findings.json`, `tables/*.csv`, and other result files. Passed as results
context — primarily informs the results section.

All sources are discovered relative to the project root (the directory containing
`raconteur.yaml`), not relative to `paper/`.

---

## Revision Reading

`onepager` and `outline` are planning artifacts, and a clean rewrite is the deliverable the
researcher wants. For those, `revise.py` reads the annotated `.docx` with `python-docx` and
raw XML parsing — `w:ins` insertions, `w:del` deletions, and the `word/comments.xml` part —
and formats them into one block of instructions passed verbatim to the revision prompt.

`revise.read_text` reads through `redline.flatten_paragraph`, not `paragraph.text`. The
latter walks `w:t` runs only, and an inline equation's characters live in `m:t` on a sibling
`m:oMath` element — so the naive read returns prose with a hole where every number was, and
the equation is silently lost from whatever is regenerated from it.

`paper` does **not** work that way, because a manuscript is not a plan.

## The Redline (how `paper` revises)

When `paper` finds an annotated `.docx` it edits **a copy of that document in place**,
inserting Word tracked changes. It does not regenerate markdown. The clean rewrite survives
as `paper --resynth`.

Two failures motivated the change, both observed in rabbitHole's identical `report`/`revise`
shape:

- **Collateral drift.** The old path re-drafted *every* section from one global annotation
  blob and ran critique→revise twice on each. A comment on the Discussion rewrote the
  Methods. A tracked change that altered an untouched region, under a reply claiming success,
  is worse than no edit at all.
- **No redline.** The reviewer annotated a sentence; the tool threw the sentence away and
  wrote a new paragraph. Nothing to accept, reject, or trust.

Three mechanisms make the redline surgical:

**Comment→sentence anchoring.** `redline.comment_spans` recovers the character range each
comment brackets and maps it to sentence indices in a specific paragraph. Each comment
reaches exactly the sentences it touches; every other sentence in the document is never even
shown to the reviser. The global blob is gone.

**Sentence-indexed edits.** The reviser receives the paragraph as numbered sentences (with
`▶` marking the anchored ones) and returns JSON of *only* the sentences it changed:

```json
{"2": "The revised second sentence [@smith2021].", "5": null}
```

`null` deletes; an absent index means untouched and is copied byte-for-byte. Minimality
becomes a set operation rather than a hope: `guards.minimal_edit_violation` fails the edit
when the model touched a sentence no comment anchored to.

**The atom stream.** A Word paragraph is *not* the text in its `w:r` runs — an inline
equation is an `m:oMath` **sibling** of the runs. `redline.serialize_paragraph` emits prose
as text and each opaque atom (equations, footnotes, drawings, hyperlinks) as a sentinel
`⟦m:1⟧`. On write-back, sentinels are re-laid as **accepted** content, never inside a
`w:ins`/`w:del`.

> **Invariant: raconteur never authors an equation; it only edits the prose around one.**

`guards.dropped_sentinels` and `guards.invented_sentinels` fail the edit closed if the model
breaks it.

### Fail closed

Malformed JSON, a dropped citekey, a dropped or invented equation, an out-of-scope sentence,
an unresolvable `[@key]`, or an exhausted retry budget — any of these and **no tracked change
is written.** The reviewer's paragraph is left exactly as they wrote it and the reply says so.
A broken edit under a reply claiming "done" is the worst possible outcome.

### Routing: not every annotation is a redline

A tracked change cannot add a section or a source that does not exist. The audit classifies
what it cannot satisfy in place:

| Class | Meaning | Where it goes |
|---|---|---|
| `section` | asks for new structure | `raconteur outline`, then `paper` |
| `sources` | asks for literature not in `refs.bib` | rabbitHole |
| `evidence` | asks for a result or method that does not exist | rayleigh / raster |
| `figure` | asks for a table or chart | rayleigh, then re-render |

raconteur cannot manufacture evidence. Answering a request for a figure with "gather more
sources" would be a false diagnosis — the class is what makes the reply honest.

**Every comment gets a disposition and a reply. Silence is not a decision:** a comment
neither applied nor explicitly declined is a defect in the revise pass itself.

### Scope

The redline touches body prose only. Headings, the title, and the References list are never
redlined. The **abstract is** redlined — it is body prose, and a comment on it must produce a
tracked change like any other. The References list is *not* rebuilt by the redline (it was
rendered by pandoc/citeproc at draft time); a newly cited source triggers a loud warning
naming the keys that have no entry.

---

## Module Organisation

```
raconteur/
├── cli.py       argparse entry point; Ollama reachability check; match dispatch
├── config.py    ProjectConfig (YAML) + GlobalConfig (TOML/env)
├── wizard.py    init interactive flow
├── onepager.py  concise narrative one-pager; embeds ≤2 rayleigh figures; revise cycle
├── guards.py    pure mechanical checks over text + bib; Finding/Metrics; no I/O, no LLM
├── brain.py     Ollama coordinator + worker; streaming; retry with backoff; context guard
├── naming.py    filename parse / generate; major + minor versioning; discovery
├── context.py   load litreview / methods writeup / results context; warn on missing upstream outputs
├── outline.py   outline generation
├── draft.py     fresh draft or revision-aware draft
├── focus.py     section extraction by number or heading; refinement; minor versioning
├── redline.py   atom-stream OOXML surgery; sentence-level tracked changes; comment anchoring
├── redline_revise.py  the default paper revise path: per-paragraph adversary, fail closed, routing
├── revise.py    read docx track changes and comments (onepager/outline clean-rewrite path)
└── render.py    pandoc markdown → docx; graceful skip if pandoc absent
```

---

## Config Schema

### `raconteur.yaml` (project root)

```yaml
title: Full Paper Title
short_title: short_title      # used in all filenames
author_initials: DCR          # your initials
topic: concise research area
focus: specific angle or contribution
scope: 4-page conference short paper, focus on empirical findings
venue:
  name: CHI 2026
  page_limit: 4
  word_limit: null
  citation_style: ACM
  columns: 2
  abstract_limit: 150
  format_notes: Use ACM reference format; include CCS concepts and keywords
brain:
  coordinator: qwen3.6:27b-16k
  worker: qwen3.5:9b-q4_K_M
```

`venue` and `scope` are optional. Both are passed verbatim into every LLM prompt
(one-pager, outline, draft, revision, focus) so the model calibrates depth, breadth,
and length to the submission target. Venue format specs are looked up by the worker
LLM in the `venue` command using training knowledge of common venues; they should be
verified against the official call for papers.

### `~/.config/raconteur/config.toml` (machine-level)

```toml
[ollama]
url = "http://localhost:11434"
coordinator = "gemma3:27b"
worker = "gemma3:12b"
```

`OLLAMA_URL` environment variable overrides `url`.

---

## Error Reporting

All output goes to **stderr**, following the same conventions as RabbitHole:

- `[raconteur]` — progress messages (file written, context loaded, etc.)
- `[warn]` — non-fatal issues (pandoc absent, LLM parse failed, no annotations found)
- `[error]` — fatal issues (Ollama unreachable, config missing, outline not found)

Stdout is unused, so raconteur output is safe to redirect or pipe.

---

## Non-Decisions

Things deliberately left out of scope for now:

- **Reference management** — `[REF]` placeholders are used in drafts; citation
  resolution is out of scope (RabbitHole handles the bibliography side).
- **Claude brain** — the `Brain` class is designed to accept a second backend;
  not wired up yet.
- **Multi-author workflows** — the initials chain is designed to support them,
  but no coordination logic exists.
- **Section reordering** — `focus` refines sections in place; structural
  reorganisation is left to the researcher.

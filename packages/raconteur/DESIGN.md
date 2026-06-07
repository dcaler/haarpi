# Raconteur — Design

## Purpose

Raconteur is an offline-first paper writing assistant. You give it a topic;
it generates an outline, drafts the paper, and incorporates your Word revisions
into successive drafts — all reasoning done by local LLMs via Ollama.

The intended loop:

```
raconteur init     →  raconteur.yaml + paper/ directory
raconteur outline  →  structured outline (.md + .docx)
raconteur draft    →  first full draft (.md + .docx)
   [you annotate]  →  save your Word file with initials appended
raconteur draft    →  reads your annotations, writes revised draft
raconteur focus N  →  refines one section, minor-version the file
```

---

## Core Principle

**Python orchestrates; the LLM does narrow tasks.**

Python owns all file I/O, naming, discovery, and control flow. The LLM is called
only for three things: parsing a research description into structured fields (a
short worker call during `init`), generating an outline or draft (a long coordinator
call), and refining a section (another coordinator call). It never decides what
files to read or write.

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
| `outline` | `YYMMDD_title_ra.md` — fresh, chain reset to `ra` |
| `draft` | `YYMMDD_title_ra.md` — fresh, chain reset to `ra`, date updated |
| `focus` | `YYMMDD_title_<existing_chain>_ra.md` — chain extended, date updated |

`draft` is always a major version — it resets the initials chain. The history of
who revised what is preserved in git, not in the filename. `focus` is a minor
version — it appends to the existing chain, so the revision lineage stays readable.

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
Used for outline generation, drafting, revision, and section refinement.

**Worker** — can be parallelised (ThreadPoolExecutor), lower-context (8 192 tokens),
temperature 0.1. Used only for parsing the research description into `topic`/`focus`
fields during `init`.

The coordinator model is stored in `raconteur.yaml` so it can be changed per
project. The global config at `~/.config/raconteur/config.toml` sets the default.
Same model defaults as RabbitHole: `qwen3.6:27b-16k` / `qwen3.5:9b-q4_K_M`.

Claude API support is a planned option (the `brain.py` abstraction is designed
for it), but not yet implemented.

---

## Context Sources

Before generating an outline or draft, raconteur reads two optional sources:

**`litReview/output/*.md`** (RabbitHole output)
Read the most recently modified file. Truncated to 12 000 characters. Passed to
the LLM as literature review context — informs all sections. If absent, silently
skipped.

**`code/`** (analysis scripts and notebooks)
Recursively reads `.py`, `.R`, `.jl`, `.ipynb` files, up to 80 lines each,
capped at 4 000 characters total. Passed as analysis code context — primarily
informs the methods and results sections. If absent, silently skipped.

Both sources are discovered relative to the project root (the directory containing
`raconteur.yaml`), not relative to `paper/`.

---

## Revision Reading

When a user-revised `.docx` is found, `revise.py` reads it using `python-docx`
and raw XML parsing:

- **Track changes** — `w:ins` elements (insertions to keep) and `w:del` elements
  (deletions to remove) are extracted from the document body XML.
- **Comments** — the `word/comments.xml` part is accessed via the document's
  relationship map and all comment texts are extracted with their author.

The result is formatted as a structured block of instructions — deletions, insertions,
comments — and passed verbatim to the revision prompt. The LLM is told to apply
insertions, remove deletions, and make substantive edits in response to each comment.

---

## Module Organisation

```
raconteur/
├── cli.py       argparse entry point; Ollama reachability check; match dispatch
├── config.py    ProjectConfig (YAML) + GlobalConfig (TOML/env)
├── wizard.py    init interactive flow
├── brain.py     Ollama coordinator + worker; streaming; retry with backoff
├── naming.py    filename parse / generate; major + minor versioning; discovery
├── context.py   load litreview and code snippets for LLM context
├── outline.py   outline generation
├── draft.py     fresh draft or revision-aware draft
├── focus.py     section extraction by number or heading; refinement; minor versioning
├── revise.py    read docx track changes and comments
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
brain:
  coordinator: gemma3:27b
  worker: gemma3:12b
```

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

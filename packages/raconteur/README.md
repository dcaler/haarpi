# Raconteur

An **offline-first paper writing assistant**. You describe your research; it
generates an outline and drafts the paper using a local LLM. You annotate the
draft in Word; it reads your comments and tracked changes and writes a revised
draft. Repeat until done.

> Four commands. Your edits drive the revision loop.

```
raconteur init      ▸ walks you through setup, writes raconteur.yaml + paper/
raconteur onepager  ▸ concise narrative one-pager → paper/YYMMDD_title_onepager_ra.md (.docx)
raconteur outline   ▸ generates a structured outline from the approved one-pager
raconteur draft     ▸ writes the full paper, or incorporates your Word revision
raconteur focus N   ▸ refines a single section without touching the rest
```

The **one-pager** comes first: the most concise path through your paper's
narrative — high notes only, up to two figures. You edit it in Word like any
other draft, and `outline` uses the approved version to design the full paper.

Raconteur is the final stage of the `ra*` toolchain. It expects three upstream
tools to have run first, and reads their output automatically:

| Tool | Output | Informs |
|---|---|---|
| [rabbitHole](https://github.com/dcaler/rabbithole) | `litReview/` | literature review — all sections |
| [raster](https://github.com/dcaler/raster) | `<date>_methods_<chain>.md` | methods writeup — methods |
| [rayleigh](https://github.com/dcaler/rayleigh) | `results/` | experiment results — results |

If any of the three is missing, raconteur **warns loudly** (during `init` and at
the start of `outline`) and proceeds with reduced context — nothing is silently
skipped.

---

## 1. Install

**Option A — symlink (no pip required)**

```bash
git clone https://github.com/dcaler/raconteur.git
pip install httpx PyYAML python-docx   # dependencies only
ln -s /path/to/raconteur/bin/raconteur ~/.local/bin/raconteur
```

Make sure `~/.local/bin` is in your `PATH`. After that, `raconteur` works from
anywhere.

**Option B — pip editable install**

```bash
git clone https://github.com/dcaler/raconteur.git
cd raconteur
pip install -e .
```

This adds `raconteur` to whatever Python environment's `bin/` directory is active.

---

Requirements: Python ≥ 3.11, Ollama running locally (or on a reachable host)
with your chosen models pulled. `pandoc` is optional but needed for `.docx` output.

Recommended models (adjust to what you have):

```
coordinator: qwen3.6:27b-16k   (drafting, revision, section refinement)
worker:      qwen3.5:9b-q4_K_M (parsing the research description during init)
```

---

## 2. One-time machine setup

Create `~/.config/raconteur/config.toml`:

```toml
[ollama]
url = "http://localhost:11434"
coordinator = "gemma3:27b"
worker = "gemma3:12b"
```

If you skip this file, raconteur uses the defaults above. Override the Ollama URL
with the environment variable `OLLAMA_URL` if needed.

---

## 3. Start a project

```bash
mkdir ~/papers/trust-in-ai
cd ~/papers/trust-in-ai
raconteur init
```

The wizard asks for:

- **Paper title** — the full title of the paper.
- **Short title** — used in all filenames (no spaces; e.g. `trust_ai`).
- **Your initials** — appended to files when you revise (e.g. `DCR`).
- **Research description** — describe your research in plain language. A local LLM
  parses this into a `topic` and `focus` field in the background while the wizard
  continues.
- **Target venue** — the journal or conference you are writing for (e.g. `CHI 2026`,
  `Nature Human Behaviour`, `ICML`). Optional. A local LLM looks up the venue's
  format specifications — page limit, word limit, citation style, column format,
  abstract length — and stores them in the project config. Always verify these
  against the venue's official call for papers.
- **Scope and length target** — a brief description of what kind of paper this is
  and any constraints on coverage (e.g. `4-page conference short paper, focus on
  empirical findings only`, `8000-word journal article`). Optional. This is passed
  directly to the LLM when generating the outline and draft, so it will calibrate
  depth, breadth, and section count accordingly.

`init` creates:

```
trust-in-ai/
├── raconteur.yaml    project config
└── paper/            all generated output goes here
```

Re-run `init` at any time to update the topic or focus; other config is preserved.

---

## 4. onepager — the narrative spine

```bash
raconteur onepager
```

Writes the most concise path through your paper's narrative: high notes only —
motivation, gap, approach, key result(s), implication — in ~500 words, in your
own authorial voice. If rayleigh produced figures in `results/figures/`, up to
two of the most load-bearing are embedded directly in the `.docx`.

The author voice is **required**: an author style profile (trained from your
Zotero publications) is a mandatory input here. If one does not exist yet,
`onepager` trains it automatically before drafting — so configure Zotero and
confirm your publications during `init`. Once trained, the profile is reused
across projects and applied at every stage (`outline`, `draft`, `focus`).

Output: `paper/YYMMDD_shorttitle_onepager_ra.md` and `.docx`.

Edit it in Word — Track Changes and Comments — then save with your initials
appended (`…_onepager_ra_DCR.docx`) and re-run `raconteur onepager` to fold your
edits in. This is where you shape the story before any structure is committed. The
approved one-pager is **required** before `outline`, and its narrative is carried
into `draft` and `focus` too, so the whole paper stays true to the through-line
you signed off on.

---

## 5. outline — plan the paper

```bash
raconteur outline
```

Generates a structured markdown outline by expanding the approved one-pager into
full section structure, grounded in the literature review, code, and results.
Requires a one-pager — run `raconteur onepager` first.

Output: `paper/YYMMDD_shorttitle_ra.md` and `paper/YYMMDD_shorttitle_ra.docx`.

Edit the outline in Word before drafting if you want to steer the structure — save
it with your initials appended (e.g. `260607_trust_ai_ra_DCR.docx`). `draft` will
pick it up and treat it as the revised outline. Or just run `draft` directly.

---

## 6. draft — write the paper

```bash
raconteur draft
```

Reads the latest outline from `paper/` and writes a full draft. If a literature
review or a raster methods writeup is present, both are used as context.

If raconteur finds a user-revised `.docx` in `paper/` (i.e. a file whose last
initials are not `ra`), it reads the tracked changes and comments and writes a
**revision** instead of a fresh draft. You do not need to pass a flag — detection
is automatic.

Output: `paper/YYMMDD_shorttitle_ra.md` and `paper/YYMMDD_shorttitle_ra.docx`.
`draft` always resets the initials chain and updates the date stamp (it is a major
version; the prior history lives in git).

---

## 7. The revision loop

This is the core workflow. After each draft:

1. Open `paper/YYMMDD_title_ra.docx` in Word (or LibreOffice).
2. Use **Track Changes** to make edits directly in the text.
3. Use **Comments** to leave instructions (e.g. "expand this argument", "cite the
   Smith 2022 paper here", "this paragraph is redundant").
4. Save the file with your initials appended:
   `paper/YYMMDD_title_ra_DCR.docx`
5. Run `raconteur draft` again.

Raconteur reads all tracked deletions, tracked insertions, and comments, then
writes a new `_ra` draft that incorporates them. You can iterate as many times as
needed.

```
260607_trust_ai_ra.docx          ← raconteur's first draft
260607_trust_ai_ra_DCR.docx      ← your revision
260608_trust_ai_ra.docx          ← raconteur incorporates your changes
260608_trust_ai_ra_DCR.docx      ← your second revision
260609_trust_ai_ra.docx          ← raconteur's second revision
```

---

## 8. focus — refine one section

```bash
raconteur focus 3
raconteur focus "Methods"
raconteur focus "2.1"
```

Extracts the specified section from the current draft, asks the LLM to improve it
for clarity, depth, and flow, and writes the result back. Accepts a section number
or a heading name (partial match, case-insensitive).

Unlike `draft`, `focus` is a **minor version** — it appends `_ra` to the existing
initials chain rather than resetting it, so the lineage stays readable:

```
260608_trust_ai_ra.docx             ← current draft
260608_trust_ai_ra_ra.docx          ← after focus (chain extended)
```

Use `focus` when the overall draft is solid but individual sections need work,
rather than running a full redraft.

---

## 9. The ra* pipeline

Raconteur assumes rabbitHole, raster, and rayleigh have already run in this
project. The expected directory layout:

```
my-paper/
├── litReview/         ← rabbitHole output
│   └── output/
│       ├── project_litreview_ollama.md
│       └── refs.bib
├── 260705_methods_ra.md  ← raster output (methods writeup, project root)
├── results/           ← rayleigh output (experiments)
│   ├── findings.json
│   ├── tables/*.csv
│   └── figures/
├── paper/             ← raconteur output
└── raconteur.yaml
```

Raconteur reads the most recent `.md` in `litReview/output/`, the latest
`<date>_methods_<chain>.md` methods writeup at the project root, and the results
in `results/`, passing all three to the LLM as context. None is strictly required
— a missing source triggers a loud warning naming the tool that should have
produced it, and raconteur proceeds with what it has.

---

## `raconteur.yaml` reference

```yaml
title: Trust in AI-Assisted Decision Making
short_title: trust_ai           # used in all filenames — no spaces
author_initials: DCR            # your initials, appended when you revise
topic: trust in AI systems in high-stakes decision contexts
focus: the role of explainability in building appropriate reliance
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
  coordinator: qwen3.6:27b-16k   # model for drafting and revision
  worker: qwen3.5:9b-q4_K_M     # model for short structured tasks
```

`venue` and `scope` are optional. If set, they are passed to the LLM at every
stage — outline, draft, revision, and focus — to keep the output calibrated to
the submission target. Venue format specs are looked up by the worker LLM during
`init`; always cross-check them against the official call for papers.

---

## Troubleshooting

- *`[error] ollama not reachable`* → check that `ollama serve` is running and the
  URL in your config is correct.
- *No `.docx` output* → install `pandoc`. The `.md` is always written regardless.
- *`[error] no outline found`* → run `raconteur outline` before `raconteur draft`.
- *Revision not detected* → make sure your revised file is in `paper/` and the
  filename ends with your initials, not `ra` (e.g. `…_ra_DCR.docx`, not `…_ra.docx`).
- *Comments or track changes not picked up* → confirm they are saved as real Word
  track changes / comments, not just coloured text. LibreOffice and Google Docs
  can export these correctly when saving as `.docx`.
- *Want to start over* → delete the contents of `paper/` and re-run `raconteur outline`.

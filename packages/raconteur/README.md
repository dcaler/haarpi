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
| [rabbitHole](../rabbithole) | `litReview/` | literature review — all sections |
| [raster](../raster) | `<date>_methods_<chain>.md` | methods writeup — methods |
| [rayleigh](../rayleigh) | `results/` | experiment results — results |

If any of the three is missing, raconteur **warns loudly** (during `init` and at
the start of `outline`) and proceeds with reduced context — nothing is silently
skipped.

---

## 1. Install

**Option A — symlink (no pip required)**

```bash
git clone https://github.com/dcaler/haarpi.git
pip install httpx PyYAML python-docx   # dependencies only
ln -s /path/to/haarpi/packages/raconteur/bin/raconteur ~/.local/bin/raconteur
```

Make sure `~/.local/bin` is in your `PATH`. After that, `raconteur` works from
anywhere.

**Option B — pip editable install**

```bash
git clone https://github.com/dcaler/haarpi.git
cd haarpi/packages/raconteur
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

`init` makes **no LLM call** and does not require Ollama to be running — it is
purely an interactive setup step. The wizard asks for:

- **Literature review** — if `litReview/` (rabbitHole) is present, whether to use it
  as context. If absent, you get a loud warning.
- **Short title** — used in all filenames (no spaces; e.g. `trust_ai`). Offered from
  rabbitHole's `litrev.yaml` when available.
- **Research description** — describe your research in plain language. Also offered
  from rabbitHole's `litrev.yaml`. It is parsed into `topic`/`focus` later, on first
  use in `raconteur onepager`.
- **Methods writeup and results** — if raster's `<date>_methods_<chain>.md` and
  rayleigh's `results/` are present, whether to use each as context. Loud warning
  for either that is missing.
- **Author style** — required. Confirms your Zotero publication list if the style
  profile has not been trained yet. This is init's only network access.

Target venue is set by the separate `raconteur venue` command, not by `init`.

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

Each drafted section is then checked **mechanically**, not by asking the LLM's
opinion: every `[@citekey]` must resolve against `refs.bib`, Background and
Discussion paragraphs must carry citations, Results paragraphs must report actual
numbers. Failures are fed back as imperatives. The run ends with a metrics line:

```
citekeys resolved 47/47 · uncited body paragraphs 0 · sparse 2 · sections 6
```

Output: `paper/YYMMDD_shorttitle_ra.md` and `paper/YYMMDD_shorttitle_ra.docx`.
A fresh draft is a major version — new date stamp, chain reset to `ra`.

### Revising: the redline

If raconteur finds a user-revised `.docx` in `paper/` (a file whose last initials
are not `ra`), it **edits a copy of your document in place**, answering each comment
with Word tracked changes you can accept or reject. Detection is automatic.

Only the paragraphs your comments are anchored to are touched. Sections you approved
are left byte-for-byte identical, equations and citations survive untouched, and every
comment gets an explicit disposition — applied, routed, or declined with a reason.
Nothing is silently skipped.

Some comments *cannot* be a tracked change. "Add a section on X" or "run this
ablation" are reported and routed — to `raconteur outline`, or to rabbitHole,
rayleigh, or raster. raconteur will not manufacture evidence.

Output: `paper/YYMMDD_shorttitle_ra_DCR_ra.docx` — a **minor** version, keeping your
date stamp and extending the chain.

```bash
raconteur paper --resynth   # opt out: regenerate the whole draft from markdown
```

`--resynth` gives you the old clean-rewrite behaviour. It discards your comments and
produces no redline, so it is the right choice only when you want a fresh draft rather
than an answer to your annotations. It is a major version.

---

## 7. The revision loop

This is the core workflow. After each draft:

1. Open `paper/YYMMDD_title_ra.docx` in Word (or LibreOffice).
2. **Highlight the text a comment is about** and leave a **Comment** on it
   ("tighten this", "wrong citation", "this is vague"). The highlighted range is
   what raconteur uses to decide which sentences it may touch — a comment on one
   sentence will never rewrite its neighbours.
3. Save the file with your initials appended: `paper/YYMMDD_title_ra_DCR.docx`
4. Run `raconteur paper` again.

Raconteur answers each comment with a tracked change in your own document. Open the
result, accept or reject each edit, comment again, repeat.

```
260607_trust_ai_ra.docx             ← raconteur's first draft (major)
260607_trust_ai_ra_DCR.docx         ← your comments
260607_trust_ai_ra_DCR_ra.docx      ← raconteur's redline — tracked changes, same date (minor)
260607_trust_ai_ra_DCR_ra_DCR.docx  ← you accept/reject and comment again
```

A **new date stamp** means a new revision cycle (a fresh draft). Within a cycle the
date stays put and the initials chain grows, so the lineage stays readable.

Watch the log: every comment is reported as **applied**, **routed** (it needs a new
section, new sources, or a result that does not exist yet), or **declined** (no edit
could be made without dropping a citation or an equation, so your paragraph was left
alone). A comment is never silently ignored.

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

Like the redline, `focus` is a **minor version** — it appends `_ra` to the existing
initials chain and **keeps the source file's date stamp** rather than starting a new
revision cycle, so the lineage stays readable:

```
260608_trust_ai_ra.docx             ← current draft
260608_trust_ai_ra_ra.docx          ← after focus (chain extended, same date)
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

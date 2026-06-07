# Raconteur

An **offline-first paper writing assistant**. You describe your research; it
generates an outline and drafts the paper using a local LLM. You annotate the
draft in Word; it reads your comments and tracked changes and writes a revised
draft. Repeat until done.

> Four commands. Your edits drive the revision loop.

```
raconteur init     ▸ walks you through setup, writes raconteur.yaml + paper/
raconteur outline  ▸ generates a structured outline → paper/YYMMDD_title_ra.md (.docx)
raconteur draft    ▸ writes the full paper, or incorporates your Word revision
raconteur focus N  ▸ refines a single section without touching the rest
```

Works alongside [RabbitHole](https://github.com/dcaler/rabbithole) — if a
literature review is present in `litReview/output/`, raconteur reads it
automatically and uses it to inform the draft. Analysis code in `code/` is read
the same way for the methods and results sections.

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
coordinator: gemma3:27b   (drafting, revision, section refinement)
worker:      gemma3:12b   (parsing the research description during init)
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

`init` creates:

```
trust-in-ai/
├── raconteur.yaml    project config
└── paper/            all generated output goes here
```

Re-run `init` at any time to update the topic or focus; other config is preserved.

---

## 4. outline — plan the paper

```bash
raconteur outline
```

Generates a structured markdown outline from your topic and focus. If a RabbitHole
literature review is present in `litReview/output/`, it is read and used to inform
the section structure and content notes.

Output: `paper/YYMMDD_shorttitle_ra.md` and `paper/YYMMDD_shorttitle_ra.docx`.

Edit the outline in Word before drafting if you want to steer the structure — save
it with your initials appended (e.g. `260607_trust_ai_ra_DCR.docx`). `draft` will
pick it up and treat it as the revised outline. Or just run `draft` directly.

---

## 5. draft — write the paper

```bash
raconteur draft
```

Reads the latest outline from `paper/` and writes a full draft. If a literature
review or analysis code is present, both are used as context.

If raconteur finds a user-revised `.docx` in `paper/` (i.e. a file whose last
initials are not `ra`), it reads the tracked changes and comments and writes a
**revision** instead of a fresh draft. You do not need to pass a flag — detection
is automatic.

Output: `paper/YYMMDD_shorttitle_ra.md` and `paper/YYMMDD_shorttitle_ra.docx`.
`draft` always resets the initials chain and updates the date stamp (it is a major
version; the prior history lives in git).

---

## 6. The revision loop

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

## 7. focus — refine one section

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

## 8. Working with RabbitHole

If the project was also used for a RabbitHole literature review, raconteur picks
it up automatically. The expected directory layout:

```
my-paper/
├── litReview/         ← RabbitHole output
│   └── output/
│       └── project_litreview_ollama.md
├── code/              ← analysis scripts (optional)
│   ├── analysis.py
│   └── results.R
├── paper/             ← raconteur output
└── raconteur.yaml
```

Raconteur reads the most recent `.md` file in `litReview/output/` and up to
4 000 characters of code from `code/`. Both are passed to the LLM as context.
Neither is required.

---

## `raconteur.yaml` reference

```yaml
title: Trust in AI-Assisted Decision Making
short_title: trust_ai           # used in all filenames — no spaces
author_initials: DCR            # your initials, appended when you revise
topic: trust in AI systems in high-stakes decision contexts
focus: the role of explainability in building appropriate reliance
brain:
  coordinator: gemma3:27b       # model for drafting and revision
  worker: gemma3:12b            # model for short structured tasks
```

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

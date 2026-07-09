# Folding the rabbitHole rewrite into raconteur

Guidance for the raconteur dev agent, 2026-07. Written after rabbitHole's `report`/`revise`
rewrite, whose failures raconteur is on track to repeat because `paper` is the same shape:
section-by-section drafting, critique→revise per section, feeding on an LLM-injected context
blob, then a whole-document revise pass.

Read this alongside the rabbitHole source it refers to. The relevant files there:

- `rabbithole/brain.py` — `_check_context` (the truncation guard)
- `rabbithole/guards.py` — the whole mechanical-guard thesis (pure functions, no I/O, no LLM)
- `rabbithole/redline.py` — the atom-stream tracked-change engine
- `rabbithole/revise.py` — orchestration: sentence-indexed edits, fail-closed audit, disposition
- `tests/test_guards.py`, `tests/test_redline.py`, `tests/test_revise_adversary.py` — the fixtures

Do not copy blindly. rabbitHole reviews a *foundation of sources*; raconteur writes a
*manuscript*. Where the two diverge, this document says so.

---

## The polestar, translated

rabbitHole's polestar is a **verifiable foundation**: every claim traceable to a curated
source, few/unverifiable citations treated as a defect rather than a style choice.

raconteur's analog is a **grounded, verifiable manuscript**: every substantive claim traceable
to the material raconteur was given (litreview, methods writeup, results, one-pager), and every
`[@citekey]` resolvable against `refs.bib`. A Methods paragraph that describes an algorithm the
methods writeup never mentions, or a Background paragraph that cites nothing, is a defect — not
a matter of taste. Everything below serves that.

The governing discipline is **guards in Python, judgement in the LLM**: Python decides *that*
something is wrong, precisely, and states it as an imperative the model must satisfy. The LLM
decides only what cannot be computed — whether prose reads as synthesis rather than a list,
whether a claim is actually supported. Today raconteur pushes computable checks
("only citekeys from the bibliography are valid", paper.py:52) into prose the model is free to
ignore. Move them into code.

---

## 1. Silent context truncation (do this first — it is the cheapest and the worst)

`brain._call` (brain.py:40) hands `num_ctx` to Ollama with no size check. Ollama's response to
an over-length prompt is to **silently discard the head** — no error, no log. This is the single
bug that produced rabbitHole's worst run: a review that cited 15 sources, all from the last
third of the corpus, because the first two thirds were truncated away before the model ever saw
them. The prompt rules were arguing downstream of a hard cut.

raconteur is one config change away from the same failure. The `_MAX_*_CHARS` caps in
`context.py` are the *only* thing holding the line, and nothing ties them to `num_ctx` or warns
when their sum overflows. A Methods draft (paper.py:342) already stuffs `analysis` + 20 000
chars of methods writeup + outline + bib + style into one `num_ctx=16384` call.

**Port `brain._check_context` verbatim** and call it at the top of `_call`. It estimates tokens
at ~4 chars/token, reserves a fraction of `num_ctx` for generation, and when the prompt exceeds
budget prints a `[WARN]` that names the offending call site via `traceback.extract_stack()`. It
does not truncate or fail — it makes the invisible visible. That is enough; you will see it in
the log and fix the caller.

---

## 2. A guards module: `raconteur/guards.py`

Pure functions over text and the parsed bib. No I/O, no LLM, no docx. Every function returns a
list of `Finding(kind, where, imperative, section)` — a precise imperative the reviser must
satisfy, routable back to the section it came from. Model it on `rabbithole/guards.py`.

**Verifiability (run on every drafted/revised section):**

- `unresolved_keys` — every `[@key]` in the prose must exist in the `refs.bib` citekey set,
  which `context._parse_bib` already produces and then throws away. Keep that set and check
  against it. A dangling `[@smith2020]` renders as literal `[@smith2020]` in the `.docx`; catch
  it before pandoc does.
- `author_year_prose` — "Smith et al. (2020) found…" written *instead of* a `[@key]` is an
  uncitable claim. Flag it; the reviser converts it to a citekey or the LLM confirms it is a
  deliberate mention.
- `uncited_paragraphs` / `sparse_paragraphs` — a body paragraph in a Background or Discussion
  section with no citation, or fewer than roughly ⌈sentences/3⌉ citations, is thin. This is the
  mechanical floor that replaces critique check 6 ("lists rather than synthesises",
  paper.py:79), which is an LLM judgement and therefore skippable. **Scope this carefully**: a
  Methods or Results paragraph is grounded in the writeup, not the bibliography, so the citation
  floor does not apply there. Gate `sparse_paragraphs` on section kind (you already classify
  sections via `_LITREV_KW` / `_CODE_KW` / `_RESULTS_KW` in paper.py:179).

**Grounding (the manuscript analog of rabbitHole's disposition ledger):**

- For Methods/Results sections, the substance the model must not invent is in the writeup and
  the results files, not the bib. A softer, LLM-side check ("does this paragraph reference a
  specific function/parameter/value that appears in the provided source?") is fine here — it is
  genuinely a judgement — but state the imperative mechanically where you can (e.g. flag a
  Results paragraph that contains no numeral when results content was provided).

Keep the module free of docx and Ollama so it is trivially testable. Write the fixtures first
(§7).

---

## 3. Redline — the architecture shift

This is the big one, and it is a genuine change of shape, not a port.

### Why revise must stop regenerating markdown

`_revise_paper` (paper.py:370) today re-drafts **every** section from the annotation blob, runs
critique→revise ×2 on all of them, regenerates the abstract, and re-renders the whole `.docx`
from fresh markdown. Two failures fall out of this:

1. **Collateral drift.** A comment on the Discussion causes the Methods section to be rewritten
   twice. The reviewer sees changes they never asked for, in sections they approved. rabbitHole's
   hardest-won rule: *a tracked change that altered an untouched region under a reply claiming
   success is worse than no edit at all.*
2. **No redline.** The reviewer annotated a specific sentence; raconteur throws the sentence away
   and writes a new paragraph. There is no way to see what changed, accept some edits and reject
   others, or trust that the parts they liked survived. A clean rewrite is the *wrong* deliverable
   for an annotated draft.

So the revise path must **edit the reviewer's `.docx` in place** — insert `w:ins`/`w:del`
tracked changes into the OOXML — rather than regenerate from markdown. The clean-rewrite path
still has a place (rabbitHole keeps it as `--resynth`); make it opt-in, not the default.

### The atom-stream paragraph model (port `redline.py` nearly as-is)

The trap that cost rabbitHole days: **a Word paragraph is not the text in its `w:r` runs.** An
inline equation is an `m:oMath` element that is a *sibling* of the runs, not text inside them. A
naive "read `w:t`, diff, rewrite runs" approach silently deletes every equation, footnote,
field, and drawing in the paragraph. raconteur will hit this immediately — a Results section is
full of inline statistics rendered as OMML equations (this is exactly what mislead an earlier
analysis of the SchellingChords doc: the numbers were *there*, as `m:t`, invisible to a `w:t`
reader).

`rabbithole/redline.py` solves it by serializing a paragraph as an **atom stream**: prose runs
become text; each opaque atom (`m:oMath`, `w:footnoteReference`, `w:drawing`, `w:object`, field
runs) becomes a sentinel like `⟦m:1⟧`. The reviser sees `⟦m:1⟧` and must reproduce it exactly;
it never authors an atom. On write-back, sentinels are re-laid as **accepted** content
(siblings of the redlined prose), never wrapped in `w:ins`/`w:del`. The invariant: *raconteur
never authors an equation; it only edits the prose around one.* Guards enforce it —
`dropped_sentinels` (an atom the model failed to reproduce) and `invented_sentinels` (one it
made up) both fail the edit closed.

This code is tool-agnostic OOXML manipulation. Port it with minimal change. Reuse its
`serialize_paragraph`, `tracked_replace_sentencewise`, `comment_spans`, `anchored_sentences`,
and `comment_anchors`.

### Sentence-indexed minimal edits

The reviser does not rewrite the paragraph. It receives the paragraph split into numbered
sentences, with the annotated ones marked, and returns a JSON map of **only the sentences it
changed**:

```json
{"2": "The revised second sentence [@smith2021].", "5": null}
```

`null` deletes; an absent index means *untouched, copied byte-for-byte*. This makes minimality a
set operation, not a hope: `minimal_edit_violation(touched, anchored, n_sentences)` fails the
edit when the model touched a sentence no comment anchored to (unless the whole paragraph was
legitimately in scope). A comment on sentence 2 cannot rewrite sentence 4. This is the
mechanical answer to the collateral-drift problem, and it is why the annotation blob must stop
being global.

### Anchoring comments to sentences (kill the global blob)

`build_revision_context` (revise.py:64) today concatenates *all* deletions, insertions, and
comments into one string handed to every section with "apply only those relevant; ignore the
rest." That leaves routing to the model, and it is why every section gets rewritten.

Replace it with `comment_anchors`-style extraction: for each comment, find the run range it
spans (`comment_spans`), map that to a set of sentence indices within a specific paragraph
(`anchored_sentences`), and carry the paragraph's section identity. Now each comment reaches
exactly the sentences it touches, in the section it belongs to, and every other sentence in the
document is provably untouched.

### Fail closed

If the reviser returns malformed JSON, drops a citekey, drops or invents a sentinel, or exhausts
its retry rounds, **do not write a tracked change.** Leave the reviewer's sentence as-is and say
so in the reply. rabbitHole's rule, learned the hard way: a broken edit under a reply that says
"done" is the worst outcome — worse than a visibly skipped comment. `revise._redline_para_adversary`
fails closed on both rounds-exhaustion and audit exception; mirror that.

### What is different for raconteur (the glue you must write)

rabbitHole's narrative is a flat run of body paragraphs. raconteur's `.docx` has a title,
an abstract, `##` section headings, `###` subsection headings, and a References section. The
redline must:

- **Identify body prose paragraphs only** — never redline a heading, the title, or the
  References list. Skip them the way `paper._is_references` and `_parse_sections` already reason
  about structure, but at the OOXML paragraph level.
- **Map each body paragraph back to its section**, so the reviser gets the right context bundle
  (that section's outline, the bib, and the methods/results content `_context_for_section`
  selects). A redline of a Methods sentence needs the methods writeup in context; a Background
  sentence needs the bib.
- **Never touch the abstract or references via redline** — those are regenerated, not annotated
  sentence-by-sentence. Decide this explicitly.

---

## 4. Routing: not every annotation is a redline

Some comments cannot be satisfied by an in-place edit. rabbitHole learned to classify each
comment and route it:

- *"tighten this sentence", "wrong citation", "this is vague"* → **redline** (in-place tracked
  change).
- *"add a section on X", "you're missing the whole literature on Y", "run this ablation"* →
  **cannot be a redline.** A tracked change cannot add a section or a source that does not exist.
  rabbitHole routes these to a fuller regeneration step (`report`) and, when new sources are
  asked for, to a `gather`/`ingest` step first. raconteur's analog: route "add a section" to a
  targeted `outline`+`paper` regeneration of that section, and "this needs a result/method that
  doesn't exist yet" to a loud flag telling the human the upstream tool (rayleigh/raster) must
  run — raconteur cannot manufacture evidence.

Give every comment a disposition and a reply. Silence is not a decision: a comment that was
neither applied nor explicitly declined is a defect in the revise pass itself.

---

## 5. The scoping rule (which guards run where)

Keep this straight or the guards fight each other:

- **Breadth/density/grounding guards run on drafting** (fresh `paper`, section regeneration).
  There, more citations and fuller grounding are the goal.
- **Minimality guards run on redline only.** There, *collateral change is the defect* — the
  edit must be as small as the annotations demand. Do not run a "this paragraph is too sparse"
  guard during a redline; the reviewer did not ask you to add citations to a sentence they only
  asked you to tighten.

A guard that is right for drafting is wrong for redlining, and vice versa. Tag each guard with
the phase it belongs to.

---

## 6. Observability

- **Line-buffer stdout.** raconteur logs to stderr (line-buffered by default, so the invisible-
  log problem is milder than rabbitHole's), but the brain's tok/s stat and anything on stdout
  are still block-buffered under a redirected run. Port rabbitHole's `cli._line_buffer_output`
  and call it first thing in `main`. Cheap insurance against "is it working or hung?"
- **Print a metrics line at the end**, the manuscript analog of rabbitHole's polestar line:
  `citekeys resolved N/N · uncited body paragraphs K · sparse M · sections G`. One line that
  says, mechanically, whether the deliverable met the bar. Put it in the log and, ideally, in
  the document header.

---

## 7. Order of work, and write the fixtures first

rabbitHole's guard battery found *more* defects than a careful human read did, because it was
built against a real annotated document as a fixture before any prompt was touched. Do the same:

1. **`_check_context` into `brain.py`.** Half a day. Ship it alone; it will start warning
   immediately and tell you which callers are already over budget.
2. **`guards.py` + `tests/test_guards.py`.** Build the verifiability guards against a real
   drafted paper `.docx` from an existing project. Confirm they reproduce, by machine, the
   citation defects you can find by hand — then wire them into the draft path.
3. **`redline.py` + `tests/test_redline.py`.** Port the atom-stream engine. The critical
   fixtures: a paragraph with an inline equation must survive a sentence edit *as accepted
   content* (assert `w:ins`/`w:del` counts and that the `m:oMath` is a direct child of `w:p`,
   not stranded inside a tracked change); an untouched sentence must be byte-for-byte identical.
4. **Rework `revise.py`** onto sentence-indexed minimal edits with comment→sentence anchoring,
   failing closed. This is where the global annotation blob dies.
5. **Routing + disposition + metrics line.**

Steps 1–2 are independently shippable and pay for themselves immediately. Step 3 is the largest
and the one with the sharpest teeth — do not shortcut the fixtures.

---

## Two things that are *not* problems in raconteur (so you don't chase them)

- **Style retrain-forever** was a rabbitHole bug because `report` auto-invoked training every
  run. raconteur's `paper` only *reads* `style_profile.md`; training is the separate `style`
  verb. There is a latent cousin — `style.py:138` records a paper key only on fetch success, so
  the idempotency check in `style.run` can be unsatisfiable for a PDF-less paper — but it does
  not fire on every paper run. Low priority.
- **The clean rewrite itself** is not wrong; it is just the wrong *default*. Keep it as an
  explicit `--resynth`-style option for when the human wants a fresh draft rather than a redline.

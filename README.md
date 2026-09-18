# HAARPi

**Human Authored Agentic Research Pipeline** — a monorepo bundling the `ra*`
research tools, which carry a research idea from literature review through
preregistration, model building, experiments, and manuscript to a
venue-specific presentation deck — with a human reviewing at every gate.
Scheduling runs on [trundlr](https://github.com/dcaler/trundlr).

The name is the claim: the research is **human authored**. The agents gather,
draft, build and redraft, but every stage ends at a document a person reads and
marks up, and no stage advances until they do. The pipeline's job is to make
that markup cheap to act on — not to remove the person from the loop.

**Offline-first is a defining goal, not a feature.** The pipeline's working
loops — gathering, synthesis, building, experiments, drafting, revision, and
deck authoring — run on local models via Ollama, on your own hardware; a
research project never needs to leave the machine. Cloud models appear only as
explicitly-optional, human-invoked deviations (an A/B coordinator swap in
rabbitHole; the interactive design sessions in ramus, raster and rayleigh; razzle's
`deck --claude`), never as shared plumbing and never on an automated path.

## The pipeline

| Stage | Tool | Works in | Produces |
|---|---|---|---|
| literature review | [rabbitHole](packages/rabbithole) | `litReview/` | an organized collection of facts + contribution map |
| experiment design | [ramus](packages/ramus) | `design/` | the preregistered experiment design |
| model building | [raster](packages/raster) | `code/` | a built, tested code repo |
| experiments | [rayleigh](packages/rayleigh) | `results/` | preregistered findings + write-up |
| paper | [raconteur](packages/raconteur) | `paper/` | the manuscript, revision by revision |
| deck | [razzle](packages/razzle) | `slides/<venue>/` | venue-specific presentation decks |

The experiment **design** (preregistration) is committed *before* any code is
built — you fix the experiments, then build to satisfy them, never the reverse.
Those are two different jobs, so they are two agents, one either side of raster's
build. **ramus** decides what would count as an answer; **rayleigh** finds out.
The names carry it: Petrus Ramus's project was *method*, laying a subject out
systematically before reasoning from it, in diagrams that branch — which is what
the framework schematic is. Lord Rayleigh's was measuring nitrogen two ways,
finding a 0.5% discrepancy, and refusing to write it off.

## How a stage works

Every stage runs the same loop, and the figure below is that loop drawn out for
all six. Amber is the human's step, indigo is the agent working alone, purple is
HAARPi acting as conductor.

![Inside each agent — the process every stage runs, and who acts at each step](figures/agentDrilldown.png)

*(Wide diagram — open [the SVG](figures/agentDrilldown.svg) to read it at size. Amber is the
human's step, indigo the agent working alone, purple HAARPi as conductor, green a clean gate.
Solid = the work moves on; dashed grey = the step produces that artifact; dashed purple = the
revision cycle.)*

The cycle is always the same four beats:

1. **The agent produces a deliverable** — a `.docx` with comment threads intact.
2. **A human reads and marks it up.** Accepting a change and resolving a thread
   are human-only actions. No tool does either, ever.
3. **`haarpi next` reads the markup** — it decomposes the *unresolved* comments,
   builds the chain those comments require, and queues it on trundlr.
4. **Either the release is minted** (all comments resolved) **or the rework
   runs** and lands back at step 2.

There is no completion tracking across cycles and no escalation. The human is
the verification loop, deliberately: each `next` is a fresh reading of the
current markup.

Every queued step names the resource it occupies — the GPU, the CPU, you, or the
Claude agent — and there is no default, so a step cannot be added without saying
where it runs. What decides whether a runner picks a task up is that resource,
not whether the task carries a command: no runner polls the human or the Claude
resource. So an **attended** step — the interactive design, build and review
sessions, which launch a Claude session in the project root — books you *and*
Claude, and still carries its command, because the person opening it should not
have to retype a verb the task already knows.

### Rework is scaled to the ask, not to the heaviest ask in the set

This is the load-bearing design decision, and it was learned the hard way.

`haarpi next` decomposes the markup **one comment at a time** into what each one
asks for — `edit`, `sources`, `section`, `ingest`, `cite`, `correct`,
`redirect` — and then *builds* the chain from that task set rather than looking
one up from a template. Corpus-level work (`gather → collect → audit → build`)
is unioned once; per-comment work is applied one comment at a time, in a single
pass, with each response matched to its own ask:

| the comment asks for | it is answered by |
|---|---|
| a prose change (`edit`) | a tracked, sentence-level rewrite of the paragraph it sits on |
| a new section (`section`) | a section planned before the gather, then drafted and spliced in at the comment that asked for it |
| papers already in Zotero (`cite`) | those citekeys worked into the anchored paragraph |
| references not yet in Zotero (`ingest`) | fetched, then finalised by the human at a `collect` |
| a wrong fact (`correct`) | a deterministic substitution across the brief, the config **and** the document |
| a section the corpus cannot carry | nothing drafted, and a reply naming what was missing |
| something prose cannot satisfy | no edit, and a reply naming the real reason |
| a change of direction (`redirect`) | the brief is rewritten and the whole document re-planned |

Only a `redirect` re-plans the whole document. It rewrites the brief, which
invalidates the premise of every other comment in the set, so cascading there is
correct rather than lazy. Everything else is answered in place.

**One comment is one ask.** Three "add a section on X" notes left on the same
heading are three requests, not one — grouping them by where they were written
fused them into a single ask, drafted one section for it, credited that section
to all three in identical replies, and left a second section belonging to
nobody.

**Sections are planned before the gather that has to find their literature.**
The heading and one-sentence claim are decided in `haarpi next`, so the search
runs on what will be written rather than on how it was requested — a reviewer's
prose carries asides and framing that make a poor query. They land in the plan
record too, which makes `haarpi next --dry-run` a few minutes' read standing in
front of a multi-day chain: it names every section the cycle intends to write
and every topic it will search, and writes nothing.

The failure this replaced is worth recording, because it is the obvious design
and it is wrong: rework used to be scaled to the *heaviest* need in a set. One
"add a section" comment sent the entire annotation set to a verb that could not
carry an in-place edit, so the edits beside it were dropped — silently, with no
reply. A no-op with no reply is indistinguishable from success from the
reviewer's side. Every comment now gets a disposition and a reply derived from
what actually changed.

### The redline contract

Deference is owed to the author's **spans**, not to their paragraphs. When a
human has written or edited text, the tool preserves those exact atoms; it does
not "improve" them while rewriting around them. Concretely:

- Existing paragraphs are never passed to a model unless a comment asks for a
  change to them. A grafted section leaves every other paragraph byte-identical.
- Edits land as **tracked changes** the reviewer can reject, never as settled
  prose.
- Comment threads survive every rework verb, so the reviewer reads a diff rather
  than a new document.
- **Accepting and resolving are human-only.** Tools reply to threads; they never
  close them.
- Guards run on every write path: a revision that would drop a citation or an
  equation is refused, and the reviewer is told why rather than handed a
  fabricated fix.

## The stages in detail

### 1 · Literature review — rabbitHole

**The literature review is a coverage instrument, not a draft chapter.** It is
not what you would drop into a paper; it is an organized collection of facts,
some of which feed one later, and its job is to let a human decide whether the
corpus is good enough to start the work. So it is built to be *checked* rather
than read through.

It opens with the **most load-bearing sources** — the top 5% of the corpus by
how much of the review's argument each one carries, with a sentence on what the
project relies on it for. Below that sits an annotated bibliography where every
claim is page-located in its source, and beside it a **contribution map** that
bands the same sources at the 5% / 25% / 50% marks, so the map's innermost ring
and the opening list are one ranking seen two ways. The prose carries the
through line that organizes those facts — the same structure the map draws
radially.

Reference targets are a diagnostic band, never a cap; a review is expected to
exceed them when the work asks for it.

**A project can hold more than one review, because one anchor cannot serve two
questions.** The substantive review is anchored on the *domain*; a methods review
is anchored on the methodological families and usually excludes that domain
outright. One project spent five steering rounds trying to get sequence-analysis
methodology out of a review anchored on innovation policy and could not — the
methodology lives in life-course sociology and demography, outside the anchor and
adjacent to an excluded field. Its minted review says "sequence analysis" 42
times and names two concrete techniques. The failure was structural, and no
amount of steering addresses it.

So a review is a **kind**: a folder, a config stem, and a deliverable infix.
`litReview/litrev.yaml` mints a `litreview`; `litReviewMethods/methodsreview.yaml`
mints a `methodsreview`. The infixes differ because `methods` already belongs to
raster's build writeup, which raconteur finds by a root-level glob — a methods
review sharing that name would be read as the writeup.

**One Zotero collection per project, forever, and nothing is ever moved out of
it.** A corpus ledger beside the project records two independent things about
each source:

- **purpose** — what it is *for*: `literature`, `methods`, or **both**. A paper
  on sequence analysis applied to funding pathways is methodological work and
  substantive work at once, and a single collection is pointless if it cannot say
  so. Written by `gather`, from the review that found it; *added to*, never
  replaced, so nothing takes the first purpose away.
- **status** — whether it is *in* the corpus at all: `corpus` or `quarantine`.
  Written by `audit`, reversible, and lockable against a person's decision.

Those are different questions — provenance against disposition — and keeping them
in one field cost the provenance: quarantining a methods paper made the ledger
forget which review it belonged to. Ingestion asks one question of a row: does it
serve *my* review, and is it in the corpus. That is what lets two reviews share a
collection without swallowing each other's sources, and it keeps `refs.bib` the
union bibliography, correct by construction — every source any review has ever
cited is still in it. Zotero subcollections cannot do this job; neither items
endpoint recurses into them.

`gather` writes a row for what it finds, so the common case needs no decision from
anyone. Anything in the collection without a row is by construction something a
person added, and `collect` — where you download the PDFs and file them — codes
those in, defaulting to the purpose of the review you are standing in and
reporting which sources still lack a PDF.

| verb | does |
|---|---|
| `init` | the brief interview → `litrev.yaml` |
| `gather` | searches, ranks and curates candidates into the collect-list; each reviewer ask gets its own queries and its own yield line |
| `collect` | *(human)* adds each real source to Zotero **with its PDF** |
| `ingest` | pulls reviewer-supplied references into the corpus |
| `audit` | quarantines lexical false-friends by word sense — reversibly |
| `build` | embeds the audited corpus (candidates, citekeys, ChromaDB, notes) |
| `report` | generates the first review — and `refs.bib`, and the embedded corpus; re-plans on a `redirect` |
| `revise` | answers every comment in kind (see the table above) |
| `graft` | **vestigial** — nothing calls it; its drafting lives inside `revise` |
| `refresh` | recomputes the load-bearing block on an existing draft |
| `mindmap` | regenerates the contribution map beside each new draft — reading the `.md`, or the tracked-change `.docx` when a redline revise is the draft |
| `style` | trains an author-voice profile from the author's own publications |

Two invariants worth knowing. **`build` is the embedder the re-draft path needs** —
`revise` reads a cached corpus and never embeds, so every rework chain that changes the
corpus carries a `build` before it. `report` is not an exception so much as outside the
rule: it calls the same builder inline, which is why the opening chain has no `build`
step and still produces an embedded corpus. And **`collect` is a human step on purpose**:
a person confirming each source exists, with its PDF, is what guards the corpus against
hallucinated citations.

The `audit` verb catches **homographs** — a paper that matched the search on a
word its own literature uses for something else entirely. `agent` in agent-based
modelling against `agent` meaning a chemical reagent; `docking` of adaptive
agents against ligand-receptor docking. Those are not delicate distinctions, and
that bluntness is the point: a false friend is obvious, and anything needing fine
weighing is not one.

Two things it deliberately does **not** do, each learned by doing them.

**It does not judge by discipline.** A statistical-physics paper analysing the
Schelling segregation model means by that term exactly what the review means.
Cross-disciplinary work is what a literature review exists to find, so dropping
it is the most expensive mistake available here — judge the word, never the
field.

**It is not a relevance test.** Whether a source is useful, central, or worth the
space was settled by `rank` before this verb ran. A paper squarely in the
subject that is merely less useful is a keep, as is one the review would cite in
passing, disagree with, or use only as an example. Two earlier versions handed
the model the review's stated needs — first fused with the author's own
statement, then one ask at a time — and both turned the word-sense check into a
relevance judgement. The first quarantined 43 of a corpus's first 49 papers at
9/10; the second still took 54% of it, Schelling scholarship included, fetched by
a search for Schelling scholarship. Almost none were homographs. The needs list
no longer reaches the model at all.

Quarantine is a **status in the corpus ledger**, never a move and never a delete.
The item keeps its place in the project's Zotero collection, so it stays in
`refs.bib` — taking it out was how this verb could hand a minted review a
dangling citation, silently, from a command nobody thought of as touching
bibliographies. `--release` returns the status to `corpus` and *locks* it, so a
later audit cannot overrule a person. The source's purpose is untouched, which is
what returns a released methods paper to the methods corpus rather than to
whichever review the release happened to name; releasing used to move the item
back and leave the verdict cache alone, which made the decision last exactly until
the next run.

Verdicts are cached per paper against the question, so a normal cycle judges
only what is new; a question that merely *gained* an ask keeps every paper it
already accepted, because widening cannot make a transferring paper stop
transferring. A change to what a verdict *means* is versioned separately and
discards the cache outright — that widening rule reasons about the question's
wording, and cannot see when the test itself has moved.

**A run has to be able to say it found nothing.** Every stage can behave
correctly and the composition still fail: a search returns a healthy total while
returning zero on the one topic the cycle exists to cover; a shortlist is a
*ranking*, so it always has a top however far away that top is; and a drafter
handed the twelve nearest sources writes a sound section out of them and reports
success. Three places therefore report absence rather than a plausible
substitute. A reviewer's ask gets dedicated search queries — phrased in the
sub-topic's own vocabulary, since the literature says "consumption smoothing"
where a reviewer says "households withdraw from their savings" — and its own
found/curated count, so an ask that brought nothing in says so. Before drafting,
the same word-sense question `audit` asks at intake is asked of the section's
evidence: a section on supply-chain decoupling is *not* supported by papers on
decoupling growth from emissions, however often they say "decoupling". And a
section that fails that check is not written; the reply names what was missing
and what the corpus holds instead.

### 2 · Experiment design — ramus

A live session settles the analytical framework — too open-ended to default — and
writes `designdocs/PLANNING.md`, `EXPERIMENTS.md`, a `PRIORS.md` index of the project's
earlier `ra*` artifacts, a framework schematic into the figure pool, and the prereg
`.docx` the gate reads. It **specifies only**: the executable `experiments.yaml` is
authored later, by `rayleigh plan` in the experiments stage, against the code raster
actually built. The human redlines the design; any severity of comment re-opens the
session rather than patching the document, because a design is a set of decisions, not
prose.

This stage is **preregistration**: it is released before the code that satisfies
it exists.

### 3 · Model building — raster

A live session authors `DESIGN.md` and checks the plan, then decomposes it into
tasks and freezes the test suite. From there a local-LLM doer implements each
task against its frozen unit test, climbing an escalation ladder on repeated
failure rather than editing the tests to pass. Module gates keep the frozen tree
green as modules land.

The tests are written first and frozen because the alternative — a model that
can edit its own acceptance criteria — has exactly one failure mode and it is
silent.

### 4 · Experiments — rayleigh

Runs each preregistered experiment's cells against the built code, processes
outputs into `findings.json` and data figures, and writes the results up. At the
gate, a **cosmetic** comment (presentation only) re-runs `process`: no new data, so
nothing is re-run — the analysis script and the tidy table are durable, and the report is
assembled again around them. A comment needing cells, seeds or experiments that do not
exist yet is an **extend**, and goes to `rayleigh review`, an attended session that
decides which layer diverged and queues its own follow-on chain. The line between the two
is whether answering it requires observations that do not exist yet.

### 5 · Paper — raconteur

The manuscript climbs three gate cycles: **one-pager → outline → draft**, each
released before the next begins.

- **one-pager** — the narrative through-line, in one page, approved before any
  structure is planned.
- **skeleton** then **outline** — phase one plans the sections, subsections, and
  the words each can afford; phase two adds the content beats to the *approved*
  skeleton.
- **draft** — the full manuscript, written from the releases upstream.

At every rung the same four routes apply, and only where they re-enter differs: a prose
comment is answered in place on that rung, a structural one re-runs `outline` (or, on the
skeleton rung, phase one), a narrative one always re-cuts the one-pager however far down
the ladder it was raised, and an **upstream_literature** comment escalates out of the
stage entirely — back to gather, collect, report and comment in the literature review,
because the claim it doubts is not in the corpus yet. `package` assembles and compiles
the venue submission.

**A Methods section cites the methods literature when there is any.** raconteur's
only methods input used to be raster's writeup — what *this project's code does* —
which can say what was done and cannot say whose method it is, which published
debate settles a parameter choice, or what the known objections are. The citation
floor excluded Methods outright and the draft prompt said there was no requirement
to cite there, so the section was built not to cite at all. A project with a
methods review now gets it as a second input, and the floor applies; a project
without one drafts exactly as before, since a floor there would fail a section for
missing sources nobody ever gathered.

Which release feeds which section is the second figure:

![Information-flow map — what each release feeds in the paper](figures/paperInflow.png)

*(Source: [`paperinflow.py`](figures/panels/paperinflow.py) ·
[SVG](figures/paperInflow.svg). Solid = the section's prose is
written from this source; dashed = it supplies an asset placed there; dotted =
summarised into the abstract, which is written last.)*

### 6 · Deck — razzle

An interview captures the facts a tool must never invent — format, venue, date,
who is presenting, affiliation logos, funders. **Which** deliverables exist is
itself that choice, so the deck is the one stage that cannot queue its own work
when it opens: the interview is followed by `haarpi next` — the way every other
chain in the pipeline ends — and that queues one authoring chain per format,
by which time the config is on disk. Each format then gets its own title, its
own duration history, and its own pass through the gate.

A deck lives in `slides/<venue>/`, the way a manuscript lives in
`paper/<venue>/` — the venue is what the work is *for*, and the presentation
format is a property of the talk rather than a way to find it. Authoring runs
on the local coordinator like every other working loop: gather, compose,
render, in one process, with no session to sit at. The spec is
normalised on the way into the render, so the slide budgets hold whoever wrote
it. Deck masters, logos and any master-format
descriptor live outside the repo in `~/.config/haarpi/razzle/` and are never
committed.

A slide here is a projected image with a claim over it, not a document, and the
budgets are enforced rather than suggested: a title is the slide's claim in nine
words or fewer, a slide carries at most three bullets of at most nine words, and
there are **no speaker notes** — what will not fit is spoken, not written down.
Slides come in four authored roles, and `split` (a point beside its figure) is
the workhorse, because a deck goes text-heavy when a slide can show evidence
*or* make a point but never both.

The notes pane has exactly one use, and it is not speech. A talk always has more
slides than the paper has figures, so a slide with nothing to show may carry an
**illustration** brief instead: one line describing the picture it wants,
rendered into the notes as a production TODO for whoever draws it. A request for
art, not a script.

What the composer is not allowed to write, razzle stamps: the **paper's title**
on the title slide (a talk is the paper, so its name is read, not invented — and
the footer is built from it), every **author** credited with exactly one contact
address, the presenter's, and a closing **acknowledgements** slide pairing each
author with their own affiliation above the affiliation and funder marks. That
last is where the logos live — a title slide's job is the title, the authors and
the venue — and the opening slide alone carries no page number.

Four things are fixed rather than trusted to a prompt. A **citation beside one of
our own figures** goes: every figure in the pool is this paper's work, and a
literature reference in the caption strip under it reads as *this figure is
theirs*. A **bullet whose words are a subset of the title** goes: the title
already made the claim. A **spelled-out sign** becomes a symbol (`plus 0.176` →
`+0.176`), because on a slide read from the back of a room a word where a symbol
belongs costs a beat. And a `figure` slide that arrives **carrying bullets**
becomes a `split`: the figure role's content area *is* the picture, so bullets
written onto one would be dropped without a word.

The deck's running text — venue and date, the footer, the contact — is
deck-level, so it never enters the spec: the composer cannot invent a venue it
was never shown. The footer may carry a **short title** from the deck config
("Sense of Schelling" for "A New Sense of Schelling Segregation") — an editorial
call about one's own talk, so it is asked in the interview, never inferred. The
footer and the contact are one line, so they are laid out as one strip at one
size read from the master: the boxes move before the type does, the contact
keeping its right edge and growing left into the footer's slack, and the size
drops only when the whole strip will not fit, for both halves at once.

## The shared core

The [haarpi](packages/haarpi) package is what the tools have in common:

- **the umbrella CLI** — `haarpi init / next / status / queue / authors /
  doctor`, plus `haarpi <tool> <verb>` to reach any stage tool
- **the planner** — the sole litreview planner: decomposes markup, builds
  chains, queues them, mints releases, advances the stage ladder
- **the redline engine** — tracked-change surgery, comment anchoring, threaded
  replies, and the guards, with per-tool policy on top
- **the style engine** — author-voice training shared by rabbitHole and
  raconteur, with the profile kept in `~/.config/haarpi/`, outside any repo
- **the trundlr client**, the figure engine, notifications, the document naming
  chain, and pandoc rendering
- **run logging** — every line stamped, and every run teed to
  `.haarpi/runlog/<stamp>_<verb>.log` in the project. A verb that runs for hours
  leaves a record of what it decided; the task queue keeps only a few kilobytes
  of tail, which is not enough to reconstruct a cycle afterwards.

The context window is **sized to the prompt, never the other way round**. Ollama
answers an over-length prompt by silently discarding the beginning of it — no
error, no log — so a call that asks for more than its window gets a confident
answer founded on whatever survived. A window a caller names is that caller's
estimate of how big the prompt will be, and an estimate that turns out wrong is
not a licence to throw evidence away: the window grows to fit, and the run says
so. Only when the hardware cap itself cannot hold the prompt is there nothing to
do but warn, because then the prompt is genuinely too big and the caller has to
divide it. Warning alone was tried and is not enough. `audit` pinned a window
sized for a one-line focus string, and when the question became the author's
full research prompt it printed that warning once per paper for 154 papers while
judging every one of them against a question it could no longer see.

The vector index is **worked on from local disk and written back**. It is a
SQLite store, project trees live on a network share, and SQLite's locking there
is a round trip per operation through the server's lock manager — reliable
almost always, and indexing a corpus makes thousands of those operations. Only
the indexing pass stages and writes back; the read-only passes stage and never
do. An advisory lock beside the store keeps two writers from overwriting each
other, and a refused writer works on the share instead of failing.

### The document revision chain

Every deliverable is named so that its history is legible from the filename:

```
260815_elephantRoom_litreview_ra.docx            ← the tool's draft
260815_elephantRoom_litreview_ra_DCR.docx        ← the human annotated it
260815_elephantRoom_litreview_ra_DCR_ra.docx     ← the tool answered
260815_elephantRoom_litreview_ra_DCR_ra_DCR.docx ← and so on
```

The `YYMMDD` prefix marks a major revision cycle; a new datestamp starts a fresh
chain. The trailing initials record who last touched the file — `ra` for the
tool, the author's initials for a human. Tools find the file to work on by
looking for the most recently modified one whose trailing suffix is *not* `ra`.

## Install

```bash
git clone https://github.com/dcaler/haarpi.git
cd haarpi
uv sync            # one venv, all six CLIs
```

Each tool remains individually installable (`pip install -e packages/<tool>`)
and individually usable — the monorepo shares machinery, not opinions.

You will also need [Ollama](https://ollama.com) for the local models, `pandoc`
for document rendering, `graphviz` for the figures, and a Zotero library with
API access for the literature stage. Configuration lives in
`~/.config/haarpi/config.toml`.

## Use

```bash
haarpi init                  # one interview → manifest, scaffold, first chain
haarpi status                # what is released, in flight, unlocked, stale
haarpi next                  # read the markup: mint a release, or queue rework
haarpi rabbithole gather     # or drive any stage tool directly
```

`haarpi next` runs automatically as the last task of every queued chain, so in
normal use the loop is: read the document, mark it up, mark the task done.

## Repo layout

```
packages/
  haarpi/       shared core + umbrella CLI + the planner
  rabbithole/   literature review
  ramus/        experiment design (preregistration)
  rayleigh/     experiments — conduct, process, review
  raster/       model building
  raconteur/    the manuscript
  razzle/       presentation decks
figures/        the two architecture figures above, with their .dot sources
```

### Keeping the figures true

The drill-down is **derived, not drawn**. Each stage is a panel under
`figures/panels/`, laid out arithmetically and emitted as SVG — three fixed columns, one row
per step — then the six are stitched into one figure. Rebuild the whole thing with:

```bash
python figures/panels/build.py
```

That much is only reproducibility. The part that matters is that a panel **declares which
registry steps it depicts**, and `test_figure_drift.py` checks that claim against
`planner.STAGE_STEPS` and `STAGE_TIERS` on every run:

- a verb added to the registry and not drawn fails the suite
- a verb the figure shows that the registry no longer has fails the suite
- a step drawn amber whose `Step.command` is no longer `None` fails the suite
- a figure the README embeds that isn't in the repo fails the suite
- a verb in a tool's CLI that this README's table omits fails the suite, and vice versa
- a need in the decomposition vocabulary this README never mentions fails the suite
- a `haarpi` verb missing from the CLI line above fails the suite

A deliberate omission is allowed, but it has to be written down: `graft` is absent from the
literature-review panel because nothing calls `graft.run()` any more, and that reason lives in
the panel's `OMITS`, where the next person will look.

Install the hook that runs this at commit time, so a change to a verb cannot also leave the
picture of it stale:

```bash
git config core.hooksPath .githooks
```

What no test can check is whether a *sentence* is still true. That surface is deliberately
small — the prose, not the structure — and it stays a human's to read.

**The information-flow map is covered too.** It is a different shape — bipartite, not a grid —
so it has its own layout function sharing the same primitives and visual language, and the same
`build.py` emits it. Its claim is narrower but just as checkable: the paper stage declares which
stages it may read, and the map must show a source for each of them and none it may not.

That check earned its keep immediately. The hand-drawn version showed the **preregistration**
feeding Methods and Discussion — and it does not. `raconteur.context` loads the literature
review, the methods writeup, the results digest and — where a project has one — the methods
review, and nothing else, which is exactly what `project.DEFAULT_STAGES["paper"]["inputs"]`
declares. The figure had been asserting a data flow that the code has never had.

## History

Four of the tools began as standalone repos (`dcaler/rabbithole`, `raconteur`,
`raster`, `rayleigh`), now archived and private; their full histories continue
here under `packages/`. razzle was born in the monorepo, and ramus was split out
of rayleigh once it was clear the two halves of the experiment workflow do
opposite jobs — one fixes what would count as an answer, the other finds out.

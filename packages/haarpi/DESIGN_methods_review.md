# The methods review: an argument followed, not a topic summarised

Drafted 2026-11-13 with Cale, for markup. Nothing here is built yet. The first test is a bench on
DigiPros' three bootstrapping families (M4–M6), and nothing is ported until that bench is judged.

## What it is for

A choice of method has to be defended at review, and a method is defended against the argument
around it, not alone. The interest is almost wholly in the procedure: "so-and-so did X in this
way". Alternatives and criticism weigh as much as the procedure itself (Cale, 2026-11-02).

A methods literature is a debate carried across papers. Two-way fixed effects was standard for
difference-in-differences. Goodman-Bacon showed that under staggered adoption it averages
comparisons, some with negative weight, and is biased when effects change over time. Callaway &
Sant'Anna, Sun & Abraham, de Chaisemartin & D'Haultfœuille and Gardner each answered that. A
newer working paper extends the answer to a continuous treatment, but it is not yet in an R
package. Each paper matters for **whom it answers, on what grounds, and what answered it**.

A project's choice is made against that argument: which critiques apply to our data, which
answer we use, what that answer still leaves open, and whether it is implemented. The review's
job is to get the argument right. The choice is not its job.

It can be small (six bootstrapping questions) or large (a sprawling agent-based-modelling
validation literature), so nothing in it may assume a small corpus.

## What the old review did, and why it goes

`ramus init` scope → `gather methods` (one ask per family, citers searched for critiques, a
"shows how it is done" score) → collect → `build` → `report` (a narrative docx, per family: The
options / Against each / The choice / For the SI) → redline → `ramus design`.

DigiPros (261103): 21 candidates, 2 reached the corpus, and the report still wrote six families
in four parts. The options and the critiques came mostly from the model's own knowledge, and the
choice asserted facts about the project it had no basis for. The narrative form hides exactly
this. It goes: `report`, `revise`, `graft`, `refresh` and the methods `mindmap`, the redlined
methods docx and its `comment` step, and the choice written unattended.

## The structure

### 1. Scope

The `ramus init` session names the families in `METHODS_SCOPE.md`: per family, where the
project uses it and what must be defended. A joint session's output is not redlined (decided
2026-11-13): the scope goes straight to the search, and the prereg from `ramus design`
straight to release.

### 2. Search: follow the argument until it stops

Kept from today: one search ask per family, the procedure score (what a source shows about how
a procedure is done or where it fails, not what its study is about), and the citer search for
critiques. Added:

- **Following, iterated.** From a procedure, its citers that argue with it (critique,
  correction, comparison, extension). From each of those, their own citers that answer them.
  Back to the original of anything the argument cites and the corpus lacks. A family is done
  when a round of following finds no new critique or answer; a paper budget per family is the
  backstop (decided 2026-11-13).
- **The frontier.** The newest answer has few citers yet, so following misses it. Per family,
  also search by recency: the newest work by authors already in the lineage, and the newest
  papers naming the procedure.
- **Working papers in.** For a methods review, NBER, arXiv and SSRN working papers are where
  the frontier is (`include_preprints` on), labelled as such.
- **audit**, as in the literature review, before build: word-sense false friends out (the
  DigiPros list had a DNA Gibbs-sampling alignment paper under "sequence").
- **A collect check.** Every paper the person collects reaches the corpus, or the run says
  which did not and why (DigiPros lost 19 of 21 somewhere).

### 3. Cards: exchanges, with their targets

The same card store and text reading as the literature review (`ground.py`): a paper is read
once and serves every project. What is extracted differs. A record is an **exchange**:

| kind | says | target |
|---|---|---|
| proposes | B introduces procedure Y (for problem G) | — or the critique it answers |
| criticises | B shows procedure X fails under condition G (by proof, simulation or example) | X, and the paper it comes from |
| answers | B's Y fixes G, a failure of X | the critique, and X |
| compares | B compares X and Y under condition H, and finds … | X, Y |
| applies | B uses X with specification S (block length, B, interval type …) | X |

Each record keeps its passage in B's own words, and the citation in that passage (`[44]`,
"Goodman-Bacon (2021)"), resolved through B's reference list to a citekey. A target that does
not resolve is a lead: the search fetches it.

Per **procedure** (not per paper): its status (published, working paper, talk) and its date,
and any software the paper itself names ("we provide the R package …"). Whether software exists
and covers the project's case is not checked here: it is a standard question of the design
session (§7).

A paper reporting someone else's method ("following Künsch (1989), we use a moving block") is
not rejected, as a borrowed idea is in the literature review. It is an *applies* record for B,
and its target is the lead to the original.

### 4. Verification

- **B says it.** The two verifiers from other model families, each judging the record against
  the passage the other located (as in the claim grounding).
- **The target is right.** Code: the citation in the passage resolves to that reference.

Nothing judges whether B is right. The review records the argument; it does not referee it.

### 5. Canaries

As in the literature review, sized to the run, holding the release when a kind slips. New kinds
for exchanges: the target swapped for another paper's procedure, a critique turned into support,
another family's critique, a condition changed (staggered → simultaneous adoption).

### 6. Gate

One page, sampled, built for a large corpus:

- **The lineage map** on top, per family: procedures, the critiques against each with their
  conditions, the answers, what is open, and the frontier marked (working papers,
  unimplemented procedures).
- **A sample of exchanges** beside their passages: does B really criticise X on ground G, and is
  the target right? Judgments pinned, as in the literature review.
- **The slip rates** from the canaries.
- **What is missing**, per family: a critique, an answer, or a newer procedure a reader of the
  field would expect. A search goes out for it, as in the literature review.

A pass releases the lineage: `{YYMMDD}_{short}_methodsreview.lineage.json`.

### 7. The choice: the `ramus design` session

The session reads the lineage with the person. Per family: which critiques apply to this
project's data, which answer is used, what it still leaves open, and how that is handled. **Does
the software exist** is a standard question for every procedure in contention: is it implemented
(R, Stata or Python package, or the authors' code), and does it cover this project's case
(continuous-treatment DiD exists as an estimator but not yet in R). The
real options are often three: the best-answered procedure that is implemented (saying which
critiques remain); a newer one implemented by us, which becomes a `raster` task carrying the
paper and the passages that specify it; or the newer one as a robustness check. The prereg
binds it. raconteur writes the Methods section from the lineage and the prereg.

## Bench on DigiPros M4–M6

- M4: resampling dependent / ordered data (iid vs block / moving-block / stationary bootstrap,
  subsampling, block length, short sequences).
- M5: bootstrap intervals and replicate counts (percentile vs BCa vs studentised; Monte Carlo
  error vs B).
- M6: hierarchical resampling and variance components (two-stage bootstrap, nested estimators,
  bias when the within-run variance is itself a bootstrap estimate).

These have real arguments in them: the iid bootstrap's failure under dependence and the block
bootstraps that answered it, the block-length rules and their corrections, the critiques of
BCa and the replies, the choice of B. Steps, each judged before the next:

1. **Search.** The iterated following and the frontier search on M4–M6, out of git in
   `devtools/bench`. The person collects. Judged on: does the lineage a methods reader would
   expect appear in the collect-list?
2. **Exchanges.** Extraction, citation resolution, the two verifiers, canaries, on what was
   collected. Judged on a gate page as above.
3. **Port** what held, into `rabbitHole` and `haarpi`, and only then redo DigiPros' whole
   methods review (M1–M6), replacing the 261103 one.

## Decided 2026-11-13

- Following stops when a round finds no new critique or answer, with a paper budget per family
  as the backstop.
- "Does the software exist" is a standard question of the `ramus design` session, not a check
  the review automates.
- A joint session's output (the scope, the prereg) is not redlined.

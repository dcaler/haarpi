# The methods review: the groundwork for choosing an implementation path

Drafted 2026-11-13 with Cale; reframed 2026-11-15 after the first bench. Nothing here is built
yet. The bench is DigiPros' three bootstrapping families (M4–M6), and nothing is ported until it
is judged.

## What it is for

A choice of method has to be defended at review. The methods review lays the groundwork for that
choice, per family: the options really in contention for **this project's case**, what a
reviewer would raise against each, what answers it, whether software covers the case, and the
handful of papers to cite. The choice itself is made with the person in the `ramus design`
session (§7), and the prereg binds it.

It is **not** a history of the method (Cale, 2026-11-15: the first bench's 50–60 papers per
family were "WAY too much detail. The goal here is not to document the history of a method, it is
to choose an implementation path and defend it"). Methods literatures are adversarial, and that is
what makes the groundwork possible: two-way fixed effects was standard for
difference-in-differences until Goodman-Bacon showed it fails under staggered adoption with
changing effects; Callaway & Sant'Anna, Sun & Abraham and Gardner answered it; a newer working
paper extends the answer to a continuous treatment, not yet in an R package. A row of the table
is that exchange, reduced to what bears on the choice.

It can be small (six bootstrapping questions) or large (an agent-based-modelling validation
literature), so the product stays small: one table per family, about 5–12 papers cited.

## What the old review did, and why it goes

`ramus init` scope → `gather methods` → collect → `build` → `report` (a narrative docx, per
family: The options / Against each / The choice / For the SI) → redline → `ramus design`.
DigiPros (261103): 21 candidates, 2 reached the corpus, and the report still wrote six families
in four parts, from the model's own knowledge, asserting facts about the project it had no basis
for. It goes: `report`, `revise`, `graft`, `refresh`, the methods `mindmap`, the redlined docx and
its `comment` step, and any choice written unattended.

## The product: one decision table per family

The family's case first, in two or three lines from the scope and the project's design docs: what
is resampled or estimated, its size and structure, what the statistic is (e.g. "short, ordered,
non-stationary sequences of factoids; the statistic is a discontinuous nearest-archetype
assignment; CPU, local").

| Option | Fits our case? | The critique a reviewer raises | Answered by | Software | Cite |
|---|---|---|---|---|---|

- **Options:** 2–5 in contention, including the naive one a reviewer would ask about and any
  option that is not a method from the literature (e.g. "order-keeping thinning, as a model of the
  archive").
- **Fits our case?** Against the case statement: which assumptions of the option the case meets.
- **The critique:** the one or two a reviewer would raise *under our conditions*, with its
  condition (stationarity, series length, smoothness of the statistic …).
- **Answered by:** what answers that critique, or "open".
- **Software:** what the papers name (R, Stata, Python package, authors' code). Whether it covers
  this case is asked in the design session, not checked here.
- **Cite:** the papers that carry the row. A cell from a paper not in the corpus is marked as a
  lead, never asserted.
- Under the table: **what stays open whatever is chosen** (e.g. "block-bootstrap theory is for
  long stationary series; a life is neither").
- Under that, one line per family: **what the person must supply** (a number the choice turns
  on, e.g. typical sequence length).

No recommended path. The table is the groundwork; §7 makes the choice.

## The structure

### 1. Scope

The `ramus init` session names the families in `METHODS_SCOPE.md`: per family, where the project
uses it and what must be defended. A joint session's output is not redlined (decided
2026-11-13): the scope goes straight to the search, and the prereg from `ramus design`
straight to release.

### 2. Search: driven by the table

The search fills the table, and stops when it is filled, not when the argument runs out.

- **Seeds** are judged against the family's *need to defend* and case statement, not its name
  (the first bench's M6 seeds were meta-analysis heterogeneity estimators and population-genetics
  AMOVA, under "variance components"). The audit's word-sense check runs on seeds, before any
  following.
- **The options** come from the seeds' proposes / compares records and the scope's list.
- **Following** fetches, per option, the citers that criticise, answer or compare it. An
  *extends* (a variant applied elsewhere) is not followed. The critique's own target, when the
  corpus lacks it, is fetched (the first bench missed Singh 1981, the iid bootstrap's failure
  under dependence, because only references shared by two members went back).
- **The frontier:** per option, the newest answers and comparisons (working papers in:
  `include_preprints` on, labelled), not the newest applications.
- **Stop:** per option, a critique under our conditions and its answer (or "open") are found; a
  paper budget per family is the backstop.
- **audit** before build, and **a collect check**: every paper collected reaches the corpus, or
  the run says which did not and why (DigiPros lost 19 of 21).

### 3. Cards: exchanges, with their targets

The same card store and text reading as the literature review (`ground.py`): a paper is read
once and serves every project. A record is an **exchange**:

| kind | says | target |
|---|---|---|
| proposes | B introduces procedure Y (for problem G) | — or the critique it answers |
| criticises | B shows X fails under condition G (proof, simulation, example) | X, and its paper |
| answers | B's Y fixes G, a failure of X | the critique, and X |
| compares | B compares X and Y under condition H, and finds … | X, Y |
| applies | B uses X with specification S | X |

Each keeps its passage in B's own words, and the citation in it resolved through B's reference
list. An unresolved target is a lead the search fetches. Per procedure: status (published,
working paper) and any software the paper names. A table cell is built from these records, and
links to them.

### 4. Verification

- **B says it.** Two verifiers from other model families, each judging the record against the
  passage the other located (as in claim grounding).
- **The target is right.** Code: the citation in the passage resolves to that reference.
- **The cell follows from its records.** Each cell's wording is checked against the records it
  cites; a cell with no record behind it is marked as a lead.

Nothing judges whether B is right. The review records the argument; it does not referee it.

### 5. Canaries

As in the literature review, sized to the run, holding the release when a kind slips: the target
swapped for another paper's procedure, a critique turned into support, another family's
critique, a condition changed (staggered → simultaneous adoption).

### 6. Gate

One page, per family: **the case and the table on top**, the citing passages one click away, the
canary slip rates, and *what is missing* (an option, a critique, an answer a reader of the field
would expect), which sends a search out, as in the literature review. A pass releases
`{YYMMDD}_{short}_methodsreview.table.json`.

### 7. The choice: the `ramus design` session

The session reads the tables with the person. Per family: which option, given the critiques that
apply; what stays open and how it is handled (e.g. a Monte Carlo coverage check on the generator
instead of an asymptotic argument); what the person supplies. **Does the software exist** is a
standard question for every option in contention: is it implemented, and does it cover this
case? The real choices are often three: the best-answered option that is implemented, saying
which critiques remain; a newer one implemented by us (a `raster` task carrying the paper and
the passages that specify it); or the newer one as a robustness check. The prereg binds it.
raconteur writes the Methods section from the tables and the prereg.

## Bench on DigiPros M4–M6

- M4: within-life factoid resampling (iid vs block / stationary bootstrap, subsampling, block
  length, short sequences).
- M5: bootstrap intervals and replicate counts (percentile vs BCa vs studentised; Monte Carlo
  error vs B).
- M6: runs × resampling (two-stage / hierarchical bootstrap, nested variance components, bias
  when the within-run variance is itself a bootstrap estimate).

1. **Search.** First run (2026-11-14, trundlr 1679–1681): 1,600 judge calls; 58 / 53 / 54
   papers; every family stopped at the 40-paper budget, M4 and M5 in round 1. M4 held the
   lineage (about 10 of 58 papers carry the table); M5 lacked the choice-of-B papers and drifted
   into matching estimators and wild-cluster inference; M6 drifted into meta-analysis. Second run:
   the table-driven search above. Judged on: does the table a methods reader would expect come out
   of the collect-list, at about 5–12 papers per family?
2. **Exchanges and cells.** Extraction, citation resolution, the two verifiers, canaries, the cell
   check, on what was collected. Judged on the gate page.
3. **Port** what held, into `rabbitHole` and `haarpi`, and only then redo DigiPros' whole
   methods review (M1–M6), replacing the 261103 one.

## Decided

- 2026-11-13: following stops when the work is done (now: when the table is filled), with a paper
  budget per family as the backstop.
- 2026-11-13: "does the software exist" is a standard question of the `ramus design` session.
- 2026-11-13: a joint session's output (the scope, the prereg) is not redlined.
- 2026-11-15: the product is a decision table per family, not a lineage; it proposes no path —
  the choice is made in `ramus design`.

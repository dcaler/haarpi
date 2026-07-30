# A shared redline engine, with per-tool policy

Design spec, 2026-07. Written after tracing rabbitHole's `revise.py` and raconteur's
`redline_revise.py` side by side and finding them ~90% the same loop with a handful of
genuinely-different substitutions. This is the target to build against — **not yet
implemented**. Read it alongside the two source files it unifies:

- `packages/rabbithole/rabbithole/revise.py` — `_redline_para_adversary`, `_redline_revise`,
  `_route_corpus_followups`; the `corpus:{sources,table,section}` classes; the
  `cosmetic/gap_fill/redirection` tier planner in `rabbithole/plan.py`.
- `packages/raconteur/raconteur/redline_revise.py` — `redline_paragraph`, `redline_revise`;
  the `route:{section,sources,evidence,figure}` classes; authored-atom protection, obsolete
  anchors, copyedits, the section-kind cite gate.
- `packages/haarpi/haarpi/redline.py` — the shared OOXML mechanics (`comment_threads`,
  `open_asks`, `tracked_replace`, `add_replies`, `add_anchored_comments`, `gate_check`).

The two implementations are **parallel but independently coded**: same three primals, same
control loop, overlapping route classes — duplicated, not shared. raconteur's is the more
evolved of the two. So the engine is **raconteur's shape**, with rabbitHole's tier-routing
folded in as an optional policy hook — not a lowest-common-denominator merge.

Since all haarpi agents are now in-tree (none ship separately), a rabbitHole→haarpi
dependency is a non-issue, and rabbitHole's standalone `redline.py` collapses into the shared
engine.

---

## The polestar, unchanged

**Guards in Python, judgement in the LLM.** Python decides *that* an edit is broken —
precisely, mechanically — and states it as an imperative. The LLM decides only the one thing
code cannot: *does this edit mean what the comment asked for?* The engine exists to run that
discipline once, for every redlining agent, so a new deliverable inherits it instead of
re-deriving it.

**Fail closed.** Malformed JSON, a dropped citekey, a dropped or invented equation, an
out-of-scope sentence, an exhausted retry budget — any of these and the engine writes **no**
tracked change and says so in the reply. A broken edit under a reply claiming "done" is the
worst outcome, worse than a visibly skipped comment.

---

## The pipeline (per anchored comment)

```
open_asks ──► build ParaContext ──► ① TRIAGE ──► ② ATTEMPT ──► ③ AUDIT ──► ④ CLASSIFY ──► reply
                                        │             │            │            │
                                   read before     sentence-    "does it     one primal
                                   responding      indexed edit  mean it?"
```

Stages ②③④ already exist in both tools (they are `redline_paragraph`). The genuinely new
stage is **① TRIAGE** — read and classify the comment *before* attempting the doomed rewrite.
It is where a comment naming a source that isn't yet citeable is caught and pulled in, rather
than being ground through the retry budget and falling out as a misleading `skipped`.

### ① Triage (new)
Before any rewrite attempt:
1. **anchor lies wholly in deleted text** → `OBSOLETE` (currently raconteur-only; lift to the
   engine).
2. **anchor is a heading** → `ROUTED("section")`.
3. **the comment names `[@key]` not in the citeable set** → `policy.resolve_named_source(key)`.
   If it returns an evidence line, enrich this paragraph's evidence and proceed to ②. If it
   returns `None`, `ROUTED("sources")`.
4. otherwise → proceed to ②.

### ②–④ Attempt / audit / classify (lifted, kept once)
The shared per-paragraph loop: number sentences → revise-as-JSON (changed sentences only) →
parse strictly → run the **core deterministic guards** → ask the audit *does the edit satisfy
the comment* → retry on findings → fail closed when rounds exhaust.

---

## The primals

```python
class Disposition(str, Enum):
    EDITED   = "edited"     # tracked prose rewrite passed guards + audit
    ROUTED   = "routed"     # answerable, but not by a prose edit — carries a .cls
    SKIPPED  = "skipped"    # fail-closed: no verifiable edit; paragraph left as-is
    OBSOLETE = "obsolete"   # anchor lies in deleted text — nothing to revise
```

`ROUTED` carries a **class** whose vocabulary is **policy-supplied**. That is the whole trick:
the primal is shared; the class list varies on purpose, declared in one place per tool.

---

## The core guard set — policy-contributed, not engine-owned

The design first assumed the core guards were liftable into the engine verbatim. Building it
proved otherwise: the six core guards are **logically identical across the two tools but
textually divergent** — the algorithm is the same, but tool vocabulary is baked into every
one. `dropped_citekeys`, `author_year_prose`, `dropped_sentinels`, `invented_sentinels`,
`minimal_edit_violation`, `unresolved_keys` each carry a `where` label (`"narrative"` vs
`"manuscript"`) and an imperative that says "corpus"/"evidence list" for rabbitHole and
"refs.bib"/"bibliography" for raconteur. They cannot be lifted without parameterising that
vocabulary — a unification that is its own increment.

So in the shipped v1 the engine owns the loop, the primals, the triage, and the fail-closed
contract; **guards reach the loop through one policy method, `guard_findings(...)`**, which
returns any object exposing `.imperative`. Each tool's `guard_findings` runs the core set
(in its own vocabulary) plus anything tool-specific:

- **core (both):** `dropped_citekeys`, `author_year_prose`, `dropped/invented_sentinels`,
  `minimal_edit_violation`, `unresolved_keys` — a rewrite may not silently lose a source,
  citations must be `[@key]`, equations reproduced exactly, a comment on sentence 2 may not
  rewrite sentence 4 (true by construction), cite only from the citeable set.
- **raconteur-only:** authored-atom protection, `style_findings(signature)`, section-kind
  cite gate.

Folding the vocabulary into a parameterised shared `haarpi.guards` (so the guard *functions*
live once) is a later step; it is not required for the engine to be correct.

---

## The policy interface

```python
@dataclass
class ParaContext:
    heading: str
    text: str
    comments: list[str]          # thread-assembled asks (followups + repeat signal)
    anchored: set[int]           # sentence indices the comment bears on
    cited_keys: set[str]         # [@keys] already in the paragraph
    named_keys: set[str]         # [@keys] the COMMENTS name — drives triage
    kind: str = "prose"          # "methods" | "results" | "prose" | ...
    authored: dict[str, str] = field(default_factory=dict)   # ⟦a:N⟧ → exact words

class RedlinePolicy(Protocol):
    author: str                          # "rabbitHole" | "raconteur"
    route_classes: tuple[str, ...]       # this deliverable's ROUTE vocabulary

    # ── evidence: the ONLY citeable set, and the prose context ──
    def evidence_for(self, ctx: ParaContext) -> Evidence: ...

    # ── the triage hook: a named @key that isn't yet citeable ──
    def resolve_named_source(self, citekey: str) -> EvidenceLine | None: ...

    # ── prompts / house style (skeleton is the engine's; text is the policy's) ──
    def revise_system(self) -> str: ...
    def audit_system(self) -> str: ...
    def revise_user(self, ctx, evidence, numbered_sentences, comment_block) -> str: ...
    def audit_user(self, ctx, revised, comment_block) -> str: ...

    # ── guards (the whole set for this deliverable; each finding exposes .imperative) ──
    def guard_findings(self, old, new, touched, ctx, evidence) -> list: ...
```

This is the shipped v1 signature (see `haarpi/redline_engine.py`). `revise_user`/`audit_user`
let the policy wrap the engine's shared parts (numbered sentences, comment block) in its house
style and drop in the evidence context / authored-span legend. Two seams named in the original
sketch — `route_advice(cls)` and `aggregate(dispositions)` — are **orchestration-level**, not
per-paragraph, so they live on the policy but are consumed by the outer `run_redline` loop
(Steps 2–5), not by `redline_paragraph`. `out_path` likewise belongs to the orchestrator.

---

## rabbitHole policy

| Method | Fill |
|---|---|
| `route_classes` | `("sources", "table", "section")` |
| `evidence_for` | `_para_digest(corpus, notes, cited)` — the Zotero-backed corpus, refreshed via `corpus.refresh_append` |
| **`resolve_named_source`** | **the new competency**: `ZoteroClient.library_items()` → match `@key` via `_extract_citekey` → download PDF/fulltext → `read_notes` annotate → `add_item_to_collection` + `persist` → return its evidence line. `None` only if the key resolves nowhere in the library. |
| `revise_system` | litreview house style (organise around ideas; cite from EVIDENCE) |
| `audit_route_menu` | `sources / table / section` |
| `extra_guards` | none beyond core (no authored atoms today) |
| `expects_citations` | always `True` — every litreview body paragraph must cite |
| `route_advice` | `sources` → "queued a gather→collect→revise cycle"; `table`/`section` → existing text |
| `aggregate` | the tier planner — `plan._make_plan` → `cosmetic/gap_fill/redirection` → `_route_corpus_followups` queues the chain. **All intra-stage** (see boundary law). |
| `out_path` | `naming.minor` chain-extend |

---

## raconteur policy

| Method | Fill |
|---|---|
| `route_classes` | `("section", "sources", "evidence", "figure")` |
| `evidence_for` | `refs.bib` slice + `_context_for_section(heading, litrev, code, results)` — no corpus |
| `resolve_named_source` | `None` for now (raconteur owns no gatherer) ⇒ a named-but-absent `@key` becomes `ROUTED("sources")`, advice "run rabbitHole to gather". **Later**: delegate to rabbitHole's resolver and the pull works here too — the point of the shared hook. |
| `revise_system` | paper house style, incl. the `⟦a:N⟧` authored-span contract |
| `audit_route_menu` | `section / sources / evidence / figure` |
| `extra_guards` | authored-atom protection, `style_findings(signature)`, section-kind cite gate |
| `expects_citations` | `guards.expects_citations(ctx.kind)` — `False` for Methods/Results (grounded in the writeup, not the bib) |
| `route_advice` | `_ROUTE_ADVICE`: section→outline, sources→rabbitHole, evidence→rayleigh/raster, figure→rayleigh |
| `aggregate` | one intra-stage tier (`restructure` → `outline → paper`); everything else is a **cross-stage signal** to the planner |
| `out_path` | `naming.minor_name` chain-extend |
| *(capabilities)* | `authored`/`copyedits` are engine features raconteur turns **on**; rabbitHole leaves them off |

---

## The `aggregate()` boundary law

> **Aggregate routing that stays inside the stage → the policy's `aggregate()`.
> Aggregate routing that crosses a stage boundary → a typed signal to haarpi's planner.**

A redline engine must never reach across a stage boundary on its own — cross-stage re-firing
is the planner's sole prerogative (`queue_chain`, `STAGE_REFRESH`, `_refresh_stale`, gate-driven
`_advance`). So `aggregate()` may only queue chains within its own stage; anything heavier is
emitted as a `FollowUp` signal the planner consumes.

The two tools exercise **opposite halves** of the contract, and neither needs the other's:

| | intra-stage `aggregate()` chain | cross-stage signal to planner |
|---|---|---|
| **rabbitHole** (ladder floor, `inputs: []`) | its whole story (gather tiers) | never emits one |
| **raconteur** (ladder top) | one tier (`restructure` → outline) | its common case (`sources`/`evidence`/`figure`) |

rabbitHole cannot trip a cross-stage need: litreview is the bottom of the ladder, nothing sits
upstream to re-fire, and its *downstream* staleness (build/experiments/paper) is already the
planner's job on release-minting — not the aggregate's. raconteur, depending on litreview /
build / experiments, is where the cross-stage signal path actually gets used. So the boundary
law is not a constraint either tool strains against; it is each one's natural shape.

---

## What moves, what stays

- **New:** `haarpi/redline_engine.py` — the loop, the four primals, the triage stage, the core
  guards, the honest-reply/report machinery, the `RedlinePolicy` protocol.
- **rabbitHole:** `revise.py`'s per-paragraph loop and `redline.py`'s mechanics collapse into
  the engine; what remains is a ~100-line `RabbitHolePolicy` (corpus evidence, the Zotero
  resolver, the tier planner).
- **raconteur:** `redline_revise.py`'s loop collapses into the engine; what remains is a
  `RaconteurPolicy` (refs.bib evidence, authored atoms, style signature, section-kind gate,
  the restructure tier + cross-stage signals).
- **Shared and untouched:** `haarpi/redline.py`'s OOXML primitives, which the engine calls.

## Build order

1. **✅ DONE** — `haarpi/redline_engine.py`: the primals, `ParaContext`, `Evidence`,
   `RedlinePolicy`, the TRIAGE stage, and the ②–④ fail-closed loop (lifted from raconteur,
   the more complete of the two). `tests/test_redline_engine.py` — 21 tests, green — pins the
   loop, triage, and fail-closed contract through a fake policy. **Not yet wired into either
   live tool**, so nothing in the running pipeline changed. Guards come through the policy
   (see the note above), so the engine is correct without a guards migration first.
2. **NEXT** — Write `RaconteurPolicy` (evidence from refs.bib + section context, authored
   atoms, style signature, section-kind gate, the paper prompts); make `raconteur paper` call
   the engine through it. Green raconteur tests = behaviour preserved.
3. Write `RabbitHolePolicy` (corpus evidence, the tier planner); make `rabbithole revise`
   call the engine. Retire `rabbithole/redline.py`.
4. Fill rabbitHole's `resolve_named_source` with the whole-library Zotero pull. The TRIAGE
   stage that consumes it already exists and is tested — this is the feature that started it
   all: *name a curated paper in a comment and it lands, no second round.*
5. Formalise the cross-stage `FollowUp` signal and wire raconteur's `sources/evidence/figure`
   routes into the planner (the `aggregate()` boundary law).
6. *(later, optional)* Parameterise the core guard vocabulary into a shared `haarpi.guards`
   so the guard functions live once, not twice.

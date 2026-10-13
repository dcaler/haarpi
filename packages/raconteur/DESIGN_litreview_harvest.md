# Two-tier litreview harvest

## The problem it replaces

raconteur consumed the literature review through one door — `_analyze_structure` — and that
door read a **blind 12,000-character prefix** of the review's prose (`context._MAX_LITREV_CHARS`),
then distilled it, unseen, into a structural-analysis JSON. Two failures followed:

- **Truncation.** A 30-source review runs ~40k chars of narrative. The back half — often the
  most specific threads — was cut off and never reached the paper.
- **No record.** Whatever the analysis drew from the review lived only in a prompt and was
  thrown away. The review exists to feed the paper *in a way a human can verify*, and nothing
  was verifiable.

## The two tiers

rabbitHole already writes the review as thesis-headed `## ` threads (one idea per heading) with
an annotated-bibliography tail. That is a harvestable index; raconteur need not swallow a prefix.

- **Tier 1 — the index** (`context.load_litreview_threads` → `context.litreview_index`). Every
  `## ` thread becomes a `LitThread(heading, thesis, citekeys, body)`; the bibliography and the
  empty "Narrative Review" wrapper are excluded. The index is one compact line per thread —
  heading, one-sentence thesis, citekeys — so the analysis sees the **whole review's shape,
  untruncated**, in a few hundred characters instead of a truncated prefix of the prose.

- **Tier 2 — the harvest** (`harvest.py`). Folded into the analysis call (no extra model call):
  the prompt asks, using the index, which threads support each `background_pillar` and which are
  left unused, returned as `litrev_harvest`. `_coverage` normalises it so **every thread lands
  exactly once** — bound to a pillar or in `unused` — dropping any heading the model invented.
  This mirrors rabbitHole's own contract: nothing is silently dropped.

## The sidecar

`harvest.write_sidecar` writes `{paper_dir}/{tag}.harvest.md` beside each deliverable: tier-1
index, tier-2 harvest by pillar, the unused threads, and the analysis JSON. It is an
**inspectable verification aid, not a gate** — the human still gates the deliverable, but can
open one file to check the right threads fed the paper and none were dropped. Regenerated every
run; best-effort (a write failure never blocks a draft).

## Where it applies

`_analyze_structure` is the single chokepoint, so the two tiers reach **every** raconteur
output that plans from the review: one-pager, outline (fresh + rebuild), paper draft, paper
revise, skeleton, focus. Each passes its own `sidecar=(paper_dir, tag)`.

## Fallback

`load_litreview_threads` returns `[]` for a review with no `## ` threads (an older flat review).
With no threads, `_analyze_structure` reproduces the original behaviour exactly — the truncated
prefix, no `litrev_harvest`, no sidecar. The new path is additive and never the only path.

## A fact base

rabbitHole can render the review as a **fact base** (`report_format: facts`): facts per source,
each with its page and a verbatim quote, scored 0–3 against approved topics and grouped into
themes. Its document opens with the reviewer's release guide, so the prefix fallback would be
worse than useless; raconteur reads the structured sidecar instead (`factbase.py`), from the
copy `haarpi next` freezes beside the release (`<release>.facts.json`) — the live
`work/facts.json` changes with every later report and would shift under a paper being drafted.

- **Digest** in place of the review text: topics (with the questions they serve and any scope),
  coverage verdicts, load-bearing sources — rabbitHole writes the last two into the sidecar, so
  raconteur does not re-derive its rules.
- **Threads** = themes with at least one fact that counts (verbatim, scoring ≥2 on a topic that
  serves a question). Tier-1 and tier-2 above run unchanged on them.
- **Per-section facts — the step this design left undone.** Background, Introduction, Discussion
  and Methods sections each get "Facts you may use": up to 40 facts (6k chars), drawn from the
  themes the harvest bound to the paper's pillars, ranked by embedding similarity to the
  section's outline beats (word overlap without an embedder), central facts first. Each line
  carries `[@citekey]`, its page and its id `{ck#n}`; an id copied into prose is stripped.
- **Trace.** After the manuscript is assembled, every sentence citing a source is matched to the
  supplied fact of that source it most resembles (word overlap, deterministic) and written to
  `{paper_dir}/paper.facttrace.md` with the fact's page link — or "no matching fact". It is how a
  reader checks any claim at its page.
- **Number fidelity.** A sentence traced to a fact that reports a number its fact does not is a
  guard finding (`fact-number`), fed back through the section's guard repair.

**Disputes.** A reviewer comment such as "this isn't what the source says" on a traced sentence
is decided by `haarpi next` (see `haarpi/DESIGN_redline_engine.md`, build order item 5): if the
sentence misstates its fact, the revise is handed the fact and its quote; if it says what the
fact says, the litreview re-reads the source, the reviewer re-releases it, and the revise is
handed the source's facts as now released. Either way the instruction rides on that comment's
own text into the redline (`paper.dispute_notes`).

**The review guide.** Every manuscript carries a comment on its title, from raconteur, that
opens "HOW TO REVIEW THIS DRAFT": what releasing means, raconteur's checklist (the argument,
claims against `paper.facttrace.md`, numbers, figures), and how to ask for each kind of rework,
`dispute` included — generated from `haarpi.comment_kinds.PAPER_MANUSCRIPT`, whose phrasings a
test holds to the gate's own prompt and dispute floor. A comment, not a section, because the
body is what gets submitted; unresolved, it blocks the release, so resolving it is the
acknowledgement. Neither the gate's classifier nor the revise reads it as an ask.

## Not done here

- Per-section **prose** harvest for a NARRATIVE review (a fact base has it — above). Today
  tier-2 records which threads feed each pillar; it does not yet splice a thread's full `body`
  into the individual section draft (those drafts read the
  analysis JSON + `refs.bib`, not the review prose). The `LitThread.body` field is carried for
  exactly that next step.
- `_MAX_LITREV_CHARS` still governs the flat-review fallback; the threaded path ignores it.

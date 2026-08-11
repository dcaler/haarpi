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

## Not done here

- Per-section **prose** harvest. Today tier-2 records which threads feed each pillar; it does
  not yet splice a thread's full `body` into the individual section draft (those drafts read the
  analysis JSON + `refs.bib`, not the review prose). The `LitThread.body` field is carried for
  exactly that next step.
- `_MAX_LITREV_CHARS` still governs the flat-review fallback; the threaded path ignores it.

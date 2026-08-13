# rabbitHole `revise` — honor the reviewer's comment, don't decline

## The problem (the four "honest declines")

On the pydsk run, four of the reviewer's explicit "explain X" comments came back as:

> could not produce a revision that addressed this without dropping a citation or an
> equation from the paragraph, so the paragraph is unchanged. Narrow the comment, or revise
> this one by hand.

That reply is *honest* but it is also a **refusal**. The reviewer asked for something; the
tool did nothing. The desired behavior: **when the human asks, do it** — and if the only
revision that answers the comment cannot keep every citation/equation, land it anyway and
**flag exactly what it sacrificed**, so the trade-off is visible instead of the comment being
dropped.

## Why it declined (mechanism)

`revise` drives the shared `haarpi.redline_engine`. Each round the brain proposes a
sentence-keyed rewrite; deterministic guards run; **any** finding sends the attempt back for
another round; if `rounds` are exhausted with a finding still standing, the engine **fails
closed** (`SKIPPED`) and the paragraph is left as the reviewer wrote it. For a citation-dense
paragraph, no rewrite that answers the comment could also keep every `[@key]` — so every
round tripped `dropped-citekey` (or `dropped-equation`), and the loop exhausted into a skip.

Fail-closed is the **right default** — it is what protects raconteur's paper contract, where a
silently dropped citation is unacceptable and no edit may land without a human. This change
does **not** touch that default. It adds an **opt-in** relaxation that only rabbitHole enables.

## The design — a policy-gated *soft override*

Two finding kinds are *soft-protected* for rabbitHole: `dropped-citekey` and
`dropped-equation`. Everything else stays a hard blocker (an **invented** equation is
fabrication; an author-year lapse or over-reach is a format fault; a paragraph emptied of all
citations — `uncited` — is degenerate). "Soft" never means "ignored" — it means **held as a
fallback**, not vetoed.

Engine loop, per paragraph:

1. As today, a clean rewrite (no findings, audit OK) returns `EDITED`. Preserving every
   citation is always preferred, and the loop still spends all its rounds trying for it.
2. When a round's findings are **all** soft-protected, the engine keeps that candidate as
   `best_soft` (latest wins — it has absorbed the most critique) and *still* re-rounds,
   because a clean rewrite would beat it.
3. If the rounds exhaust with **no** clean rewrite but a `best_soft` in hand, the engine runs
   the audit **once** on `best_soft`. The audit is the meaning check — *does this edit answer
   the comment?* If **OK** → return the new disposition `OVERRIDDEN` (the edit lands). If the
   audit **routes** → route it. If the audit is **not OK** → fail closed as before. A dropped
   citation on an edit that also misses the point is junk, and is still skipped.

A policy that does not declare `soft_finding_kinds()` (raconteur, every existing `FakePolicy`)
gets an empty set via `getattr`, so `best_soft` is never set and the behavior is **byte-for-byte
the old fail-closed loop**. The paper contract is untouched.

### What the reviewer sees

The overridden edit lands as a tracked change, and its comment reply names the sacrifice,
derived deterministically from old-vs-new text (not from the model):

> rabbitHole: revised the paragraph above as a tracked change to address this comment. Doing
> so dropped the citation [@x] — I could not honor the comment while keeping it, so verify
> this was the intended trade-off.

So the human keeps the accept/reject decision (the redline contract), now with the full
trade-off in front of them — instead of a refusal.

## Where each piece lives

- `haarpi/redline_engine.py` — `Disposition.OVERRIDDEN`; optional `soft_finding_kinds()` on the
  policy Protocol; `best_soft` tracking + the exhaustion audit. Opt-in, default off.
- `rabbithole/revise.py` — `RabbitHolePolicy.soft_finding_kinds()`; the adapter maps
  `OVERRIDDEN` → `"override:<note>"`; `_redline_revise` lands the edit; `_reply_to_comments`
  emits the flag.

## Frozen tests (GPU-free, brain mocked)

Engine (`test_redline_engine.py`):
1. A policy declaring `{dropped-citekey}` + a rewrite that drops one every round + audit OK →
   `OVERRIDDEN`, the edit is returned, the audit ran exactly once.
2. Same, but the exhaustion audit **routes** → `routed:<class>`, no edit.
3. Same, but the exhaustion audit is **not OK** → `SKIPPED`, no edit.
4. A policy with **no** `soft_finding_kinds` (the existing exhaustion test) still `SKIPPED` —
   the raconteur-safe default.

rabbitHole (`test_revise_adversary.py`):
5. The old "exhausted → skipped" citekey case now returns an `override`, the last attempt is
   the text, the audit ran once.
6. The `override` outcome carries a note that **names the dropped `[@key]`**.
7. An **invented-equation** every round (a hard finding) still `skipped` — the override is only
   for soft sacrifices, never for fabrication.

## Not in scope (a later increment)

*Additive answers.* Many "explain X" comments are best answered by **inserting** a clarifying
sentence beside the anchored one — which preserves every citation and needs no override at all.
That requires extending the sentence-edit contract to express an insertion (a new key form,
policy-gated so raconteur is unaffected) and is a separate, larger change. The override above
is the safety net; insertion would reduce how often it fires. Deferred until the override is
proven on a live run.

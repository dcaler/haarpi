# Shared style engine — design

Sister document to `DESIGN_redline_engine.md`. Same disease, same cure: one job implemented
twice, drifting, with a fix landed in only one copy.

## The problem this closes

`rabbithole/style.py` (273 ln) and `raconteur/style.py` (335 ln) are independent
re-implementations of the same subsystem — train a voice profile from the author's own
publications, decide when to retrain, write it, read it back during synthesis. They:

1. **Duplicate the whole surface**: `_item_label`, `_extract_prose`, `_load_existing_meta`,
   `_write_profile`, `fetch_and_train`, `run`, the up-to-date guard — two copies of each.
2. **Collide on one file.** Both read/write `~/.config/raconteur/style_profile.md`. Whichever
   tool trained last owns the file. The profile on disk right now was written by raconteur
   (it carries a `signature:` block and no `papers_skipped:` — rabbitHole writes the inverse).
3. **Carry a fix in only one copy.** Commit `3b9bb63` (2026-07-08) stopped the
   train-on-every-run loop in rabbitHole by recording *attempted* keys, not *used* keys, in
   `paper_keys`. raconteur's copy never got it — it still `paper_keys.append(key)` for used-only
   papers. So a raconteur run rewrites the shared `paper_keys` back to the short list and
   **re-arms rabbitHole's loop**, even though rabbitHole's own code is fixed.
4. **Leave a second bug uncovered.** The 07-08 fix records keys that *came back from Zotero*.
   Keys that resolve to **no Zotero item at all** (deleted, wrong key, wrong library) never
   enter `confirmed_items`, so they never enter `paper_keys`, so the subset check stays
   unsatisfiable forever. This is the pydsk case: 21 configured keys, 9 resolve, 12 vanish,
   `needs_training` is `True` on every single run.

## The shape of the cure

The two profiles are **not peers — raconteur's is a superset of rabbitHole's**:

| | frontmatter | body | consumed by |
|---|---|---|---|
| rabbitHole profile | author, keys, used | analysis prose | dumped as a string into the synthesis prompt |
| raconteur profile | + `signature` (measured palette) | `## Voice — exemplars` + `## Voice — analysis` | `voice.style_block()` renders a rich, budgeted block |

rabbitHole can consume raconteur's profile perfectly (it only reads the body string).
raconteur *cannot* consume rabbitHole's (no signature → `style_block` degrades — the reason
raconteur grew its `profile_is_current` guard). So there is one canonical profile: **the
measured one.** One trainer produces it; both tools read it.

## Architecture (mirrors the redline engine)

Engine owns the invariant; policy injects the tool-specific pieces. "Guards in Python,
judgement — and I/O — through the policy."

### `haarpi/voice.py` (moved from raconteur, verbatim)

The 496-line measurement library — `pdf_prose`, `signature`, `pick_exemplars`, `style_block`,
`_tidy`, `clean_prose`. It already imports only `haarpi.redline`, `haarpi.text`, stdlib, and
`docx`; the single `from .log import log` is the only edit (→ a passed-in / haarpi logger).
raconteur re-exports it under `raconteur.voice` (the `rabbithole/redline.py` re-export
precedent), so every existing `voice.*` consumer keeps working untouched.

### `haarpi/style.py` (new — the engine)

Owns, as invariants:

- **Location + migration.** Canonical path `~/.config/haarpi/style_profile.md` (neutral —
  the PII boundary the config module already defines). Reads fall back to the legacy
  `~/.config/raconteur/style_profile.md` when the canonical file is absent; writes only ever
  go to the canonical path. No destructive move — same philosophy as `config.legacy_path`.
- **Profile format** = the measured one (signature + exemplars + analysis). One writer.
- **`load_meta()` / `load_signature()` / `load_block(kind, budget, render)`** — consumption.
- **`needs_training(confirmed_keys, require_format=True)`** — the fixed subset check, now with
  BOTH bugs closed: `paper_keys` records every *requested* key (attempted ∪ requested-but-
  unresolved), so a key Zotero can't find counts as trained-against instead of re-triggering
  forever. Plus the format-current check folded in from raconteur.
- **`train(policy, confirmed_items, requested_keys)`** — the invariant loop: per item →
  `policy.prose_for(item)`; record attempted/used/skipped; `voice.signature` + exemplars;
  `policy.analyze(...)`; write. Records `requested_keys − returned` as trained-against.
- **`run(policy, directory)`** — orchestration (config load, key resolution, up-to-date guard,
  fetch, train, save).

### `StylePolicy` protocol (per tool)

| member | rabbitHole | raconteur |
|---|---|---|
| `style_author` / `style_paper_keys` | from `litrev.yaml` | from `paper/raconteur.yaml` |
| `zotero()` | `rabbithole.zotero.ZoteroClient` | `raconteur.zotero.ZoteroClient` |
| `prose_for(item)` | PDF-by-layout (was Zotero-fulltext + `_extract_prose`; upgraded to the measured path) | PDF-by-layout (`voice.pdf_prose`) |
| `analyze(author, exemplars, brain_cfg)` | coordinator prose analysis | same (best-effort — signature+exemplars carry the voice) |
| `log(msg)` | `runlog.stamp`-prefixed print | `raconteur.log.log` |

Both `prose_for` implementations become the same measured path — the only reason they differed
was rabbitHole never measured. After this, rabbitHole's profile is the measured one too, so its
synthesis gets the signature-backed voice it never had.

## The two decisions (flagged for approval)

1. **Make the measured profile canonical; retire rabbitHole's thin trainer.** rabbitHole stops
   writing an analysis-only profile and both reads and writes the measured format. Upside: the
   collision is gone (one format), rabbitHole gains the measured signature, `profile_is_current`
   becomes moot. This is the whole point of consolidating rather than merely porting the fix.

2. **Move `voice.py` into `haarpi/` and re-export from raconteur.** The measurement code is the
   shared asset; it must live where both tools can reach it. Verbatim move + re-export shim =
   zero change to raconteur's ~15 `voice.*` call sites.

## Neutral territory + never-synced (the two constraints)

- **Neutral:** `~/.config/haarpi/style_profile.md`, not `~/.config/raconteur/`. Already outside
  any git repo.
- **Never synced:** the profile has always lived in `~/.config` (outside the tree), so it is not
  and was never a committed artifact. Belt-and-suspenders regardless — add to root `.gitignore`:
  `style_profile.md`, `style_profile.md.bak`, `*.md.bak`. Tests must write to `tmp_path`, never
  the real path (monkeypatch `STYLE_PROFILE_PATH`).

## Build order (each step ends green)

1. **DONE** — **Move** `voice.py` → `haarpi/voice.py` (only edit: a local `log`); re-export shim
   in `raconteur/voice.py`. raconteur 630, haarpi 219.
2. **DONE** — **Engine**: `haarpi/style.py` with neutral path + legacy read-fallback +
   non-destructive `_migrate_legacy`, `load_*`/`load_block`, `needs_training` (both bugs closed),
   strict `profile_is_current` (writer always emits the tagged section, so a fresh train is never
   mistaken for stale), `train`, `run`, `StylePolicy`. `haarpi/tests/test_style_engine.py` (10).
3. **DONE** — **raconteur policy**: `raconteur/style.py` → thin `RaconteurStylePolicy` +
   delegation; `context._read_profile` routed through the engine; `test_style_note.py` updated to
   the new home. raconteur 630.
4. **DONE** — **rabbitHole policy**: `rabbithole/style.py` → thin `RabbitHoleStylePolicy` +
   delegation; `load_style_profile` now returns the measured block; `test_style_profile.py`
   rewritten as a binding + format-upgrade test. rabbitHole 135.
5. **DONE** — root `.gitignore` gains `style_profile.md` / `*.md.bak`; audited that every test
   patches the engine path (nothing touches the real `~/.config`).

Suites after consolidation: haarpi 229, rabbitHole 135, raconteur 630.

## Boundary law

The engine never imports a tool. Everything tool-specific — which Zotero library, which config
file, how to log, whether a brain is available — arrives through the policy. Same law as the
redline engine.

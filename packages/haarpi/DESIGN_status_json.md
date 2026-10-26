# `.haarpi/status.json` — the milestone contract

HAARPi writes one status file per project; raDash reads it. HAARPi owns the file and this
schema; raDash owns everything about how it is shown. Bump `schema` on any breaking change.

## Where and when

`<project>/.haarpi/status.json`. raDash finds projects by scanning
`Current_Work_Projects/*/.haarpi/status.json`.

Rebuilt after every `haarpi` verb, on every exit path including errors: `next`, `status`,
`init`, `queue`, `authors`, and each `haarpi <tool> …` (so a new draft from `report` shows up
without waiting for the next gate). Written atomically (temp file + rename); a failure to write
warns and never fails the verb. Code: `haarpi/status.py`.

## Principles

- **Derived, not kept.** A milestone is done when its release exists on disk. Nothing is
  entered twice; nothing can be ticked without the work.
- **The full list, always.** Every milestone the project can reach is listed from day one;
  only some are done.
- **Each venue is its own track.** After the shared paper rungs the paper forks per selected
  venue, and each fork (with its deck) is a separate process: SchellingChords → CSS 2026 and
  SchellingChords → Leonardo. A track whose submission is done has outcome `submitted` — a
  success — whatever the other tracks are doing.
- **Implied completion.** A milestone with no document of its own is `done` with
  `implied: true` when something downstream of it is done: earlier rungs of a submitted
  track, the shared rungs, and every stage feeding a done stage. Never overrides a milestone
  with its own document (in flight stays in flight).
- **Targets are the only typed input**, in `haarpi.yaml`:
  ```yaml
  milestones:
    litreview: 2026-11-15
    paper.css2026.submission: 2027-01-10
  ```
  A deck's `decks.<format>.date` is its target unless overridden here.

## Milestone ids

| id | done when |
|---|---|
| `litreview` | the litreview release exists |
| `methodsreview` | the methods review is released (listed only if `methodsReview/methodsreview*.yaml` exists) |
| `design`, `build`, `experiments` | that stage's release exists |
| `paper.onepager`, `paper.venue` | the shared paper rungs (track `null`) |
| `paper.<venue>.outline` / `.draft` / `.submission` | per selected venue (track = venue); submission = packaged PDF/docx |
| `deck.<format>` | the deck for that presentation format is minted (track = its venue, lower-cased) |

## Schema (v1)

```jsonc
{
  "schema": 1,
  "generated": "2026-10-26T18:04:11",
  "generated_by": "haarpi next",            // the verb that wrote it
  "project": {"name": "DigiPros", "short_title": "DigiPros", "trundlr_project_id": 4,
              "priority": 2, "folder": "/…/260208_DigiPros"},
  "trundlr": true,                          // false: trundlr not consulted/unreachable — no projections
  "milestones": [                           // the FULL list: stages, shared paper rungs, venue rungs, decks
    {
      "id": "litreview",
      "track": null,                        // null = shared; else a venue slug
      "state": "in_flight",                 // done | in_flight | open | unlocked | waiting | not_started
      "implied": false,                     // done only because something downstream is
      "waiting_on": "queue",                // you | tool | queue | running | null
      "document": "261013_DigiPros_litreview_ra_DCR.docx",
      "cycle_started": "2026-10-13",        // datestamp of the current document's cycle
      "rounds": 1,                          // reviewer passes in its chain (initials tokens)
      "released": null,                     // or {"file": "…", "date": "YYYY-MM-DD"} (latest release)
      "target": "2026-11-15",               // or null
      "projected": {"date": "2026-10-27", "basis": "estimate"},   // or null
      "health": "on_track",                 // done | on_track | at_risk | late | no_target
      "late_by_days": null,                 // a done milestone released after its target
      "blocked_by": [],                     // inputs not yet released (state waiting)
      "stale_inputs": [],                   // inputs re-released since this stage bound them
      "open_tasks": [{"id": 1326, "title": "rabbithole next 2", "status": "todo",
                      "resource": "runner", "hours": 0.05}]
    }
  ],
  "tracks": [
    {"venue": "css2026", "milestones": ["paper.css2026.outline", "…", "deck.longtalk"],
     "outcome": "submitted"}               // submitted | null
  ]
}
```

### States

- `done` — released (or implied).
- `in_flight` — a working document exists (newer than any release).
- `open` — the stage was opened in the ledger but has no document yet.
- `unlocked` — every input is released; not opened.
- `waiting` — an input is not released (`blocked_by` names it).
- `not_started` — reachable, nothing yet (paper rungs whose inputs are all in).

### `waiting_on`

From the milestone's open trundlr tasks when there are any: `running` if one is in progress,
else `you` when the earliest is a human task, `queue` when it is a runner task. With no open
tasks it falls back to the document: a tool draft (`…_ra`) waits on `you`; a reviewer's markup
(`…_DCR`) waits on the `tool` (a `haarpi next` that is not queued). A deck in flight waits on `you`
(it is reviewed in place).

### Projection and health

`projected` = now + the summed estimated hours of the milestone's open tasks — no queue
contention, so it is an `estimate`, and `null` without trundlr. `health`: `done`; else
`no_target`; `late` if today is past the target; `at_risk` if the projection is past it;
`on_track` otherwise.

### Task → milestone

Open tasks are assigned by their board title (`planner._parse_title`): a litreview step to
`litreview` (`methods` in the venue slot → `methodsreview`); a paper step with a venue to that
track's first unfinished rung, without one to the first unfinished shared rung; a deck step to
the deck of its venue; anything else to its stage. Untitled or unparseable tasks are left out.

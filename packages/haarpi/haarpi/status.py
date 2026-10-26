"""`.haarpi/status.json` — every milestone a project can reach, and where each one stands.

The file is a CONTRACT with raDash (schema: DESIGN_status_json.md); raDash only reads it. It is
derived, never kept: a milestone is done when its release exists on disk, so nothing is entered
twice and nothing can be ticked without the work. Rebuilt after every `haarpi` verb — `next` on
every exit, `status`, `init`, `queue`, and each `haarpi <tool> …` — by :func:`write_status`, which
never fails the verb that called it.

Milestones are the stage releases, the paper's rungs and the decks. The paper forks per selected
VENUE after its shared rungs, and each venue is its own TRACK: SchellingChords → CSS 2026 and
SchellingChords → Leonardo are separate processes, and a track whose submission is done has the
outcome "submitted" whatever another track is doing. A milestone with no file of its own whose
downstream is done counts as done, `implied` — SchellingChords' rungs before its CSS 2026
submission predate the current layout, but the submission proves they happened.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from . import naming, project

SCHEMA = 1
STATUS_FILE = "status.json"
PAPER_SHARED = ("onepager", "venue")
VENUE_RUNGS = ("outline", "draft", "submission")


def status_path(root: Path) -> Path:
    return root / project.STATE_DIR / STATUS_FILE


# ── one milestone ────────────────────────────────────────────────────────────────
def _row(mid: str, track: str | None = None, **kw) -> dict:
    row = {"id": mid, "track": track, "state": "not_started", "implied": False,
           "waiting_on": None, "document": None, "cycle_started": None, "rounds": 0,
           "released": None, "target": None, "projected": None, "health": "no_target",
           "late_by_days": None, "blocked_by": [], "stale_inputs": [], "open_tasks": []}
    row.update(kw)
    return row


def _iso(stamp: str | None) -> str | None:
    """A chain datestamp (YYMMDD) as an ISO date."""
    if not stamp or not re.fullmatch(r"\d{6}", stamp):
        return None
    return f"20{stamp[:2]}-{stamp[2:4]}-{stamp[4:]}"


def _rounds(chain: list[str]) -> int:
    """Reviewer passes in a chain: the initials tokens (upper-case, letters only)."""
    return sum(1 for t in chain if t.isalpha() and t.isupper() and t.lower() != "ra")


def _from_files(row: dict, m, release: Path | None, flight: Path | None) -> dict:
    """Fill a row from its release and in-flight files (either may be None)."""
    if release is not None:
        parsed = naming.parse(release, m.short_title)
        row["released"] = {"file": release.name, "date": _iso(parsed[0] if parsed else None)}
    if flight is not None and (release is None
                               or flight.stat().st_mtime > release.stat().st_mtime):
        parsed = naming.parse(flight, m.short_title) or ("", ["ra"], "")
        row.update(state="in_flight", document=flight.name, cycle_started=_iso(parsed[0]),
                   rounds=_rounds(parsed[1]),
                   waiting_on="you" if parsed[1][-1].lower() == "ra" else "tool")
    elif release is not None:
        row.update(state="done", document=release.name)
    return row


# ── the list ─────────────────────────────────────────────────────────────────────
def _stage_rows(root: Path, m) -> list[dict]:
    opened = {e.get("stage") for e in project.list_plans(root) if e.get("type") == "opened"}
    rows = []
    for stage, spec in m.stages.items():
        if stage in ("paper", "deck"):
            continue
        row = _row(stage)
        _from_files(row, m, project.latest_release(root, m, stage),
                    project.in_flight(root, m, stage))
        if row["state"] == "not_started":
            missing = [s for s in spec.get("inputs", [])
                       if project.latest_release(root, m, s) is None]
            row["state"] = ("open" if stage in opened
                            else "waiting" if missing else "unlocked")
            row["blocked_by"] = missing
        row["stale_inputs"] = project.stale_inputs(root, m, stage)
        rows.append(row)
        if stage == "litreview":
            rows += _methods_review(root, m)
    return rows


def _methods_review(root: Path, m) -> list[dict]:
    """The methods review, only where the project asked for one (its config is the opt-in)."""
    d = root / "methodsReview"
    if not any(d.glob("methodsreview*.yaml")):
        return []
    out = d / "output"
    rel = flight = None
    if out.is_dir():
        for ext in ("docx", "md"):
            got = naming.find_latest_release(out, m.short_title, ext=ext,
                                             chain_includes="methodsreview")
            if got and (rel is None or got.stat().st_mtime > rel.stat().st_mtime):
                rel = got
        live = [p for p in list(out.glob("*.docx")) + list(out.glob("*.md"))
                if (pr := naming.parse(p, m.short_title)) and not naming.is_release(pr[1])
                and "methodsreview" in [c.lower() for c in pr[1]]]
        flight = max(live, key=lambda p: p.stat().st_mtime) if live else None
    row = _from_files(_row("methodsreview"), m, rel, flight)
    if row["state"] == "not_started":
        row["state"] = "open"
    return [row]


def _paper_rows(root: Path, m, venues: list[str]) -> list[dict]:
    from . import planner
    missing = [s for s in m.stages.get("paper", {}).get("inputs", [])
               if project.latest_release(root, m, s) is None]
    pending = "waiting" if missing else "not_started"
    rows = []

    def rung(mid, track, includes):
        row = _row(mid, track)
        _from_files(row, m, planner._paper_pick(root, m, includes, want_release=True),
                    planner._paper_pick(root, m, includes, want_release=False))
        if row["state"] == "not_started":
            row.update(state=pending, blocked_by=list(missing))
        return row

    for deliv in PAPER_SHARED:
        rows.append(rung(f"paper.{deliv}", None, [deliv]))
    for v in venues:
        rows.append(rung(f"paper.{v}.outline", v, [v, "outline"]))
        rows.append(rung(f"paper.{v}.draft", v, [v]))
        sub = planner._submission_state(root, m, v)
        row = _row(f"paper.{v}.submission", v)
        if sub.startswith("packaged"):
            name = sub.split(None, 1)[1].strip()
            pdir = m.stage_dir(root, "paper") / "submission" / v
            when = pdir / name
            d = (datetime.fromtimestamp(when.stat().st_mtime).date().isoformat()
                 if when.exists() else None)
            row.update(state="done", document=name, released={"file": name, "date": d})
        elif sub.startswith("assembled"):
            row.update(state="in_flight", waiting_on="you")
        else:
            row.update(state=pending, blocked_by=list(missing))
        rows.append(row)
    return rows


def _deck_rows(root: Path, m, venues: list[str]) -> list[dict]:
    """One deck per presentation format; its track is the venue it is presented at."""
    rows = []
    base = m.stage_dir(root, "deck")
    for fmt, cfg in sorted((m.decks or {}).items()):
        cfg = cfg or {}
        venue = str(cfg.get("venue") or "").strip()
        track = venue.lower() or None
        row = _row(f"deck.{fmt}", track)
        files = []
        if venue and base.is_dir():
            for d in base.iterdir():
                if d.is_dir() and d.name.lower() == venue.lower():
                    files += [p for p in d.glob("*.pptx") if naming.parse(p, m.short_title)]
        rel = [p for p in files if naming.is_release(naming.parse(p, m.short_title)[1])]
        live = [p for p in files if p not in rel]
        _from_files(row, m, max(rel, key=lambda p: p.stat().st_mtime) if rel else None,
                    max(live, key=lambda p: p.stat().st_mtime) if live else None)
        if row["state"] == "in_flight":
            row["waiting_on"] = "you"          # a deck is reviewed in place: it waits on you
        target = _parse_date(cfg.get("date"))
        if target:
            row["target"] = target
        rows.append(row)
    return rows


def _parse_date(value) -> str | None:
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10]
    s = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%d %B %Y", "%d %b %Y", "%B %d, %Y", "%B %d %Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


# ── implied completion ───────────────────────────────────────────────────────────
def _imply(rows: list[dict], m) -> None:
    """A milestone with nothing of its own is done when something it feeds is done: the
    later work proves it happened. Never overrides a milestone that has its own document."""
    by_id = {r["id"]: r for r in rows}

    def mark(mid):
        r = by_id.get(mid)
        if r and r["state"] not in ("done", "in_flight"):
            r.update(state="done", implied=True, blocked_by=[], waiting_on=None)

    def feeds(stage):                       # every stage upstream of `stage`, transitively
        seen, todo = set(), list(m.stages.get(stage, {}).get("inputs", []))
        while todo:
            s = todo.pop()
            if s not in seen:
                seen.add(s)
                todo += m.stages.get(s, {}).get("inputs", [])
        return seen

    for r in list(rows):
        if r["state"] != "done":
            continue
        mid, track = r["id"], r["track"]
        if mid.startswith("paper.") and track:
            rung = mid.rsplit(".", 1)[1]
            for earlier in VENUE_RUNGS[:VENUE_RUNGS.index(rung)]:
                mark(f"paper.{track}.{earlier}")
            for deliv in PAPER_SHARED:
                mark(f"paper.{deliv}")
            if rung == "submission":
                for s in feeds("paper"):
                    mark(s)
        elif mid.startswith("deck."):
            for s in feeds("deck"):
                mark(s)
        elif mid in m.stages:
            for s in feeds(mid):
                mark(s)


# ── trundlr: who it waits on, and when it might land ─────────────────────────────
def _assign_tasks(rows: list[dict], tasks: list[dict], human_ids: set[int]) -> None:
    from . import planner
    by_id = {r["id"]: r for r in rows}
    for t in tasks:
        if t.get("status") in ("done", "cancelled"):
            continue
        parsed = planner._parse_title(t.get("title", ""))
        if not parsed:
            continue
        stage, step, venue, _cycle = parsed
        target = None
        if stage == "litreview" and venue == "methods":
            target = by_id.get("methodsreview")
        elif stage == "paper":
            order = ([f"paper.{venue}.{x}" for x in VENUE_RUNGS] if venue
                     else [f"paper.{x}" for x in PAPER_SHARED])
            target = next((by_id[i] for i in order if i in by_id
                           and by_id[i]["state"] != "done"), None)
        elif stage == "deck":
            decks = [r for r in rows if r["id"].startswith("deck.") and r["state"] != "done"]
            target = next((r for r in decks if venue and venue in (r["track"] or "")),
                          decks[0] if decks else None)
        else:
            target = by_id.get(stage)
        if target is None:
            continue
        res = t.get("resource_ids") or []
        target["open_tasks"].append({
            "id": t.get("id"), "title": t.get("title"), "status": t.get("status"),
            "resource": "human" if set(res) & human_ids else "runner",
            "hours": float(t.get("duration") or 0)})
    for r in rows:
        ts = sorted(r["open_tasks"], key=lambda x: x["id"] or 0)
        r["open_tasks"] = ts
        if not ts:
            continue
        if any(x["status"] == "in_progress" for x in ts):
            r["waiting_on"] = "running"
        else:
            r["waiting_on"] = "you" if ts[0]["resource"] == "human" else "queue"
        hours = sum(x["hours"] for x in ts)
        r["projected"] = {"date": (datetime.now() + timedelta(hours=hours)).date().isoformat(),
                          "basis": "estimate"}


def _health(r: dict, today: date) -> None:
    tgt = r["target"]
    if r["state"] == "done":
        r["health"] = "done"
        rel = (r["released"] or {}).get("date")
        if tgt and rel and rel > tgt:
            r["late_by_days"] = (date.fromisoformat(rel) - date.fromisoformat(tgt)).days
        return
    if not tgt:
        r["health"] = "no_target"
    elif today.isoformat() > tgt:
        r["health"] = "late"
    elif r["projected"] and r["projected"]["date"] > tgt:
        r["health"] = "at_risk"
    else:
        r["health"] = "on_track"


# ── the document ─────────────────────────────────────────────────────────────────
def build(root: Path, m=None, *, tasks: list[dict] | None = None, human_ids=(),
          venues: list[str] | None = None, generated_by: str = "",
          today: date | None = None) -> dict:
    """The whole status document. ``tasks`` are the project's trundlr tasks (None: trundlr was
    not consulted, so nothing is projected); ``venues`` default to the author's selection."""
    from . import planner
    m = m or project.load_manifest(root)
    venues = planner._selected_venues(root) if venues is None else venues
    rows = _stage_rows(root, m) + _paper_rows(root, m, venues)
    decks = _deck_rows(root, m, venues)
    # the deck stage's place in the list: after the paper, per venue
    rows += decks
    _imply(rows, m)
    if tasks is not None:
        _assign_tasks(rows, tasks, set(human_ids))
    targets = getattr(m, "milestones", None) or {}
    for r in rows:
        if r["id"] in targets:
            r["target"] = _parse_date(targets[r["id"]])
        _health(r, today or date.today())
    tracks = []
    for v in list(dict.fromkeys(venues + [r["track"] for r in decks if r["track"]])):
        ids = [r["id"] for r in rows if r["track"] == v]
        sub = next((r for r in rows if r["id"] == f"paper.{v}.submission"), None)
        tracks.append({"venue": v, "milestones": ids,
                       "outcome": "submitted" if sub and sub["state"] == "done" else None})
    return {
        "schema": SCHEMA,
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "generated_by": generated_by,
        "project": {"name": m.name, "short_title": m.short_title,
                    "trundlr_project_id": m.trundlr_project_id,
                    "priority": m.trundlr_priority, "folder": str(root.resolve())},
        "trundlr": tasks is not None,
        "milestones": rows,
        "tracks": tracks,
    }


def _trundlr(m) -> tuple[list[dict] | None, set[int]]:
    """The project's tasks and the human resource ids — or (None, ∅) when trundlr is not
    configured or does not answer (the file is still written, without projections)."""
    if not m.trundlr_project_id:
        return None, set()
    try:
        from . import planner, trundlr
        tr_cfg = planner.pipeline_config().get("trundlr", {})
        if not tr_cfg.get("url"):
            return None, set()
        human = {i for i in (planner._resource_id(tr_cfg, "human"),) if i}
        return trundlr.TrundlrClient(tr_cfg["url"]).tasks_for_project(m.trundlr_project_id), human
    except Exception:                                   # noqa: BLE001
        return None, set()


def write_status(root: Path | None, generated_by: str = "") -> Path | None:
    """Rebuild and atomically write `.haarpi/status.json`. Never raises: a status file that
    cannot be written must not fail the work that triggered it."""
    if root is None or not (root / project.MANIFEST).is_file():
        return None
    try:
        m = project.load_manifest(root)
        tasks, human = _trundlr(m)
        doc = build(root, m, tasks=tasks, human_ids=human, generated_by=generated_by)
        fp = status_path(root)
        fp.parent.mkdir(parents=True, exist_ok=True)
        tmp = fp.with_name(f".{fp.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, fp)
        return fp
    except Exception as e:                              # noqa: BLE001
        print(f"  [status] could not write {project.STATE_DIR}/{STATUS_FILE} ({e})",
              file=sys.stderr)
        return None

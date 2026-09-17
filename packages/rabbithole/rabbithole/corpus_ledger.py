"""The corpus ledger — what role each Zotero item plays in this project.

ONE ZOTERO COLLECTION PER PROJECT, FOREVER. Nothing is ever moved out of it. That single
rule is what this module exists to make possible, and it fixes a whole class of failure:

  * `refs.bib` is built from the collection (`ledger.py` -> `zc.collection_bibtex`), so an
    item that LEAVES the collection leaves the bibliography. `audit` used to quarantine by
    MOVING items between collections, which meant a minted review could acquire a dangling
    citation from a command nobody thought of as touching bibliographies. Reconciliation
    could not catch it either: narrative and refs.bib shrink together.
  * A project can hold more than one review (substantive + methods), and one anchor cannot
    serve two questions. Two collections would split the bibliography; Zotero
    subcollections cannot help, because neither items endpoint recurses, `create_collection`
    cannot make one, and `find_collection` is flat, exact-match and unpaginated.

So role lives HERE, beside the project, not in collection membership:

    item_key · citekey · title · role · locked · added_by

`literature` and `methods` scope INGESTION — each review embeds only its own rows.
`quarantine` is excluded from every review's corpus while the item stays in the collection,
so the bibliography never loses it. Exclusion from the corpus, never from the record.

AUTHORITATIVE, NOT DERIVED. With no subcollection there is nothing to derive a role from,
so this file is state rather than a cache. The mitigation for state that can drift is that
it can be checked: `reconcile()` compares it against the live collection in both directions
and nothing silently disappears.

`locked` records that a human overruled the machine — `--release` sets it, and `audit` must
not re-quarantine a locked row. Without it a release does not survive the next audit, which
is how the verb behaved for as long as quarantine was a move.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from pathlib import Path

from . import config as _config

LEDGER_NAME = "corpus_ledger.json"
STATE_DIR = ".haarpi"

LITERATURE = "literature"
METHODS = "methods"
QUARANTINE = "quarantine"
ROLES = (LITERATURE, METHODS, QUARANTINE)

# Roles that put an item in a review's corpus. Quarantine is the one that does not.
INGESTED_ROLES = (LITERATURE, METHODS)


@dataclass
class Row:
    key: str                     # Zotero item key — the stable identity
    role: str = LITERATURE
    citekey: str = ""
    title: str = ""
    locked: bool = False         # a human set this role; the machine may not overrule it
    added_by: str = ""           # which review filed it, or "human"

    def ingestible(self) -> bool:
        return self.role in INGESTED_ROLES


def ledger_path(path: str | Path = ".") -> Path:
    """`<project>/.haarpi/corpus_ledger.json` — PROJECT level, not review level.

    One collection is shared by every review in the project, so its ledger is shared too.
    Callers hand us a review directory as often as a project root, so normalise: a review
    root's parent is the project.
    """
    p = Path(path)
    root = _config.work_root(p)
    project = root.parent if root != p or _is_review_dir(p) else p
    return project / STATE_DIR / LEDGER_NAME


def _is_review_dir(p: Path) -> bool:
    return _config.work_root(p) == p


def load(path: str | Path = ".") -> dict[str, Row]:
    """Every row, keyed by Zotero item key. Missing or unreadable file reads as empty —
    a ledger that cannot be parsed must not stop a run; `reconcile` rebuilds it."""
    fp = ledger_path(path)
    if not fp.exists():
        return {}
    try:
        blob = json.loads(fp.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    rows = {}
    for raw in blob.get("items", []):
        key = raw.get("key")
        if not key:
            continue
        rows[key] = Row(key=key,
                        role=raw.get("role", LITERATURE),
                        citekey=raw.get("citekey", ""),
                        title=raw.get("title", ""),
                        locked=bool(raw.get("locked", False)),
                        added_by=raw.get("added_by", ""))
    return rows


def save(path: str | Path, rows: dict[str, Row]) -> Path:
    fp = ledger_path(path)
    fp.parent.mkdir(parents=True, exist_ok=True)
    body = {"version": 1,
            "items": [asdict(r) for r in sorted(rows.values(), key=lambda r: r.key)]}
    fp.write_text(json.dumps(body, indent=1), encoding="utf-8")
    return fp


def roles_for(path: str | Path, role: str) -> set[str]:
    """Item keys carrying one role — what an ingest filters on."""
    return {k for k, r in load(path).items() if r.role == role}


def set_role(path: str | Path, key: str, role: str, *, locked: bool = False,
             title: str = "", added_by: str = "") -> Row:
    """Record a role. Refuses to overwrite a LOCKED row unless locking again.

    The refusal is the whole point of `locked`: `audit` calls this to quarantine, and a
    paper the human released must not be quarantined again on the next run.
    """
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r} — expected one of {ROLES}")
    rows = load(path)
    cur = rows.get(key)
    if cur is not None and cur.locked and not locked:
        return cur
    if cur is None:
        cur = Row(key=key, role=role, title=title, added_by=added_by)
        rows[key] = cur
    else:
        cur.role = role
        cur.title = title or cur.title
        cur.added_by = added_by or cur.added_by
    cur.locked = cur.locked or locked
    save(path, rows)
    return cur


@dataclass
class Reconciliation:
    """What the ledger and the live collection disagree about."""
    added: list[Row] = field(default_factory=list)      # in Zotero, had no row
    orphaned: list[Row] = field(default_factory=list)   # had a row, gone from Zotero
    total: int = 0

    @property
    def clean(self) -> bool:
        return not self.added and not self.orphaned


def reconcile(path: str | Path, items: list[dict], *, default_role: str = LITERATURE,
              added_by: str = "") -> tuple[dict[str, Row], Reconciliation]:
    """Bring the ledger level with the collection. Pure: returns rows, writes nothing.

    Every item in the collection gets a row — that is the invariant. An item with no row
    is a human addition (rabbitHole writes its own rows when it files a find), and takes
    `default_role`, which callers set to the role of the review being run: additions made
    while working on the methods review are methods.

    A row whose item has gone is REPORTED, not deleted. Losing the record of a human's
    decision because someone tidied Zotero is exactly the silent drift this guards against;
    the caller decides.
    """
    rows = load(path)
    rec = Reconciliation(total=len(items))
    seen = set()
    for raw in items:
        data = raw.get("data", {}) or {}
        key = data.get("key") or raw.get("key")
        if not key:
            continue
        seen.add(key)
        if key in rows:
            if not rows[key].title:
                rows[key].title = data.get("title", "")
            continue
        row = Row(key=key, role=default_role, title=data.get("title", ""),
                  added_by=added_by or "human")
        rows[key] = row
        rec.added.append(row)
    rec.orphaned = [r for k, r in rows.items() if k not in seen]
    return rows, rec


def format_reconciliation(rec: Reconciliation, *, prefix: str = "  ") -> list[str]:
    """Human-readable lines for a run log. Silent when there is nothing to say."""
    out: list[str] = []
    if rec.added:
        out.append(f"{prefix}{len(rec.added)} item(s) in Zotero with no ledger row:")
        for r in rec.added[:20]:
            out.append(f"{prefix}  {r.key}  {(r.title or '?')[:58]:<58} -> {r.role}")
        if len(rec.added) > 20:
            out.append(f"{prefix}  … and {len(rec.added) - 20} more")
    if rec.orphaned:
        out.append(f"{prefix}{len(rec.orphaned)} ledger row(s) whose item is gone from Zotero:")
        for r in rec.orphaned[:20]:
            out.append(f"{prefix}  {r.key}  {(r.title or '?')[:58]}")
    return out


def sync(paths, cfg, gc, *, quiet: bool = False) -> Reconciliation:
    """Bring the ledger level with the live collection, and write it.

    Called wherever the collection may have changed under us — after `gather` files its
    finds, and at `collect` once the human has added theirs. New items take the role of
    the review being run, which is the useful default in both cases: rabbitHole's own
    finds belong to the review that searched for them, and a paper you add while working
    on the methods review is a methods paper.

    Network failures are not fatal. A ledger that is one gather behind is repaired by the
    next sync; refusing to run because Zotero was unreachable would be worse.
    """
    from . import config as _cfg
    project_root = _cfg.work_root(paths.root).parent
    kind = _cfg.kind_of(paths.root)
    try:
        from . import zotero
        zc = zotero.ZoteroClient(gc)
        coll = cfg.zotero.get("collection_key") or zc.find_collection(cfg.project_name)
        if not coll:
            return Reconciliation()
        items = [it for it in zc.collection_items(coll)
                 if (it.get("data", {}) or {}).get("itemType") not in ("attachment", "note")]
    except Exception as e:  # noqa: BLE001
        if not quiet:
            print(f"  [warn] could not sync the corpus ledger ({e}); it will catch up next run.")
        return Reconciliation()

    rows, rec = reconcile(project_root, items, default_role=kind.name, added_by=kind.name)
    save(project_root, rows)
    if not quiet:
        for line in format_reconciliation(rec):
            print(line)
    return rec


def run_collect(directory: str = ".", *, set_roles: dict[str, str] | None = None,
                default_role: str | None = None) -> int:
    """`rabbitHole collect` — code newly-added papers into the ledger and say what is ready.

    Collect has always been the human's step: rabbitHole lists what it could not find, you
    download the PDFs and add them to Zotero. It had no tool support at all — the planner
    queued it with command `None`. This is that support, and it does two things a person
    should not have to do by hand.

    FIRST, every item in the collection gets a role. What rabbitHole found already has one
    (gather wrote it), so anything still missing a row is by construction something you
    added, and it takes the role of the review you are standing in: additions made while
    working on the methods review are methods. Override any of them with `--role KEY=role`.

    SECOND, it reports which items cannot yet be used — an item with no PDF is in the
    bibliography but will never reach the corpus, and that is worth knowing at collect time
    rather than discovering it in a thin draft weeks later.
    """
    from . import config as _cfg, runlog
    runlog.start()
    cfg = _cfg.load_project(directory)
    gc = _cfg.load_global()
    paths = _cfg.project_paths(directory)
    project_root = _cfg.work_root(directory).parent
    kind = _cfg.kind_of(paths.root)

    print(f"rabbitHole collect — {cfg.project_name} ({kind.name} review)")
    if not (gc.have_zotero and (cfg.zotero.get("collection_key") or cfg.project_name)):
        print("  [error] collect needs a Zotero collection (run gather first).")
        return 1

    rec = sync(paths, cfg, gc)
    if rec.clean and not set_roles:
        print(f"  {runlog.stamp()}Ledger is level with the collection "
              f"({rec.total} item(s)); nothing new to code in.")

    for key, role in (set_roles or {}).items():
        try:
            row = set_role(project_root, key, role, locked=True, added_by="human")
        except ValueError as e:
            print(f"  [error] {e}")
            return 1
        print(f"  {key} -> {row.role} (locked)")

    if default_role and rec.added:
        for row in rec.added:
            set_role(project_root, row.key, default_role, added_by="human")
        print(f"  {len(rec.added)} new item(s) -> {default_role}")

    rows = load(project_root)
    mine = [r for r in rows.values() if r.role == kind.name]
    have_pdf = {p.stem for p in paths.pdfs.glob("*.pdf")} if paths.pdfs.exists() else set()
    missing = [r for r in mine if r.key not in have_pdf]
    print(f"  {runlog.stamp()}{len(mine)} item(s) carry the '{kind.name}' role; "
          f"{len(mine) - len(missing)} have a PDF.")
    if missing:
        print(f"  {len(missing)} still need one — they are in the bibliography but will not "
              f"reach the corpus until a PDF lands in {paths.pdfs}:")
        for r in missing[:15]:
            print(f"    {r.key}  {(r.title or '?')[:64]}")
        if len(missing) > 15:
            print(f"    … and {len(missing) - 15} more")
    return 0

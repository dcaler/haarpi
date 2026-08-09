"""razzle.interview — a PURE-PYTHON, no-LLM interview that configures a project's deck(s).

The deck stage splits its opening in two: this interview gathers the DETERMINISTIC facts a deck
needs — which presentation formats to build, and per format the venue, date, presenting authors,
their affiliation logos, and the funders to acknowledge — while the AUTHORING (composing the spec
from the one-pager + figures) stays the LLM session `razzle deck`. Facts a tool must never invent
belong to the human, so they are collected by asking, not by a model.

It writes `deck_formats` + `decks` to the manifest and auto-queues one `razzle deck` authoring
session per format (removing the deck-open "pick format(s)" prompt it fulfils). Input is plain
`input()`, so it is scriptable in tests by patching builtins.input.
"""

from __future__ import annotations

from pathlib import Path

from haarpi import config as _hconfig
from haarpi import project as _hproject
from haarpi import trundlr as _trundlr

from razzle import assets
from razzle import formats as _formats

_PROMPT_TITLE = "razzle deck: pick format(s)"      # the deck-open task this interview fulfils


def _ask(label: str, default: str = "") -> str:
    r = input(f"{label}" + (f" [{default}]" if default else "") + ": ").strip()
    return r or default


def _ask_yn(label: str, default: bool = True) -> bool:
    r = input(f"{label} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
    return default if not r else r.startswith("y")


def _pick_many(label: str, options: list[str]) -> list[str]:
    """Numbered menu; accept comma/space-separated indices, or Enter/'all' for all."""
    if not options:
        return []
    print(f"{label}:")
    for i, o in enumerate(options, 1):
        print(f"  {i}. {o}")
    raw = input("  choose (numbers, or Enter for all): ").strip().lower()
    if not raw or raw == "all":
        return list(options)
    picked = [options[int(t) - 1] for t in raw.replace(",", " ").split()
              if t.isdigit() and 1 <= int(t) <= len(options)]
    return picked or list(options)


def _author_names(m) -> list[str]:
    return [a.get("name") for a in (m.authors or []) if isinstance(a, dict) and a.get("name")]


def _funder_names(m) -> list[str]:
    return [f.get("name") for f in (m.funders or []) if isinstance(f, dict) and f.get("name")]


def _affiliations_of(m, author_names: list[str]) -> list[str]:
    """The affiliations of the presenting authors, de-duplicated in first-seen order."""
    seen: dict[str, None] = {}
    for a in (m.authors or []):
        if isinstance(a, dict) and a.get("name") in author_names:
            for aff in a.get("affiliations", []) or []:
                seen.setdefault(aff, None)
    return list(seen)


def _logo_status(aff: str) -> str:
    entry = assets._registry("affiliations").get(aff)
    if entry and entry.get("logo") and (assets.home() / entry["logo"]).is_file():
        return "logo ✓"
    return "NO LOGO → text only"


def _configure_format(m, fmt: str) -> dict:
    mins = _formats.minutes(fmt)
    print(f"\n=== {fmt}" + (f" (~{mins} min, ~{_formats.slide_budget(fmt)} slides)" if mins else
                            " (poster)") + " ===")
    venue = _ask("  Venue name")
    date = _ask("  Date")
    authors = _pick_many("  Presenting authors", _author_names(m))
    affs = []
    for aff in _affiliations_of(m, authors):
        if _ask_yn(f"  Include affiliation logo '{aff}' ({_logo_status(aff)})", default=True):
            affs.append(aff)
    funders = _pick_many("  Funders to acknowledge", _funder_names(m))
    return {"venue": venue, "date": date, "authors": authors,
            "affiliations": affs, "funders": funders}


def _queue_sessions(m, formats_chosen: list[str]) -> list[str]:
    """One `razzle deck --format <fmt>` authoring session per format; delete the pick-format prompt
    it fulfils. Best-effort — no trundlr configured means the interview still wrote the config."""
    tr = _hconfig.merged_config("haarpi", {}).get("trundlr", {})
    if not (tr.get("url") and m.trundlr_project_id):
        return []
    try:
        client = _trundlr.TrundlrClient(tr["url"])
        human = tr.get("human_resource") or None
        for fmt in formats_chosen:
            client.create_task(
                f"razzle deck {fmt}", m.trundlr_project_id,
                description=f"Author the {fmt} deck from the paper's one-pager + the figures/claims, "
                            f"then render — run: haarpi razzle deck --format {fmt}",
                resource_id=human, duration=2.0)
        for t in client.list_tasks(m.trundlr_project_id):
            if (t.get("title") or "").strip() == _PROMPT_TITLE and t.get("status") != "done":
                client.delete_task(t["id"])
    except _trundlr.TrundlrError:
        return []
    return formats_chosen


def run(root: Path, *, queue: bool = True) -> dict:
    """The interview: configure the project's decks and (optionally) queue the authoring sessions.
    Returns {"formats": [...], "decks": {...}, "queued": [...]}."""
    m = _hproject.load_manifest(root)
    print(f"razzle interview — configuring decks for {m.name or root.name}\n")
    chosen = _pick_many("Presentation formats to build", list(_formats.FORMATS))
    if not chosen:
        print("No formats chosen — nothing to configure.")
        return {"formats": [], "decks": dict(m.decks or {}), "queued": []}

    decks = dict(m.decks or {})
    for fmt in chosen:
        decks[fmt] = _configure_format(m, fmt)
    m.deck_formats = chosen
    m.decks = decks
    _hproject.save_manifest(m, root)

    queued = _queue_sessions(m, chosen) if queue else []
    print(f"\nWrote deck config for: {', '.join(chosen)}")
    if queued:
        print(f"Queued authoring session(s): {', '.join('razzle deck ' + f for f in queued)}")
    elif queue:
        print("(no trundlr configured — run `haarpi razzle deck --format <fmt>` yourself)")
    return {"formats": chosen, "decks": decks, "queued": queued}

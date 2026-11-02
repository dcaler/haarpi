"""Signing off an interview's document in the terminal, once the Claude session has exited.

An attended interview (ramus init / design, raster plan, rayleigh plan / review) ends with a
`_ra` document the author just built with Claude. Sending it to a separate review repeats a
decision already made in the session (DigiPros' scope, 2026-11-01: the only way to honour
"consider it mintable" was renaming the file to `_DCR` by hand, which claims an annotation
that never happened).

So when the session exits, `haarpi` itself — never Claude — shows the document's path and
outline and asks for a sign-off. Only the exact answers `yes` or `no` count; anything else is
asked again. `yes` records the sign-off in `.haarpi/signoffs.json`, keyed on the file's
content, and the gate (`planner.find_finished_markup`) accepts that file as finished markup —
its name stays `_ra`, the honest record of who wrote it. `no`, or no terminal at all (a runner,
a script), leaves the normal review in place.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import zipfile
from pathlib import Path

from . import project

SIGNOFFS = "signoffs.json"
QUESTION = "Sign off this document as reviewed in session? (yes/no): "


def _path(root: Path) -> Path:
    return root / project.STATE_DIR / SIGNOFFS


def _digest(p: Path) -> str:
    return hashlib.sha1(p.read_bytes()).hexdigest()


def _load(root: Path) -> list[dict]:
    try:
        return json.loads(_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def record(root: Path, doc: Path, stage: str) -> None:
    rows = _load(root)
    rows.append({"file": doc.name, "stage": stage, "sha1": _digest(doc),
                 "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "how": "signed off in session"})
    fp = _path(root)
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(json.dumps(rows, indent=2), encoding="utf-8")


def signed_off(root: Path, doc: Path) -> bool:
    """Was THIS content signed off? A re-rendered file under the same name is not."""
    rows = [r for r in _load(root) if r.get("file") == doc.name]
    if not rows or not doc.is_file():
        return False
    sha = _digest(doc)
    return any(r.get("sha1") == sha for r in rows)


_HEADING = re.compile(rb'<w:p\b.*?</w:p>', re.S)


def outline(doc: Path, limit: int = 24) -> list[str]:
    """The document's headings, indented by level, read from the docx itself — what will be
    minted, not what the session meant to write."""
    try:
        xml = zipfile.ZipFile(doc).read("word/document.xml")
    except (OSError, KeyError, zipfile.BadZipFile):
        return []
    out = []
    for para in _HEADING.findall(xml):
        m = re.search(rb'<w:pStyle w:val="(?:Heading|heading)\s?(\d)"', para)
        if not m:
            continue
        text = "".join(t.decode("utf-8", "replace")
                       for t in re.findall(rb"<w:t[^>]*>([^<]*)</w:t>", para)).strip()
        if text:
            out.append("  " * (int(m.group(1)) - 1) + text)
    return out[:limit] + ([f"… and {len(out) - limit} more headings"] if len(out) > limit else [])


def ask(prompt=input) -> bool:
    """Exactly `yes` or `no`, lower case; anything else is asked again. End of input is a no."""
    while True:
        try:
            ans = prompt(QUESTION).strip()
        except EOFError:
            return False
        if ans in ("yes", "no"):
            return ans == "yes"
        print("  Please type yes or no.")


def newest_draft(root: Path, stage: str, since: float) -> Path | None:
    """The newest `_ra` docx the session wrote in this stage's live folders."""
    from . import naming, planner
    m = project.load_manifest(root)
    if stage not in m.stages:
        return None
    best = None
    for d in planner._markup_dirs(root, m, stage):
        for p in d.glob("*.docx"):
            parsed = naming.parse(p, m.short_title)
            if (not parsed or not parsed[1] or parsed[1][-1].lower() != "ra"
                    or p.stat().st_mtime < since):
                continue
            if best is None or p.stat().st_mtime > best.stat().st_mtime:
                best = p
    return best


def after_session(root: Path | None, stage: str, since: float, *, prompt=input,
                  interactive: bool | None = None) -> bool:
    """Ask for the sign-off on the document this session produced. Never raises: a failure
    here must not fail the session, it only leaves the normal review in place."""
    try:
        if root is None:
            return False
        doc = newest_draft(root, stage, since)
        if doc is None:
            return False
        if not (sys.stdin.isatty() if interactive is None else interactive):
            print(f"  {doc.name} goes to review (no terminal to sign it off in).")
            return False
        print(f"\n  The session produced: {doc.relative_to(root)}")
        lines = outline(doc)
        if lines:
            print("  Its outline:")
            for ln in lines:
                print(f"    {ln}")
        if not ask(prompt):
            print("  Not signed off: it goes to review as usual.")
            return False
        record(root, doc, stage)
        print(f"  Signed off: `haarpi next` mints {doc.name} as it stands (if a review task "
              f"for it is on the board, mark that done).")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"  [sign-off] skipped ({e}); the document goes to review as usual.",
              file=sys.stderr)
        return False

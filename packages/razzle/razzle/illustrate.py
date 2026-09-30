"""razzle.illustrate — draw the pictures the composer briefed, and let the author choose.

A content slide with nothing to show may carry an `illustration` brief. A PICTORIAL brief (a scene,
an object, a metaphor) can be drawn by `imagine` — the local Stable Diffusion command — so razzle
turns each one into a prompt and, as its own GPU-booked task, draws a few candidates of each. A
SCHEMATIC brief (a diagram, a chart, a worked example) cannot: a diffusion model draws a plausible
chart of data nobody has and labels it in glyphs that are not letters. Those stay production TODOs
in the notes pane, exactly as before.

    slides/<venue>/illustrations/
        prompts.json           what to draw — written by every render, read by `illustrate`
        <key>/cand_0.png …     the candidates (`imagine -n`)
        <key>/chosen.png       the AUTHOR's pick — nothing reaches a slide that nobody chose
        placed.json            which pick each key's slide was last rendered with

The key is the slide's position plus a hash of its brief, so a re-authored deck that keeps a
brief keeps its candidates and its pick, and a brief that changed is drawn afresh.

THE NOTE. While any pictorial brief is unsettled, the rendered deck carries one comment signed by
razzle saying so. It is a tool's comment, so it never counts as the author having reviewed the
deck — but it is unresolved, so the deck cannot mint past it. Resolving it is the author saying
"the illustrations are as I want them" (placed, or none at all); resolving is the human's act,
never a tool's.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path


DIRNAME = "illustrations"
CHOSEN = "chosen.png"
AUTHOR = "razzle"            # the note's signature — one of redline.TOOL_AUTHORS
INITIALS = "ra"

COUNT = 3                    # candidates per brief
SIZE = 512                   # ~35 s an image on a P40; the author picks, then it is placed small
STYLE = ("clean flat editorial illustration, soft muted colours, plain light background, "
         "no text, no letters, no labels")

# A brief naming one of these is schematic whatever the composer tagged it: a picture of it would
# be a fabricated figure. Fixed here rather than trusted to the prompt, like the other budgets.
_SCHEMATIC = re.compile(
    r"\b(diagram|schematic|chart|graph|plot|axis|axes|table|flow ?chart|timeline|equation|"
    r"formula|histogram|heat ?map|scatter|matrix|map of|legend|labell?ed|worked example|"
    r"piano roll|score|notation)\b", re.I)

# `imagine` refuses rather than queues when no card has room. trundlr's GPU lane keeps other
# pipeline work off the card, but an Ollama model from the step before may still be resident
# for its keep-alive — so a refusal is waited out, not failed on.
_BUSY = re.compile(r"MiB free", re.I)
WAIT_S = 30
WAIT_MAX_S = 20 * 60


def kind_of(slide: dict) -> str:
    """`pictorial` or `schematic` — the composer's tag, overruled by what the brief names."""
    brief = str(slide.get("illustration") or "")
    if _SCHEMATIC.search(brief):
        return "schematic"
    k = str(slide.get("illustration_kind") or "").strip().lower()
    return k if k in ("pictorial", "schematic") else "pictorial"


def home(deck_dir: Path) -> Path:
    return deck_dir / DIRNAME


def key_for(index: int, brief: str) -> str:
    return f"slide{index + 1:02d}_{hashlib.sha1(brief.encode('utf-8')).hexdigest()[:6]}"


def prompt_for(brief: str) -> str:
    return f"{brief.rstrip('. ')}. {STYLE}"


def briefs(spec: list[dict]) -> list[dict]:
    """Every pictorial brief in the spec, in slide order: [{key, slide, index, brief, prompt}]."""
    out = []
    for i, s in enumerate(spec):
        brief = str(s.get("illustration") or "").strip()
        if not brief or s.get("figure") or kind_of(s) != "pictorial":
            continue
        out.append({"key": key_for(i, brief), "slide": i + 1, "index": i,
                    "brief": brief, "prompt": prompt_for(brief)})
    return out


def candidates(deck_dir: Path, key: str) -> list[Path]:
    return sorted((home(deck_dir) / key).glob("cand_*.png"))


def chosen(deck_dir: Path, key: str) -> Path | None:
    p = home(deck_dir) / key / CHOSEN
    return p if p.is_file() else None


def _digest(p: Path) -> str:
    return hashlib.sha1(p.read_bytes()).hexdigest()


def _placed(deck_dir: Path) -> dict:
    try:
        return json.loads((home(deck_dir) / "placed.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def read_prompts(deck_dir: Path) -> list[dict]:
    try:
        return json.loads((home(deck_dir) / "prompts.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def needs_placing(deck_dir: Path) -> list[str]:
    """Keys whose pick differs from what the deck was last rendered with — the author chose (or
    re-chose) since, so the deck is stale and a re-render, not a mint, is what comes next."""
    placed = _placed(deck_dir)
    out = []
    for b in read_prompts(deck_dir):
        c = chosen(deck_dir, b["key"])
        if c is not None and placed.get(b["key"]) != _digest(c):
            out.append(b["key"])
    return out


def last_pick_time(deck_dir: Path) -> float:
    """When the author last chose anything (0 if never) — the deck's 'a human went last' time
    when choosing is all they did."""
    ts = [p.stat().st_mtime for p in home(deck_dir).glob(f"*/{CHOSEN}")]
    return max(ts, default=0.0)


# ── the render's half ────────────────────────────────────────────────────────

def prepare(deck_dir: Path, spec: list[dict]) -> dict:
    """Before a render: write the prompt list and work out what goes on which slide.

    Returns {placements: {spec index: image}, note: text or None, briefs: [...]}. The note says
    what the author has left to do; it is None only when there is no pictorial brief at all.
    """
    bs = briefs(spec)
    h = home(deck_dir)
    if bs or h.is_dir():
        h.mkdir(parents=True, exist_ok=True)
        (h / "prompts.json").write_text(json.dumps(bs, indent=2), encoding="utf-8")
    placements = {b["index"]: c for b in bs if (c := chosen(deck_dir, b["key"])) is not None}
    return {"placements": placements, "briefs": bs, "note": _note(deck_dir, bs, placements)}


def _note(deck_dir: Path, bs: list[dict], placements: dict) -> str | None:
    if not bs:
        return None
    rel = f"{deck_dir.name}/{DIRNAME}"
    waiting = [b for b in bs if b["index"] not in placements]
    placed = [b for b in bs if b["index"] in placements]
    lines = ["Illustrations pending."]
    if placed:
        lines.append("Placed from your picks: slide " + ", ".join(str(b["slide"]) for b in placed)
                     + ".")
    if waiting:
        lines.append("Not yet picked: slide " + ", ".join(str(b["slide"]) for b in waiting)
                     + f". Candidates are drawn into slides/{rel}/<slide…>/cand_*.png by the "
                       "illustrate task; copy the one you want to chosen.png in the same folder.")
    lines.append("Leave this comment UNRESOLVED and finish your review: haarpi next re-renders "
                 "with your picks in place. RESOLVE it once the illustrations are as you want "
                 "them (or to go without) — the deck cannot be released while it is open.")
    return " ".join(lines)


def record_placed(deck_dir: Path, placements: dict, bs: list[dict]) -> None:
    """After a render: remember which pick each slide now shows (see `needs_placing`)."""
    if not bs:
        return
    by_index = {b["index"]: b["key"] for b in bs}
    placed = {by_index[i]: _digest(p) for i, p in placements.items() if i in by_index}
    (home(deck_dir) / "placed.json").write_text(json.dumps(placed, indent=2), encoding="utf-8")


# ── the `illustrate` verb ────────────────────────────────────────────────────

def _imagine(prompt: str, out: Path, *, count: int, size: int) -> tuple[int, str]:
    r = subprocess.run(["imagine", "-s", str(size), "-n", str(count), "-o", str(out), prompt],
                       capture_output=True, text=True)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def run(deck_dir: Path, *, count: int = COUNT, size: int = SIZE,
        wait_s: float = WAIT_S, wait_max_s: float = WAIT_MAX_S) -> int:
    """Draw candidates for every brief in the prompt list that has none yet.

    Idempotent: a brief with candidates on disk is skipped, so a re-run after a partial failure
    draws only what is missing, and a re-authored deck re-draws only the briefs that changed.
    """
    bs = read_prompts(deck_dir)
    if not bs:
        print(f"razzle illustrate: no pictorial briefs in "
              f"{home(deck_dir)}/prompts.json — nothing to draw")
        return 0
    if shutil.which("imagine") is None:
        print(f"razzle illustrate: `imagine` is not on PATH on this machine — "
              f"the {len(bs)} brief(s) stay as notes in the deck", file=sys.stderr)
        return 1
    todo = [b for b in bs if not candidates(deck_dir, b["key"])]
    print(f"razzle illustrate: {len(bs)} brief(s), {len(todo)} to draw "
          f"({count} × {size}px each)")
    failed = 0
    for n, b in enumerate(todo, 1):
        d = home(deck_dir) / b["key"]
        d.mkdir(parents=True, exist_ok=True)
        print(f"  [{n}/{len(todo)}] slide {b['slide']}: {b['brief']}")
        waited = 0.0
        while True:
            rc, out = _imagine(b["prompt"], d / "cand", count=count, size=size)
            if rc == 0 or not _BUSY.search(out) or waited >= wait_max_s:
                break
            print(f"  no card has room yet (a model still resident?) — "
                  f"waiting {int(wait_s)} s")
            time.sleep(wait_s)
            waited += wait_s
        if rc != 0:
            failed += 1
            tail = out.strip().splitlines()[-1:] or ["(no output)"]
            print(f"  slide {b['slide']}: imagine failed (exit {rc}): {tail[0]}",
                  file=sys.stderr)
            continue
        if count == 1 and (d / "cand.png").is_file():      # imagine names a single image bare
            (d / "cand.png").rename(d / "cand_0.png")
        print(f"  slide {b['slide']}: {len(candidates(deck_dir, b['key']))} "
              f"candidate(s) in {d}")
    print(f"razzle illustrate: done — {len(todo) - failed} drawn, {failed} failed. "
          f"Pick one per slide by copying it to {CHOSEN} in its folder.")
    return 1 if failed else 0

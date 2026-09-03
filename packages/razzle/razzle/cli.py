"""razzle CLI — `razzle <deck|render>`.

`deck`   gathers a project's inputs (the one-pager, figures, claims, logos), sizes to a presentation
         FORMAT, and launches an interactive authoring session (or prints the manual path) to write
         the deck spec — `slides/{fmt}/spec.json`.
`render` renders that spec against the neutral house master into the branded `.pptx`.

The spec is the durable artifact; render is deterministic and re-runnable.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from haarpi import figure as _figure
from haarpi import naming as _naming

from razzle import assets, compose, formats, gather, render
from haarpi import runlog


DECK_PROMPT = (
    "You are running `razzle deck` — author a venue-specific presentation deck for the {fmt} format "
    "(~{mins} minutes, aim for ~{budget} slides at 1 slide/minute). razzle has gathered the inputs; "
    "read them: the one-pager is the talk's SPINE (re-present it, do not re-argue), the figure pool "
    "(reference figures by id), and the real claims/numbers (use verbatim; never invent one). Write "
    "the deck spec to slides/{fmt}/spec.json — a JSON list of slides, each {{role: title|figure|"
    "split|content, title, subtitle?, body?, figure?<id>, citation?, illustration?}}: open on a "
    "title slide, one idea per slide. A title is the slide's CLAIM in <=9 words, not its topic, in "
    "sentence case. At most 3 bullets, <=9 words each, fragments not sentences, and never one that "
    "only repeats the title. Prefer `split` (a point beside its figure) and `figure` over "
    "`content`, and use every figure at least once — a slide that can show something shows it. At "
    "least one results slide must state a real number from the claims verbatim. Every figure in "
    "the pool is THIS paper's own work, so a slide showing one carries NO `citation` — that would "
    "misattribute our own result; cite only on a slide with no figure, as a bare source ref. NO "
    "speaker notes: what does not fit is spoken. A `content` slide may instead carry "
    "`illustration` — one line briefing a picture that does not exist yet, for whoever draws it. "
    "Do not write a title slide title, a byline or an acknowledgements slide: razzle stamps those "
    "from the paper and the deck config. Bullets go under `body` (a JSON array of strings) — that "
    "is the key the renderer reads. Write ONLY that one file and then stop: razzle renders it "
    "itself, so do not run any render command. Start by reading the one-pager."
)


def _figures_for(root: Path, short: str, spec: list[dict]) -> dict[str, str]:
    out: dict[str, str] = {}
    for s in spec:
        fid = s.get("figure")
        if fid and fid not in out:
            png = _figure.resolve(root, short, fid, "png", width=1600)
            if png:
                out[fid] = str(png)
    return out


def run_render(args) -> int:
    root = Path(args.dir).resolve() if args.dir else Path.cwd()
    fmt = args.format
    spec_path = gather.deck_dir(root, fmt) / "spec.json"
    if not spec_path.is_file():
        print(f"razzle render: no {spec_path.relative_to(root)} — run `razzle deck --format {fmt}` first",
              file=sys.stderr)
        return 1
    desc = assets.descriptor(args.master)
    if desc is None or not desc.get("master_path"):
        print(f"razzle render: no house master '{args.master}' in {assets.home()} — cannot render",
              file=sys.stderr)
        return 1
    short = gather.short_title(root)
    # NORMALISE what was authored by hand, exactly as the programmatic path normalises what a
    # model returns. The spec is written by a session, so it arrives with a session's habits: a
    # `notes` field out of old muscle memory, `bullets` where the renderer reads `body`, four
    # bullets where the budget is three, a figure id that does not exist. Rendering it raw meant
    # every rule in compose.py applied only to a path nothing calls.
    raw = json.loads(spec_path.read_text(encoding="utf-8"))
    spec = compose.normalise(raw, {f["id"] for f in gather.figures(root, short)})
    # the facts, re-stamped on every render: the paper's title (before the footer is built from
    # it), every author with one contact address, and the closing acknowledgements
    gather.apply_title(spec, gather.talk_title(root, fmt))
    gather.apply_byline(spec, gather.byline(root), gather.presenter_email(root, fmt))
    spec = gather.apply_acknowledgements(spec, gather.acknowledgements(root, fmt))
    # The `_ra` chain draft — the author reviews it in place; `haarpi next` mints it (the format is
    # carried by the folder, so the filename infix is just `deck`).
    out = gather.deck_dir(root, fmt) / _naming.major_name(short, "pptx", infix="deck")
    render.render_deck(spec, desc["master_path"], desc, out,
                       figures=_figures_for(root, short, spec),
                       logos=gather.logo_entries(root, fmt),
                       furniture=gather.furniture(root, fmt, spec))
    print(f"razzle render: wrote {out.relative_to(root)}  ({len(spec)} slides)")
    return 0


def _author_headless(args, root: Path, fmt: str, prompt: str) -> int:
    """Author the spec unattended, then render it here.

    The authoring pass has no decision left in it — the interview settled every fact a tool must
    not invent, and the rendered .pptx meets the human at the redline gate — so `claude -p` writes
    the spec and exits instead of opening a session nobody is sitting at.

    Two things this has to get right, both learned the hard way on the first live run:

    * PERMISSIONS. A print-mode session has no write permission by default. The first run composed
      a perfectly good deck in its REPLY, asked for "write access and permission to run render",
      and exited 0 — the task went green having produced nothing. It gets the tools it needs, and
      no Bash: rendering is razzle's job, not the session's, which is one fewer permission and one
      fewer thing to go wrong.
    * PROOF. The session's exit code says the conversation ended, not that a deck exists. So the
      spec must be there and newer than the moment we started, or this fails. A queued task that
      reports success without a deliverable is worse than one that fails: the board chains a
      review of something that was never written.
    """
    spec_path = gather.deck_dir(root, fmt) / "spec.json"
    started = time.time()
    print(f"[razzle deck] authoring {fmt} headlessly in {root} …")
    rc = subprocess.run(
        ["claude", "-p", prompt,
         "--permission-mode", "acceptEdits",
         "--allowedTools", "Read,Write,Edit,Glob,Grep"],
        cwd=str(root)).returncode
    if rc != 0:
        print(f"razzle deck: the authoring session failed (exit {rc})", file=sys.stderr)
        return rc
    if not spec_path.is_file() or spec_path.stat().st_mtime < started:
        print(f"razzle deck: the session ended without writing {spec_path.relative_to(root)} — "
              "nothing was authored", file=sys.stderr)
        return 1
    print(f"[razzle deck] authored {spec_path.relative_to(root)}; rendering …")
    return run_render(argparse.Namespace(dir=str(root), format=fmt, master=args.master))


def run_deck(args) -> int:
    root = Path(args.dir).resolve() if args.dir else Path.cwd()
    return _author_one(args, root, args.format)


def _local_brain():
    """The pipeline's own coordinator, over Ollama. Deck authoring is a CONSTRAINED transform —
    a one-pager and a manuscript in, ~18 small JSON objects out, under budgets the normaliser
    enforces afterwards — so it belongs on the same local model every other working loop uses."""
    from haarpi import config as _hcfg
    from haarpi.brain import Brain
    o = (_hcfg.merged_config("razzle") or {}).get("ollama", {})
    return Brain(o.get("url", "http://localhost:11434"),
                 o.get("coordinator", "qwen3.6:27b-16k"),
                 o.get("worker", "llama3.1:8b"), tool="razzle")


def _author_offline(args, root: Path, fmt: str, bundle: dict | None = None) -> int:
    """gather -> compose -> render, in this process, on the local brain.

    No session, so none of a session's failure modes: no permission prompt, no PATH lookup, no
    exit code that reports success for a deck that was never written, no spec key to agree on.
    The budgets live in compose.normalise either way, so what the brain has to get right is the
    slide selection, the order, and the phrasing.
    """
    from razzle import deck as _deck
    try:
        out = _deck.build_deck(root, fmt, _local_brain(), master=args.master, bundle=bundle)
    except Exception as e:
        print(f"razzle deck: authoring {fmt} failed — {e}", file=sys.stderr)
        return 1
    spec, pptx = out["spec"], out["pptx"]
    if pptx is None:
        print(f"razzle deck: wrote the spec ({len(spec)} slides) but found no house master "
              f"'{args.master}' — nothing rendered", file=sys.stderr)
        return 1
    print(f"razzle deck: wrote {pptx.relative_to(root)}  ({len(spec)} slides)")
    return 0


def _author_one(args, root: Path, fmt: str) -> int:
    if fmt not in formats.FORMATS:
        print(f"razzle deck: unknown format {fmt!r} — one of {sorted(formats.FORMATS)}", file=sys.stderr)
        return 2
    b = gather.bundle(root, fmt)
    budget = formats.slide_budget(fmt) or 15
    mins = formats.minutes(fmt)
    print(f"razzle deck — {fmt}" + (f" (~{mins} min, ~{budget} slides)" if mins else " (poster)"))
    print(f"  gathered: narrative {len(b['narrative'])} chars · {len(b['figures'])} figure(s) · "
          f"claims {'yes' if b['claims'] else 'none'} · {len(b['logos'])} logo(s)")
    gather.deck_dir(root, fmt).mkdir(parents=True, exist_ok=True)
    # --no-launch means "gather and stop", and it has to be honoured BEFORE any brain is reached:
    # it is the inspection path, and it is what lets the tests exercise gathering without a model.
    if getattr(args, "no_launch", False):
        print(f"  Author {gather.deck_dir(root, fmt).relative_to(root)}/spec.json, "
              f"then `haarpi razzle render --format {fmt}`.")
        return 0
    if not getattr(args, "claude", False):
        return _author_offline(args, root, fmt, b)
    have_claude = shutil.which("claude") is not None
    if getattr(args, "headless", False) and not have_claude:
        # Never the manual path here. Headless is how a QUEUED task runs, and a task that prints
        # instructions nobody reads and exits 0 goes green having authored nothing — the board
        # would then chain the review of a deck that does not exist.
        print("razzle deck: --headless needs `claude` on PATH and there is none", file=sys.stderr)
        return 1
    if not have_claude:
        print(f"  Author {gather.deck_dir(root, fmt).relative_to(root)}/spec.json, "
              f"then `haarpi razzle render --format {fmt}`.")
        return 0
    prompt = DECK_PROMPT.format(fmt=fmt, mins=mins or 0, budget=budget)
    if getattr(args, "headless", False):
        return _author_headless(args, root, fmt, prompt)
    print(f"[razzle deck] launching an interactive authoring session for {fmt} in {root} …")
    return subprocess.run(["claude", prompt], cwd=str(root)).returncode


def run_interview(args) -> int:
    from razzle import interview
    root = Path(args.dir).resolve() if args.dir else Path.cwd()
    interview.run(root)
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="razzle", description="Author + render venue-specific decks.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, help_ in (("deck", "gather inputs + author the deck spec (interactive)"),
                        ("render", "render slides/<fmt>/spec.json to the branded .pptx")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--dir", help="project root (default: cwd)")
        p.add_argument("--format", default="longtalk",
                       help="presentation format: " + ", ".join(sorted(formats.FORMATS)))
        p.add_argument("--venue", default="",
                       help="the venue this deck is for — resolves to whichever configured "
                            "format targets it (what the queued commands use)")
        p.add_argument("--master", default="default", help="house master name (neutral)")
        if name == "deck":
            p.add_argument("--no-launch", action="store_true",
                           help="gather + print the manual path instead of launching claude")
            p.add_argument("--claude", action="store_true",
                           help="author with the cloud coordinator instead of the local brain "
                                "(explicitly-optional, human-invoked — see the README)")
            p.add_argument("--headless", action="store_true",
                           help="with --claude: author unattended (`claude -p`) rather than "
                                "opening a session")
    iv = sub.add_parser("interview", help="pure-python interview: configure the deck(s) (writes config only)")
    iv.add_argument("--dir", help="project root (default: cwd)")
    return ap


def _resolve_venue(args) -> int:
    """`--venue` names the deck; razzle authors a FORMAT. Turn one into the other, once, here —
    so every path below deals only in formats and the board can stay venue-scoped."""
    venue = (getattr(args, "venue", "") or "").strip()
    if not venue:
        return 0
    root = Path(args.dir).resolve() if args.dir else Path.cwd()
    fmt = gather.format_for_venue(root, venue)
    if not fmt:
        print(f"razzle: no configured deck targets venue {venue!r} — "
              "run `haarpi razzle interview` first", file=sys.stderr)
        return 1
    args.format = fmt
    return 0


def main(argv=None) -> int:
    runlog.stamp_output()
    args = build_parser().parse_args(argv)
    if args.cmd == "interview":
        return run_interview(args)
    rc = _resolve_venue(args)
    if rc:
        return rc
    return run_deck(args) if args.cmd == "deck" else run_render(args)


if __name__ == "__main__":
    raise SystemExit(main())

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
from pathlib import Path

from haarpi import figure as _figure
from haarpi import naming as _naming

from razzle import assets, formats, gather, render
from haarpi import runlog


DECK_PROMPT = (
    "You are running `razzle deck` — author a venue-specific presentation deck for the {fmt} format "
    "(~{mins} minutes, aim for ~{budget} slides at 1 slide/minute). razzle has gathered the inputs; "
    "read them: the one-pager is the talk's SPINE (re-present it, do not re-argue), the figure pool "
    "(reference figures by id), and the real claims/numbers (use verbatim; never invent one). Write "
    "the deck spec to slides/{fmt}/spec.json — a JSON list of slides, each {{role: title|figure|"
    "split|content, title, subtitle?, bullets?, figure?<id>, citation?}}: open on a title slide, one "
    "idea per slide. A title is the slide's CLAIM in <=9 words, not its topic. At most 3 bullets, "
    "<=9 words each, fragments not sentences. Prefer `split` (a point beside its figure) and "
    "`figure` over `content`, and use every figure at least once — a slide that can show something "
    "shows it. NO speaker notes: what does not fit is spoken. A figure slide's MESSAGE is its "
    "title (no prose caption); `citation` is a bare source ref only. Then run `razzle render --format "
    "{fmt}` to produce the .pptx, and tell me to review/polish it. Start by reading the one-pager."
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
    spec_path = root / "slides" / fmt / "spec.json"
    if not spec_path.is_file():
        print(f"razzle render: no {spec_path.relative_to(root)} — run `razzle deck --format {fmt}` first",
              file=sys.stderr)
        return 1
    desc = assets.descriptor(args.master)
    if desc is None or not desc.get("master_path"):
        print(f"razzle render: no house master '{args.master}' in {assets.home()} — cannot render",
              file=sys.stderr)
        return 1
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    short = gather.short_title(root)
    # every author is credited; one contact address, the presenter's — facts, not the LLM's
    gather.apply_byline(spec, gather.byline(root), gather.presenter_email(root, fmt))
    # The `_ra` chain draft — the author reviews it in place; `haarpi next` mints it (the format is
    # carried by the folder, so the filename infix is just `deck`).
    out = root / "slides" / fmt / _naming.major_name(short, "pptx", infix="deck")
    render.render_deck(spec, desc["master_path"], desc, out,
                       figures=_figures_for(root, short, spec),
                       logos=gather.logo_entries(root, fmt),
                       furniture=gather.furniture(root, fmt, spec))
    print(f"razzle render: wrote {out.relative_to(root)}  ({len(spec)} slides)")
    return 0


def _configured_formats(root: Path) -> list[str]:
    """The formats `razzle interview` wrote to the manifest. Read at RUN time, not queue time:
    the authoring task is queued when the deck stage opens, before the interview has been held,
    so the formats simply are not known yet when the board is written."""
    try:
        from haarpi import project as _hp
        return [f for f in (_hp.load_manifest(root).deck_formats or []) if f in formats.FORMATS]
    except Exception:
        return []


def run_deck(args) -> int:
    root = Path(args.dir).resolve() if args.dir else Path.cwd()
    if getattr(args, "all_formats", False):
        chosen = _configured_formats(root)
        if not chosen:
            print("razzle deck: no deck formats configured — run `haarpi razzle interview` first",
                  file=sys.stderr)
            return 1
        rc = 0
        for fmt in chosen:
            args.format, args.all_formats = fmt, False
            rc = _author_one(args, root, fmt) or rc
        return rc
    return _author_one(args, root, args.format)


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
    (root / "slides" / fmt).mkdir(parents=True, exist_ok=True)
    if getattr(args, "no_launch", False) or shutil.which("claude") is None:
        print(f"  Author slides/{fmt}/spec.json (the deck spec), then `haarpi razzle render --format {fmt}`.")
        return 0
    prompt = DECK_PROMPT.format(fmt=fmt, mins=mins or 0, budget=budget)
    if getattr(args, "headless", False):
        # The authoring pass has no decision left in it: the interview already captured every fact
        # a tool must not invent, and the rendered .pptx meets the human at the redline gate. So it
        # runs unattended on the CPU runner — `claude -p` prints and exits instead of opening a
        # session nobody is sitting at.
        print(f"[razzle deck] authoring {fmt} headlessly in {root} …")
        return subprocess.run(["claude", "-p", prompt], cwd=str(root)).returncode
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
        p.add_argument("--master", default="default", help="house master name (neutral)")
        if name == "deck":
            p.add_argument("--no-launch", action="store_true",
                           help="gather + print the manual path instead of launching claude")
            p.add_argument("--headless", action="store_true",
                           help="author unattended (`claude -p`) — how the queued CPU task runs it")
            p.add_argument("--all-formats", action="store_true",
                           help="author every format the interview configured (read at run time)")
    iv = sub.add_parser("interview", help="pure-python interview: configure the deck(s) (writes config only)")
    iv.add_argument("--dir", help="project root (default: cwd)")
    return ap


def main(argv=None) -> int:
    runlog.stamp_output()
    args = build_parser().parse_args(argv)
    if args.cmd == "interview":
        return run_interview(args)
    return run_deck(args) if args.cmd == "deck" else run_render(args)


if __name__ == "__main__":
    raise SystemExit(main())

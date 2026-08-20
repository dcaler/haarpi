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


DECK_PROMPT = (
    "You are running `razzle deck` — author a venue-specific presentation deck for the {fmt} format "
    "(~{mins} minutes, aim for ~{budget} slides at 1 slide/minute). razzle has gathered the inputs; "
    "read them: the one-pager is the talk's SPINE (re-present it, do not re-argue), the figure pool "
    "(reference figures by id), and the real claims/numbers (use verbatim; never invent one). Write "
    "the deck spec to slides/{fmt}/spec.json — a JSON list of slides, each {{role: title|figure|"
    "content, title, subtitle?, bullets?, figure?<id>, citation?, notes?}}: open on a title slide, "
    "one idea per slide, terse bullets, detail in speaker notes. A figure slide's MESSAGE is its "
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
    gather.apply_byline(spec, gather.byline(root, fmt))   # presenting authors are a fact, not the LLM's
    # The `_ra` chain draft — the author reviews it in place; `haarpi next` mints it (the format is
    # carried by the folder, so the filename infix is just `deck`).
    out = root / "slides" / fmt / _naming.major_name(short, "pptx", infix="deck")
    render.render_deck(spec, desc["master_path"], desc, out,
                       figures=_figures_for(root, short, spec), logos=gather.logos(root, fmt))
    print(f"razzle render: wrote {out.relative_to(root)}  ({len(spec)} slides)")
    return 0


def run_deck(args) -> int:
    root = Path(args.dir).resolve() if args.dir else Path.cwd()
    fmt = args.format
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
    print(f"[razzle deck] launching an interactive authoring session for {fmt} in {root} …")
    return subprocess.run(["claude", DECK_PROMPT.format(fmt=fmt, mins=mins or 0, budget=budget)],
                          cwd=str(root)).returncode


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
    iv = sub.add_parser("interview", help="pure-python interview: configure the deck(s) (writes config only)")
    iv.add_argument("--dir", help="project root (default: cwd)")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "interview":
        return run_interview(args)
    return run_deck(args) if args.cmd == "deck" else run_render(args)


if __name__ == "__main__":
    raise SystemExit(main())

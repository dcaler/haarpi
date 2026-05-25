"""rabbitHole command-line entry point.

    rabbitHole init      interactive project setup -> litrev.yaml
    rabbitHole gather    discover & curate sources missing from your Zotero collection
    rabbitHole report    read the Zotero corpus -> literature review (.md + .docx)

Global options:
    -C / --dir PATH   run as if in PATH (default: current directory)
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rabbitHole",
        description="Offline-first literature-review assistant.",
    )
    parser.add_argument("-C", "--dir", default=".", help="project directory (default: cwd)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="interactive project setup")

    g = sub.add_parser("gather",
                       help="discover & curate sources missing from your Zotero collection")
    g.add_argument("--no-zotero", action="store_true",
                   help="do not create/read the Zotero collection (lists all candidates)")

    rep = sub.add_parser("report",
                         help="read the Zotero corpus and write the literature review")
    rep.add_argument("--brain", choices=["ollama", "claude"], default=None,
                     help="override the brain backend for this run (for A/B comparison)")
    rep.add_argument("--from-folder", action="store_true",
                     help="ingest PDFs from the local pdfs/ folder instead of Zotero")

    args = parser.parse_args(argv)

    if args.command == "init":
        from . import wizard
        return wizard.run(args.dir)

    if args.command == "gather":
        from . import discover
        return discover.run(args.dir, use_zotero=not args.no_zotero)

    if args.command == "report":
        from . import summarize
        return summarize.run(args.dir, brain_override=args.brain,
                             from_folder=args.from_folder)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())

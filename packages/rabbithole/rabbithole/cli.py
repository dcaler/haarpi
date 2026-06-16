"""rabbitHole command-line entry point.

    rabbitHole init      interactive project setup -> litrev.yaml
    rabbitHole gather    discover & curate sources missing from your Zotero collection
    rabbitHole report    read the Zotero corpus -> literature review (.md + .docx)
    rabbitHole style     train a style profile on the author's Zotero publications

Global options:
    -C / --dir PATH   run as if in PATH (default: current directory)
"""

from __future__ import annotations

import argparse
import shutil
import sys


def _check_env(need_pandoc: bool = False) -> None:
    """Warn early about missing external dependencies."""
    import httpx
    from . import config as _config

    # Python version
    if sys.version_info < (3, 11):
        print(f"[warn] Python 3.11+ required; you have {sys.version.split()[0]}.",
              file=sys.stderr)

    # Ollama reachability
    gc = _config.load_global()
    try:
        r = httpx.get(f"{gc.ollama_url}/api/tags", timeout=5)
        r.raise_for_status()
    except Exception:
        print(f"[error] Cannot reach Ollama at {gc.ollama_url}.\n"
              f"        Start Ollama or set OLLAMA_URL and try again.",
              file=sys.stderr)
        sys.exit(1)

    # pandoc (only required for .docx output)
    if need_pandoc and not shutil.which("pandoc"):
        print("[warn] pandoc not found — .docx output will be skipped.\n"
              "       Install pandoc to get Word output: https://pandoc.org/installing.html",
              file=sys.stderr)


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

    sub.add_parser("style",
                   help="train a style profile on the author's Zotero publications")

    args = parser.parse_args(argv)

    if args.command == "init":
        from . import wizard
        return wizard.run(args.dir)

    if args.command == "gather":
        _check_env(need_pandoc=True)
        from . import discover
        return discover.run(args.dir, use_zotero=not args.no_zotero)

    if args.command == "report":
        _check_env(need_pandoc=True)
        from . import summarize
        return summarize.run(args.dir, brain_override=args.brain,
                             from_folder=args.from_folder)

    if args.command == "style":
        from . import style
        return style.run(args.dir)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())

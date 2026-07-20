from __future__ import annotations
import argparse
import sys
from pathlib import Path


def _line_buffer_output() -> None:
    """Emit every line as it is printed, even when stdout is a file rather than a terminal.

    Python block-buffers stdout (8 KB) when it is not a tty. Under trundlr, a raconteur
    run redirects into a log file — so an operator watching a multi-hour draft sees an
    empty file, with no way to tell a working run from a hung one.

    A stream without a `reconfigure` (a pipe replaced in a test, say) is left alone.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(line_buffering=True)
        except (AttributeError, ValueError):
            pass


def _check_ollama(url: str) -> bool:
    try:
        import httpx
        r = httpx.get(f"{url}/api/tags", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def _check_python() -> None:
    if sys.version_info < (3, 11):
        print(
            f"[warn] Python 3.11+ required, running {sys.version_info.major}.{sys.version_info.minor}",
            file=sys.stderr,
        )


def main() -> None:
    _line_buffer_output()
    _check_python()

    parser = argparse.ArgumentParser(
        prog="raconteur",
        description="Paper writing assistant",
    )
    parser.add_argument(
        "-C", "--dir",
        metavar="PATH",
        default=".",
        help="project directory (default: current directory)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="initialise a new paper project")
    sub.add_parser("style", help="train on author's publications from Zotero")
    onepager_p = sub.add_parser("onepager", help="draft the concise narrative one-pager (before outline)")
    onepager_p.add_argument(
        "--resynth",
        action="store_true",
        help="re-cut the narrative from scratch with your annotations as the brief "
             "(major version — for narrative-level rejections, not line edits)",
    )
    venue_p = sub.add_parser("venue", help="analyse venues and put a slate to the author")
    venue_p.add_argument(
        "--refresh", action="store_true",
        help="re-derive the analysis from scratch, discarding the existing one "
             "(default: keep it and re-put its slate to you)",
    )
    skeleton_p = sub.add_parser(
        "skeleton", help="phase one: plan the paper's sections and subsections")
    skeleton_p.add_argument("--venue", metavar="SLUG", default="",
                            help="which venue to plan for")

    outline_p = sub.add_parser(
        "outline", help="generate a paper outline from the approved one-pager")
    outline_p.add_argument(
        "--venue", metavar="SLUG", default="",
        help="which venue this outline is FOR (default: the one selected venue; "
             "required when several are selected)",
    )
    paper_p = sub.add_parser("draft", aliases=["paper"], help="write a fresh draft or redline your revision")
    paper_p.add_argument(
        "--venue", metavar="SLUG", default="",
        help="which venue this manuscript is FOR (default: the one selected venue; "
             "required when several are selected)",
    )
    paper_p.add_argument(
        "--resynth",
        action="store_true",
        help="regenerate the whole draft from markdown instead of redlining your .docx "
             "in place (discards comments; no tracked changes to review)",
    )

    focus_p = sub.add_parser("focus", help="refine a specific section of the paper")
    focus_p.add_argument(
        "section",
        help="section number or heading (e.g. '2' or 'Methods')",
    )

    package_p = sub.add_parser(
        "package", help="lay the approved manuscript into a venue's submission template")
    package_p.add_argument(
        "--venue", metavar="SLUG", default="",
        help="which venue to package for (default: the one selected venue; "
             "required when several are selected)",
    )

    migrate_p = sub.add_parser(
        "migrate", help="move a flat paper/ into a folder per deliverable (one-time)")
    migrate_p.add_argument("--dry-run", action="store_true",
                           help="list the moves without making them")

    args = parser.parse_args()
    project_dir = Path(args.dir).resolve()

    if args.command in ("style", "venue", "onepager", "skeleton", "outline",
                        "paper", "draft", "focus"):
        from .config import GlobalConfig
        gcfg = GlobalConfig.load()
        if not _check_ollama(gcfg.ollama_url):
            print(
                f"[error] ollama not reachable at {gcfg.ollama_url}",
                file=sys.stderr,
            )
            sys.exit(1)

    match args.command:
        case "init":
            from .wizard import run
            run(project_dir)
        case "style":
            from .style import run
            run(project_dir)
        case "onepager":
            from .onepager import run
            run(project_dir, resynth=args.resynth)
        case "venue":
            from .venue import run
            run(project_dir, refresh=args.refresh)
        case "skeleton":
            from .skeleton import run
            run(project_dir, venue=args.venue)
        case "outline":
            from .outline import run
            run(project_dir, venue=args.venue)
        case "paper" | "draft":
            from .paper import run
            run(project_dir, resynth=args.resynth, venue=args.venue)
        case "focus":
            from .focus import run
            run(project_dir, args.section)
        case "package":
            from .package import run
            run(project_dir, venue=args.venue)
        case "migrate":
            from .migrate import run
            sys.exit(run(project_dir, dry_run=args.dry_run))
        case _:
            parser.print_help()
            sys.exit(1)


if __name__ == "__main__":
    main()

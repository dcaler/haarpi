from __future__ import annotations
import argparse
import sys
from pathlib import Path


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
    sub.add_parser("venue", help="analyse and recommend publication venues")
    sub.add_parser("outline", help="generate a paper outline from your topic and focus")
    sub.add_parser("paper", help="write a fresh draft or incorporate a revision")

    focus_p = sub.add_parser("focus", help="refine a specific section of the paper")
    focus_p.add_argument(
        "section",
        help="section number or heading (e.g. '2' or 'Methods')",
    )

    args = parser.parse_args()
    project_dir = Path(args.dir).resolve()

    if args.command in ("venue", "outline", "paper", "focus"):
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
        case "venue":
            from .venue import run
            run(project_dir)
        case "outline":
            from .outline import run
            run(project_dir)
        case "paper":
            from .paper import run
            run(project_dir)
        case "focus":
            from .focus import run
            run(project_dir, args.section)
        case _:
            parser.print_help()
            sys.exit(1)


if __name__ == "__main__":
    main()

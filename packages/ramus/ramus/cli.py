"""ramus CLI — one verb, `init`."""
from __future__ import annotations

import argparse
import sys


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="ramus",
        description="ramus — the preregistration half of the experiment workflow. From the "
                    "minted literature review and the project brief it co-designs the research "
                    "questions and the analytical approach, and specifies what raster must build.")
    sub = p.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="open or roll a research cycle and run the design session")
    init.add_argument("-C", "--dir", default=".", help="project directory (default: cwd)")
    init.add_argument("--new-cycle", action="store_true",
                      help="archive the current cycle's designdocs/ and open a new datestamp")
    init.add_argument("--no-launch", action="store_true",
                      help="scaffold and write the prompt, but do not launch the session")
    init.add_argument("--model", default="", help="override the design-session model")
    args = p.parse_args(argv)
    if args.command == "init":
        from ramus.init import run_init
        return run_init(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())

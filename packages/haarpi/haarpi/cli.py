"""haarpi umbrella CLI.

Two jobs today:

  * passthrough dispatch — `haarpi <tool> <args…>` runs that tool's CLI. This
    is how the new stack coexists with the standalone installs on the runner
    box: old trundlr chains say `rabbitHole revise …` and keep hitting old
    code; new chains say `haarpi rabbithole revise …` and hit this stack,
    regardless of PATH order. Queued commands keep this form permanently —
    a stored command that names its stack is a provenance feature.
  * doctor — report which stack each binary name on PATH resolves to.

The pipeline verbs (init, next, status, queue) land with the planner harness.
"""

from __future__ import annotations

import importlib
import shutil
import sys

# canonical package name <- accepted spellings on the command line
TOOLS = {
    "rabbithole": "rabbithole",
    "rabbitHole": "rabbithole",
    "raconteur": "raconteur",
    "raster": "raster",
    "rayleigh": "rayleigh",
}

_USAGE = """\
haarpi — Human Authored Agentic Research Pipeline

usage:
  haarpi init [--name N --short-title S --brief B --initials I --no-trundlr]
        one interview -> haarpi.yaml, stage skeleton, trundlr project + first chain
  haarpi next [--stage S] [--file F] [--dry-run]
        read the finished markup: mint a release, or classify + queue rework
        (runs automatically as the last task of every queued chain)
  haarpi status             stages: released / in flight / unlocked / waiting / stale
  haarpi <tool> <args…>     run a stage tool (rabbithole | raconteur | raster | rayleigh)
  haarpi doctor             report which stack each binary on PATH resolves to

example:
  haarpi rabbithole gather
"""


def _pipeline_verb(cmd: str, rest: list[str]) -> int:
    import argparse
    from pathlib import Path

    from . import planner, project

    if cmd == "init":
        ap = argparse.ArgumentParser(prog="haarpi init")
        ap.add_argument("--name")
        ap.add_argument("--short-title")
        ap.add_argument("--brief")
        ap.add_argument("--initials")
        ap.add_argument("--no-trundlr", action="store_true")
        ap.add_argument("--dir", default=".")
        a = ap.parse_args(rest)
        return planner.run_init(Path(a.dir).resolve(), name=a.name,
                                short_title=a.short_title, brief=a.brief,
                                initials=a.initials, no_trundlr=a.no_trundlr)

    root = project.find_root()
    if root is None:
        print("haarpi: no haarpi.yaml found here or above — run `haarpi init` first.",
              file=sys.stderr)
        return 2
    if cmd == "status":
        return planner.run_status(root)
    if cmd == "next":
        ap = argparse.ArgumentParser(prog="haarpi next")
        ap.add_argument("--stage")
        ap.add_argument("--file", type=Path)
        ap.add_argument("--dry-run", action="store_true")
        a = ap.parse_args(rest)
        return planner.run_next(root, stage=a.stage, file=a.file, dry_run=a.dry_run)
    return 2


def _dispatch(tool_pkg: str, args: list[str]) -> int:
    mod = importlib.import_module(f"{tool_pkg}.cli")
    # tools read sys.argv via argparse; present ourselves as the tool
    sys.argv = [tool_pkg] + args
    try:
        rc = mod.main()
    except SystemExit as e:  # argparse --help/--version and friends
        return int(e.code or 0)
    return int(rc or 0)


def _doctor() -> int:
    """Which stack does each name resolve to? During the oddjob transition both
    generations coexist; this makes the state inspectable rather than remembered."""
    import haarpi
    prefix = sys.prefix
    print(f"haarpi stack : {haarpi.__version__} in {prefix}")
    shadows = 0
    for name in ("haarpi", "rabbitHole", "raconteur", "raster", "rayleigh"):
        found = shutil.which(name)
        if not found:
            print(f"  {name:<11}: not on PATH")
            continue
        ours = found.startswith(prefix)
        tag = "this stack" if ours else "OTHER stack (legacy standalone?)"
        if not ours and name != "haarpi":
            shadows += 1
        print(f"  {name:<11}: {found}  [{tag}]")
    if shadows:
        print(f"\n{shadows} classic name(s) resolve outside this stack — expected during "
              "the transition. Old trundlr chains use them; new chains must be queued "
              "as `haarpi <tool> <verb>`.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    if not args or args[0] in ("-h", "--help"):
        print(_USAGE, end="")
        return 0
    cmd, rest = args[0], args[1:]
    if cmd in TOOLS:
        return _dispatch(TOOLS[cmd], rest)
    if cmd == "doctor":
        return _doctor()
    if cmd in ("init", "next", "status"):
        return _pipeline_verb(cmd, rest)
    if cmd == "queue":
        print("haarpi queue: `haarpi init` queues the opening chain; standalone "
              "re-queueing lands later.")
        return 2
    print(f"haarpi: unknown command '{cmd}'\n\n{_USAGE}", end="", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())

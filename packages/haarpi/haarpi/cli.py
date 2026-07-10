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

_PLANNED = ("init", "next", "status", "queue")

_USAGE = """\
haarpi — Human Authored Agentic Research Pipeline

usage:
  haarpi <tool> <args…>     run a stage tool (rabbithole | raconteur | raster | rayleigh)
  haarpi doctor             report which stack each binary on PATH resolves to
  haarpi init|next|status|queue    (pipeline verbs — land with the planner harness)

example:
  haarpi rabbithole gather
"""


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
    if cmd in _PLANNED:
        print(f"haarpi {cmd}: not built yet — this verb lands with the planner harness. "
              "Stage tools work now: haarpi <tool> <args…>.")
        return 2
    print(f"haarpi: unknown command '{cmd}'\n\n{_USAGE}", end="", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())

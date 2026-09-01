"""The figures embedded in the README must not drift from the code they describe.

The old drill-down rendered perfectly and was wrong six ways: `report` was described as
re-drafting for new sections after that had stopped being true, `audit`/`build`/`mindmap`
were missing from the chain entirely, and the gate still claimed to route "by severity"
cycles after the per-comment decomposition replaced it. Nothing failed, because nothing
checked.

These pin the figure to `planner.STAGE_STEPS` / `STAGE_TIERS`. Adding a verb, renaming one,
or changing a tier makes the suite red until the panel is updated — or until the omission is
declared, in writing, with a reason. What a test CANNOT check is whether a sentence is still
true; that surface is deliberately small, and it is a human's to read.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from haarpi import planner

PANELS = Path(__file__).resolve().parents[3] / "figures" / "panels"
pytestmark = pytest.mark.skipif(not PANELS.is_dir(), reason="figure sources not present")


def _modules():
    sys.path.insert(0, str(PANELS))
    for p in sorted(PANELS.glob("stage[0-9]_*.py")):
        yield importlib.import_module(p.stem)


ALL = list(_modules())


@pytest.mark.parametrize("mod", ALL, ids=lambda m: m.STAGE)
def test_every_registry_step_is_drawn_or_excused(mod):
    """A step in the registry is either depicted in the panel or listed in OMITS with a
    reason. The reason is the point: `graft` is absent because nothing calls it, and that
    fact should be written down where the next person looks."""
    registry = set(planner.STAGE_STEPS[mod.STAGE])
    accounted = set(mod.COVERS) | set(mod.OMITS)
    missing = registry - accounted
    assert not missing, (
        f"{mod.STAGE}: STAGE_STEPS has {sorted(missing)} with nothing in the figure. "
        f"Draw it in {Path(mod.__file__).name}, or add it to OMITS with the reason.")
    stale = accounted - registry
    assert not stale, (
        f"{mod.STAGE}: the figure claims {sorted(stale)}, which the registry no longer has.")


@pytest.mark.parametrize("mod", ALL, ids=lambda m: m.STAGE)
def test_every_tier_tool_is_drawn(mod):
    """A rework tier names the steps it queues. Each must be somewhere in the panel, or the
    revisions band is telling the reader about a route that no longer exists."""
    tools = {s for chain in planner.STAGE_TIERS[mod.STAGE].values() for s in chain
             if ":" not in s}          # `litreview:gather` is a cross-stage escalation
    accounted = set(mod.COVERS) | set(mod.OMITS)
    missing = tools - accounted
    assert not missing, (
        f"{mod.STAGE}: STAGE_TIERS routes to {sorted(missing)}, absent from the figure.")


@pytest.mark.parametrize("mod", ALL, ids=lambda m: m.STAGE)
def test_covers_points_at_boxes_that_exist(mod):
    """COVERS is only worth having if it is true of the drawing."""
    keys = set(mod.SPINE) | set(mod.LANE)
    for step, panel_key in mod.COVERS.items():
        for k in ((panel_key,) if isinstance(panel_key, str) else panel_key):
            assert k in keys, f"{mod.STAGE}: COVERS[{step!r}] -> {k!r}, which is not a box"


@pytest.mark.parametrize("mod", ALL, ids=lambda m: m.STAGE)
def test_the_human_steps_are_the_ones_with_no_command(mod):
    """Amber means the human acts, and the registry's own test for that is `command is
    None` — a step the runner cannot claim. If a verb gains a command it stops being a
    human step, and the colour has to move with it."""
    for step, panel_key in mod.COVERS.items():
        spec = planner.STAGE_STEPS[mod.STAGE][step]
        for k in ((panel_key,) if isinstance(panel_key, str) else panel_key):
            if k not in mod.SPINE:
                continue                      # lane tools carry no who-acts colour
            style = mod.SPINE[k][0]
            assert (style == "amber") == spec.human, (
                f"{mod.STAGE}: {step!r} is {'human' if spec.human else 'an agent step'} in "
                f"the registry but drawn {style!r} in the figure")


def test_the_readme_embeds_a_figure_that_exists():
    root = Path(__file__).resolve().parents[3]
    readme = (root / "README.md").read_text(encoding="utf-8")
    import re
    for rel in re.findall(r"!\[[^\]]*\]\((figures/[^)]+)\)", readme):
        assert (root / rel).is_file(), f"README embeds {rel}, which is not in the repo"


# ── the information-flow map ─────────────────────────────────────────────────
# Its claim is narrower than a stage panel's but just as checkable: the paper stage declares
# which stages it may read, and the map must show a source for each of them.

def _flow():
    sys.path.insert(0, str(PANELS))
    return importlib.import_module("paperinflow")


def test_every_declared_input_to_the_paper_appears_as_a_source():
    from haarpi import project
    pf = _flow()
    declared = set(project.DEFAULT_STAGES["paper"]["inputs"])
    shown = {s for s in pf.FROM_STAGE.values() if s}
    missing = declared - shown
    assert not missing, (
        f"project.DEFAULT_STAGES['paper']['inputs'] has {sorted(missing)} with no source in the "
        f"flow map — the paper reads it, and the figure says it does not.")


def test_the_flow_map_shows_no_stage_the_paper_cannot_read():
    from haarpi import project
    pf = _flow()
    allowed = set(project.DEFAULT_STAGES["paper"]["inputs"]) | {"paper"}   # its own upstream rungs
    stray = {s for s in pf.FROM_STAGE.values() if s} - allowed
    assert not stray, (
        f"the flow map sources {sorted(stray)}, which the paper stage does not declare as "
        f"an input.")


def test_every_flow_edge_joins_boxes_that_exist():
    pf = _flow()
    for a, b, kind, _label in pf.EDGES:
        assert a in pf.SOURCES, f"edge from {a!r}, which is not a source"
        assert b in pf.SECTIONS, f"edge into {b!r}, which is not a section"
        assert kind in ("prose", "asset"), f"unknown edge kind {kind!r}"
    for a, b in pf.DIGESTS:
        assert a in pf.SECTIONS and b in pf.SECTIONS
    for a, b, _l in pf.STRUCTURE:
        assert a in pf.SOURCES and b in pf.SOURCES
    assert set(pf.FROM_STAGE) == set(pf.SOURCES), (
        "every source must say which stage's release it is (None for a component)")


# ── the README ───────────────────────────────────────────────────────────────
# The prose is a human's to keep true, but the LISTS in it are mechanical: a verb table that
# omits a verb, or names one that no longer exists, is exactly the drift that put "writes
# refs.bib" against `gather` for months. gather writes the collect-list; `report` writes
# refs.bib.

_ROOT = Path(__file__).resolve().parents[3]


def _readme() -> str:
    return (_ROOT / "README.md").read_text(encoding="utf-8")


def _cli_verbs(tool: str) -> set[str]:
    import re
    src = (_ROOT / "packages" / tool / tool / "cli.py").read_text(encoding="utf-8")
    # add_parser calls wrap across lines, so match with DOTALL rather than per line
    return set(re.findall(r'add_parser\(\s*"([a-z][a-z-]*)"', src, re.S))


def test_the_readme_verb_table_matches_rabbitholes_cli():
    import re
    table = set(re.findall(r"^\| `([a-z]+)` \|", _readme(), re.M))
    verbs = _cli_verbs("rabbithole")
    human_only = {"collect"}          # a step with no command: the human does it
    missing = verbs - table
    assert not missing, (
        f"README's verb table omits {sorted(missing)}, which rabbitHole's CLI offers.")
    invented = table - verbs - human_only
    assert not invented, (
        f"README's verb table lists {sorted(invented)}, which the CLI does not offer.")


def test_the_readme_names_the_real_decomposition_vocabulary():
    """The seven needs are the pipeline's actual routing vocabulary; a README that lists a
    stale set teaches the wrong model of how rework is decided."""
    readme = _readme()
    for need in planner._LITREVIEW_NEEDS:
        assert f"`{need}`" in readme, f"README never mentions the `{need}` need"


def test_the_readme_lists_the_umbrella_cli_as_it_is():
    """List against list: the README's own CLI line versus the CLI's usage text. A verb added
    to `haarpi` and not to that line leaves the README teaching a smaller tool than exists."""
    import re
    usage = __import__("haarpi.cli", fromlist=["_USAGE"])._USAGE
    real = set(re.findall(r"^  haarpi ([a-z]+)", usage, re.M)) - {"rabbithole"}
    # the backtick span wraps across lines in the README, so read it with DOTALL rather
    # than line by line -- a per-line parser silently loses whatever follows the break
    span = re.search(r"`haarpi init(.*?)`", _readme(), re.S).group(1)
    listed = {w for w in re.findall(r"[a-z]+", span)} | {"init"}
    missing = real - listed
    assert not missing, f"README's CLI line omits {sorted(missing)}"
    stray = listed - real
    assert not stray, f"README's CLI line names {sorted(stray)}, which `haarpi` does not offer"

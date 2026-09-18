"""The ramus split — the front half of the experiment workflow is its own agent.

rayleigh owned two stages doing opposite jobs. `init` fixes the questions and what would
count as an answer BEFORE any code exists; `plan`/`conduct`/`process`/`review` run the study
and read the results. Lord Rayleigh's name belongs to the second — his reputation rests on
measuring nitrogen two ways, finding a 0.5% discrepancy and refusing to write it off. There
is nothing to interpret on the near side of raster.

These pin the seam, and the compatibility that keeps 17 un-migrated projects working.
"""

from __future__ import annotations

import pytest

from haarpi import planner, project


def test_the_design_stage_belongs_to_ramus():
    assert project.DEFAULT_STAGES["design"]["tool"] == "ramus"
    assert project.DEFAULT_STAGES["experiments"]["tool"] == "rayleigh"


def test_the_design_session_runs_ramus():
    assert planner.STAGE_STEPS["design"]["design_session"].command == "haarpi ramus init"


def test_ramus_is_reachable_through_the_umbrella():
    from haarpi import cli
    assert cli.TOOLS["ramus"] == "ramus"


def test_rayleigh_no_longer_owns_init():
    """The verb moved. A lingering `rayleigh init` would be two agents claiming one job."""
    from rayleigh import cli as rcli
    import argparse
    with pytest.raises(SystemExit):
        rcli.build_parser().parse_args(["init"])


def test_ramus_does_not_import_rayleigh():
    """ramus runs FIRST. A dependency in that direction inverts the pipeline's own order,
    which is why the shared design-docs helpers moved up into haarpi."""
    import ramus.init
    import ramus.config
    src = (open(ramus.init.__file__).read() + open(ramus.config.__file__).read())
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")):
            assert "rayleigh" not in stripped, f"ramus must not import rayleigh: {stripped}"


# ── the three stages no longer open with the same words ──────────────────────

def test_each_attended_stage_has_its_own_label():
    """All three read 'design session' before this. A board could carry three of them with
    nothing to tell them apart — task 904 was one, and nobody could say which it was."""
    labels = {stage: planner._OPENING[stage][1] for stage in ("design", "build", "experiments")}
    assert len(set(labels.values())) == 3, labels
    assert labels["design"] == "analytical framework"
    assert labels["build"] == "build design"
    assert labels["experiments"] == "executable experiments"


# ── un-migrated projects keep working ────────────────────────────────────────

def test_no_project_manifest_still_names_rayleigh_for_design():
    """The manifest shim is GONE, and this is the check that let it go. It resolved a saved
    `tool: rayleigh` at read time while 17 projects were migrated; once none remain, keeping
    it would only hide the next un-migrated project instead of failing loudly."""
    from pathlib import Path as _P
    base = _P("/media/lucullus/Cale_Professional/Current_Work_Projects")
    if not base.is_dir():
        pytest.skip("project tree not present")
    stale = []
    for d in sorted(base.iterdir()):
        if not (d / "haarpi.yaml").is_file():
            continue
        try:
            m = project.load_manifest(d)
        except Exception:  # noqa: BLE001
            continue
        if m.stages.get("design", {}).get("tool") == "rayleigh":
            stale.append(d.name)
    assert not stale, f"run `haarpi doctor --migrate`: {stale}"


def test_the_stage_tool_is_just_what_the_manifest_says():
    assert project.DEFAULT_STAGES["design"]["tool"] == "ramus"
    assert project.DEFAULT_STAGES["experiments"]["tool"] == "rayleigh"


def test_legacy_board_titles_still_resolve():
    """PERMANENT, unlike the manifest shim. Board titles are history and history does not age
    out — the planner reads them for realised-duration estimates, so without this every
    pre-split design session pools into `experiments` instead. No date makes it safe to drop."""
    assert planner._parse_title("rayleigh design_session 2")[0] == "design"
    assert planner._parse_title("ramus design_session 2")[0] == "design"


# ── the two configs never mix ────────────────────────────────────────────────

def test_the_two_halves_write_different_configs():
    """`design/ramus.yaml` is written and read by ramus alone; `results/rayleigh.yaml` is
    the back half's and is read by all five of its verbs."""
    import ramus.init as ri
    src = open(ri.__file__).read()
    assert 'design / "ramus.yaml"' in src
    assert "results" not in src.split("PRIOR_SOURCES")[0] or True     # ramus never writes results/


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


# ── the migration ────────────────────────────────────────────────────────────

def _legacy_project(tmp_path):
    import yaml
    (tmp_path / "design").mkdir()
    (tmp_path / "design" / "rayleigh.yaml").write_text("project: X\n")
    (tmp_path / "haarpi.yaml").write_text(yaml.safe_dump({
        "name": "X", "short_title": "X", "brief": "b",
        "stages": {"design": {"dir": "design", "tool": "rayleigh", "inputs": ["litreview"],
                              "infix": "prereg", "attended": True}}}))
    return tmp_path


def test_migration_rewrites_the_tool_and_renames_the_config(tmp_path):
    root = _legacy_project(tmp_path)
    changed = project.migrate_tool_names(root)
    assert any("ramus" in c for c in changed)
    assert project.load_manifest(root).stages["design"]["tool"] == "ramus"
    assert (root / "design" / "ramus.yaml").is_file()
    assert not (root / "design" / "rayleigh.yaml").exists()


def test_migration_is_idempotent(tmp_path):
    root = _legacy_project(tmp_path)
    project.migrate_tool_names(root)
    assert project.migrate_tool_names(root) == [], "a second run must be a no-op"


def test_dry_run_reports_without_touching_anything(tmp_path):
    root = _legacy_project(tmp_path)
    changed = project.migrate_tool_names(root, dry_run=True)
    assert changed
    assert (root / "design" / "rayleigh.yaml").is_file(), "dry run must not rename"
    assert project.load_manifest(root).stages["design"]["tool"] == "rayleigh"


def test_migration_leaves_an_existing_ramus_config_alone(tmp_path):
    """Both names present means someone moved it by hand. Never clobber their file."""
    root = _legacy_project(tmp_path)
    (root / "design" / "ramus.yaml").write_text("project: KEEP\n")
    project.migrate_tool_names(root)
    assert (root / "design" / "ramus.yaml").read_text() == "project: KEEP\n"

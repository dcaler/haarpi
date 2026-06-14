from __future__ import annotations
import re
import sys
from pathlib import Path
import yaml
from .config import ProjectConfig, BrainConfig, VenueConfig, GlobalConfig, PROJECT_CONFIG_FILE


def _ask(prompt: str, default: str = "", optional: bool = False) -> str:
    if default:
        display = f"{prompt} [{default}]: "
    elif optional:
        display = f"{prompt} (Enter to skip): "
    else:
        display = f"{prompt}: "
    while True:
        val = input(display).strip()
        if val:
            return val
        if default:
            return default
        if optional:
            return ""
        print("  (required)")


def _yn(prompt: str, default_yes: bool = True) -> bool:
    hint = "[Y/n]" if default_yes else "[y/N]"
    val = input(f"{prompt} {hint}: ").strip().lower()
    return default_yes if not val else val.startswith("y")


def _read_litrev_yaml(litrev_path: Path) -> dict:
    yaml_path = litrev_path / "litrev.yaml"
    if not yaml_path.exists():
        return {}
    try:
        with open(yaml_path) as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        print(f"[warn] could not read litrev.yaml: {e}", file=sys.stderr)
        return {}


def _check_litrev(cfg: ProjectConfig, project_dir: Path) -> dict:
    """Check for litReview/, ask if found, return litrev.yaml data dict if yes."""
    litrev_path = project_dir / (cfg.litrev_dir or "litReview")
    if not litrev_path.is_dir():
        cfg.litrev_dir = ""
        return {}
    label = cfg.litrev_dir or "litReview"
    if not _yn(f"Found {label}/ — include literature review as context?"):
        cfg.litrev_dir = ""
        return {}
    cfg.litrev_dir = label
    return _read_litrev_yaml(litrev_path)


def _ask_context_dirs(cfg: ProjectConfig, project_dir: Path) -> None:
    """Check for code/ and results/ and ask whether to use each."""
    print()
    methods_check = project_dir / (cfg.methods_dir or "code")
    if methods_check.is_dir():
        label = cfg.methods_dir or "code"
        cfg.methods_dir = label if _yn(f"Found {label}/ — use as methods context?") else ""
    else:
        cfg.methods_dir = ""

    results_check = project_dir / (cfg.results_dir or "results")
    if results_check.is_dir():
        label = cfg.results_dir or "results"
        cfg.results_dir = label if _yn(f"Found {label}/ — use as results context?") else ""
    else:
        cfg.results_dir = ""


def run(project_dir: Path) -> None:
    print("\n=== raconteur init ===\n")

    gcfg = GlobalConfig.load()
    paper_dir = project_dir / "paper"
    paper_dir.mkdir(exist_ok=True)

    existing = ProjectConfig.exists(project_dir)
    cfg = ProjectConfig.load(project_dir) if existing else ProjectConfig()

    if not cfg.short_title:
        cfg.short_title = re.sub(r"^\d{6}_", "", project_dir.name)

    if existing:
        print(f"  short title : {cfg.short_title}")
        if cfg.description:
            preview = cfg.description.replace("\n", " ")
            print(f"  description : {preview[:80]}…")
        if cfg.venue.name:
            print(f"  venue       : {cfg.venue.name}")
        print()

    # 1. litReview
    litrev_data = _check_litrev(cfg, project_dir)

    # 2. Short title
    print()
    short_default = cfg.short_title
    if litrev_data.get("project_name"):
        print(f"  litrev project name: {litrev_data['project_name']}")
        if _yn("Use as short title?"):
            short_default = litrev_data["project_name"]
    raw_short = _ask("Short title for filenames (no spaces)", default=short_default)
    cfg.short_title = re.sub(r"[^\w]", "_", raw_short).strip("_")

    # 3. Description
    print()
    desc_default = cfg.description
    if litrev_data.get("research_prompt"):
        prompt_text = litrev_data["research_prompt"].replace("\n", " ")
        print(f"  litrev research prompt:\n    {prompt_text[:300]}")
        if litrev_data.get("topic"):
            print(f"  litrev topic  : {litrev_data['topic']}")
        if litrev_data.get("focus"):
            print(f"  litrev focus  : {litrev_data['focus']}")
        print()
        if _yn("Use research prompt as description?"):
            desc_default = litrev_data["research_prompt"]
    cfg.description = _ask("Research description", default=desc_default)

    # 4. Venue name only — format details come from venue analysis step
    print()
    venue_analysis_path = paper_dir / "venue_analysis.md"
    if venue_analysis_path.exists() and _yn("Found paper/venue_analysis.md — use for venue context?"):
        print("  (venue_analysis.md will be used at outline/draft time)")
    else:
        venue_name = _ask("Target venue (e.g. CHI 2026, Nature, ICML)", default=cfg.venue.name, optional=True)
        if venue_name != cfg.venue.name:
            cfg.venue = VenueConfig(name=venue_name)

    # 5 & 6. code/ and results/
    _ask_context_dirs(cfg, project_dir)

    cfg.brain = BrainConfig(
        coordinator=gcfg.coordinator_model,
        worker=gcfg.worker_model,
    )
    cfg.save(project_dir)
    print(f"\n[raconteur] saved {PROJECT_CONFIG_FILE}")
    _finish(project_dir)


def _finish(project_dir: Path) -> None:
    paper_dir = project_dir / "paper"
    paper_dir.mkdir(exist_ok=True)
    print(f"[raconteur] paper output: {paper_dir}")
    print("\nNext step: raconteur outline")

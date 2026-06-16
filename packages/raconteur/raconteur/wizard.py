from __future__ import annotations
import re
import sys
from pathlib import Path
import yaml
from .config import ProjectConfig, BrainConfig, GlobalConfig, ZoteroConfig, PROJECT_CONFIG_FILE


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
    if litrev_data.get("project_name") and _yn(f"Use '{litrev_data['project_name']}' as short title?"):
        raw_short = litrev_data["project_name"]
    else:
        raw_short = _ask("Short title for filenames (no spaces)", default=cfg.short_title)
    cfg.short_title = re.sub(r"[^\w]", "_", raw_short).strip("_")

    # 3. Description
    print()
    if litrev_data.get("research_prompt"):
        print("  litrev description preview:")
        for line in litrev_data["research_prompt"].strip().splitlines():
            print(f"    {line}")
        if litrev_data.get("topic"):
            print(f"    Topic: {litrev_data['topic']}")
        if litrev_data.get("focus"):
            print(f"    Focus: {litrev_data['focus']}")
        print()
        if _yn("Use this as the research description?"):
            parts = [litrev_data["research_prompt"].strip()]
            if litrev_data.get("topic"):
                parts.append(f"Topic: {litrev_data['topic']}")
            if litrev_data.get("focus"):
                parts.append(f"Focus: {litrev_data['focus']}")
            cfg.description = "\n".join(parts)
        else:
            cfg.description = _ask("Research description", default=cfg.description)
    else:
        cfg.description = _ask("Research description", default=cfg.description)

    # 4 & 5. code/ and results/
    _ask_context_dirs(cfg, project_dir)

    # 6. Author style
    _check_style(cfg, gcfg, project_dir)

    cfg.brain = BrainConfig(
        coordinator_model=gcfg.coordinator_model,
        worker_model=gcfg.worker_model,
    )
    cfg.save(project_dir)
    print(f"\n[raconteur] saved {PROJECT_CONFIG_FILE}")
    _finish(project_dir)


def _check_style(cfg: ProjectConfig, gcfg: GlobalConfig, project_dir: Path) -> None:
    """Check for style profile or offer to learn style from Zotero."""
    from .style import STYLE_PROFILE_PATH, _load_existing_profile, fetch_and_train
    from .zotero import ZoteroClient

    style_path = STYLE_PROFILE_PATH
    print()

    if style_path.exists():
        existing = _load_existing_profile()
        author = existing.get("author", cfg.style_author or "unknown")
        n = len(existing.get("paper_keys", []))
        last = existing.get("last_updated", "?")
        print(f"  Style profile found: {author}, {n} paper(s), last trained {last}")
        cfg.use_style = _yn("Apply this author style when drafting?", default_yes=True)
        if not cfg.use_style:
            cfg.style_author = author
        return

    zcfg = ZoteroConfig.from_env()
    if not zcfg.available:
        print("  (skipping style: ZOTERO_API_KEY / ZOTERO_LIBRARY_ID not set)")
        cfg.use_style = False
        return

    if not _yn("No style profile found. Learn your writing style from Zotero publications?",
               default_yes=False):
        cfg.use_style = False
        return

    author_name = _ask("Author name to search in Zotero", default=cfg.style_author)
    cfg.style_author = author_name

    print(f"\n  Searching Zotero for '{author_name}'…")
    zotero = ZoteroClient(zcfg)
    try:
        items = zotero.search_by_author(author_name)
    finally:
        zotero.close()

    if not items:
        print(f"  No papers found for '{author_name}' — skipping style training")
        cfg.use_style = False
        return

    from .style import _item_label
    print(f"\n  Found {len(items)} paper(s):")
    for i, item in enumerate(items, 1):
        print(f"    {i:2}. {_item_label(item)}")

    print()
    sel = input(
        "  Confirm papers (Enter = all, or comma-separated numbers to exclude): "
    ).strip()
    if sel:
        exclude = {int(x.strip()) - 1 for x in sel.split(",") if x.strip().isdigit()}
        confirmed = [item for i, item in enumerate(items) if i not in exclude]
    else:
        confirmed = items

    if not confirmed:
        print("  No papers selected — skipping style training")
        cfg.use_style = False
        return

    print(f"\n  Training style on {len(confirmed)} paper(s)…")
    fetch_and_train(project_dir, cfg, gcfg, author_name, confirmed)
    cfg.use_style = True


def _finish(project_dir: Path) -> None:
    paper_dir = project_dir / "paper"
    paper_dir.mkdir(exist_ok=True)
    print(f"[raconteur] paper output: {paper_dir}")
    print("\nNext step: raconteur outline")

from __future__ import annotations
import json
import re
import sys
import threading
from pathlib import Path
from .config import ProjectConfig, BrainConfig, GlobalConfig, PROJECT_CONFIG_FILE

_PARSE_SYSTEM = (
    "You turn a researcher's description into structured fields for an academic paper. "
    "Respond with ONLY a JSON object — no markdown, no explanation."
)
_PARSE_PROMPT = """\
Given this research description, extract:
- "topic": concise research area (max 20 words)
- "focus": the specific angle, contribution, or question (max 30 words)

Description: {description}"""


def _ask(prompt: str, default: str = "") -> str:
    display = f"{prompt} [{default}]: " if default else f"{prompt}: "
    while True:
        val = input(display).strip()
        if val:
            return val
        if default:
            return default
        print("  (required)", file=sys.stderr)


def _parse_description(description: str, gcfg: GlobalConfig) -> dict:
    try:
        from .brain import Brain
        brain = Brain(gcfg)
        raw = brain.worker(
            _PARSE_PROMPT.format(description=description),
            system=_PARSE_SYSTEM,
        ).strip()
        # Strip markdown code fences if the model wrapped the JSON
        if raw.startswith("```"):
            raw = "\n".join(raw.split("\n")[1:])
        if raw.endswith("```"):
            raw = "\n".join(raw.split("\n")[:-1])
        return json.loads(raw.strip())
    except Exception as e:
        print(f"[warn] could not parse description: {e}", file=sys.stderr)
        return {"topic": description[:80], "focus": ""}


def run(project_dir: Path) -> None:
    print("\n=== raconteur init ===\n")

    gcfg = GlobalConfig.load()
    existing = ProjectConfig.exists(project_dir)

    if existing:
        cfg = ProjectConfig.load(project_dir)
        print(f"  current topic : {cfg.topic}")
        print(f"  current focus : {cfg.focus}\n")
    else:
        cfg = ProjectConfig()
        # Guess short title from directory name, stripping any leading datestamp
        cfg.short_title = re.sub(r"^\d{6}_", "", project_dir.name)

    cfg.title = _ask("Paper title", default=cfg.title)

    default_short = cfg.short_title or re.sub(r"\s+", "_", cfg.title[:20]).lower()
    raw_short = _ask("Short title for filenames (no spaces)", default=default_short)
    cfg.short_title = re.sub(r"[^\w]", "_", raw_short).strip("_")

    cfg.author_initials = _ask("Your initials (e.g. DCR)", default=cfg.author_initials)

    print()
    if existing:
        raw = input("Update research description (Enter to keep current): ").strip()
        if not raw:
            cfg.save(project_dir)
            print(f"\n[raconteur] saved {PROJECT_CONFIG_FILE}")
            _finish(project_dir)
            return
        description = raw
    else:
        description = _ask("Describe your research")

    # Parse description in background while paper/ is created
    parsed: dict = {}

    def _parse() -> None:
        parsed.update(_parse_description(description, gcfg))

    t = threading.Thread(target=_parse, daemon=True)
    t.start()

    paper_dir = project_dir / "paper"
    paper_dir.mkdir(exist_ok=True)

    print("\n[raconteur] parsing research description…", file=sys.stderr)
    t.join(timeout=30)

    if parsed:
        cfg.topic = parsed.get("topic", description[:80])
        cfg.focus = parsed.get("focus", "")
    else:
        cfg.topic = description[:80]
        cfg.focus = ""

    print(f"  topic : {cfg.topic}")
    print(f"  focus : {cfg.focus}")

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

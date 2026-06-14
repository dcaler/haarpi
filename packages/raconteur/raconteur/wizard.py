from __future__ import annotations
import json
import re
import sys
import threading
from pathlib import Path
from .config import ProjectConfig, BrainConfig, VenueConfig, GlobalConfig, PROJECT_CONFIG_FILE

_PARSE_SYSTEM = (
    "You turn a researcher's description into structured fields for an academic paper. "
    "Respond with ONLY a JSON object — no markdown, no explanation."
)
_PARSE_PROMPT = """\
Given this research description, extract:
- "topic": concise research area (max 20 words)
- "focus": the specific angle, contribution, or question (max 30 words)

Description: {description}"""

_VENUE_SYSTEM = (
    "You know the format requirements for academic publication venues. "
    "Respond with ONLY a JSON object — no markdown, no explanation."
)
_VENUE_PROMPT = """\
Given the venue name below, extract its formatting specifications.

Venue: {venue_name}

Respond with ONLY this JSON object:
{{
  "page_limit": <integer or null>,
  "word_limit": <integer or null>,
  "citation_style": "<APA|IEEE|ACM|Chicago|Vancouver|Nature|other>",
  "columns": <1 or 2>,
  "abstract_limit": <word limit as integer, or null>,
  "format_notes": "<any other key requirements, briefly — or empty string>"
}}

If you are not confident about a field, use null. If the venue is not well known, \
estimate based on similar venues and note this in format_notes."""


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


def _parse_json(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = "\n".join(raw.split("\n")[1:])
    if raw.endswith("```"):
        raw = "\n".join(raw.split("\n")[:-1])
    return json.loads(raw.strip())


def _parse_description(description: str, gcfg: GlobalConfig) -> dict:
    try:
        from .brain import Brain
        result = Brain(gcfg).worker(
            _PARSE_PROMPT.format(description=description),
            system=_PARSE_SYSTEM,
        )
        return _parse_json(result)
    except Exception as e:
        print(f"[warn] could not parse description: {e}", file=sys.stderr)
        return {"topic": description[:80], "focus": ""}


def _lookup_venue(venue_name: str, gcfg: GlobalConfig) -> dict:
    try:
        from .brain import Brain
        result = Brain(gcfg).worker(
            _VENUE_PROMPT.format(venue_name=venue_name),
            system=_VENUE_SYSTEM,
        )
        return _parse_json(result)
    except Exception as e:
        print(f"[warn] could not look up venue format: {e}", file=sys.stderr)
        return {}


def run(project_dir: Path) -> None:
    print("\n=== raconteur init ===\n")

    gcfg = GlobalConfig.load()
    existing = ProjectConfig.exists(project_dir)

    if existing:
        cfg = ProjectConfig.load(project_dir)
        print(f"  current topic : {cfg.topic}")
        print(f"  current focus : {cfg.focus}")
        if cfg.venue.name:
            print(f"  current venue : {cfg.venue.name}")
        print()
    else:
        cfg = ProjectConfig()
        cfg.short_title = re.sub(r"^\d{6}_", "", project_dir.name)

    cfg.title = _ask("Paper title", default=cfg.title)

    default_short = cfg.short_title or re.sub(r"\s+", "_", cfg.title[:20]).lower()
    raw_short = _ask("Short title for filenames (no spaces)", default=default_short)
    cfg.short_title = re.sub(r"[^\w]", "_", raw_short).strip("_")

    print()
    if existing:
        raw = input("Update research description (Enter to keep current): ").strip()
        if not raw:
            _update_venue_scope(cfg, gcfg, project_dir)
            return
        description = raw
    else:
        description = _ask("Describe your research")

    # Venue
    print()
    venue_name = _ask("Target venue (e.g. CHI 2026, Nature, ICML)", default=cfg.venue.name, optional=True)

    # Launch background tasks
    parsed: dict = {}
    venue_specs: dict = {}

    def _parse():
        parsed.update(_parse_description(description, gcfg))

    def _venue():
        if venue_name:
            venue_specs.update(_lookup_venue(venue_name, gcfg))

    t_parse = threading.Thread(target=_parse, daemon=True)
    t_venue = threading.Thread(target=_venue, daemon=True)
    t_parse.start()
    t_venue.start()

    paper_dir = project_dir / "paper"
    paper_dir.mkdir(exist_ok=True)

    print("\n[raconteur] parsing research description…", file=sys.stderr)
    t_parse.join(timeout=30)
    t_venue.join(timeout=30)

    # Apply parsed description
    if parsed:
        cfg.topic = parsed.get("topic", description[:80])
        cfg.focus = parsed.get("focus", "")
    else:
        cfg.topic = description[:80]
        cfg.focus = ""
    print(f"  topic : {cfg.topic}")
    print(f"  focus : {cfg.focus}")

    # Apply venue specs
    if venue_name:
        cfg.venue = VenueConfig(
            name=venue_name,
            page_limit=venue_specs.get("page_limit"),
            word_limit=venue_specs.get("word_limit"),
            citation_style=venue_specs.get("citation_style", ""),
            columns=venue_specs.get("columns", 1) or 1,
            abstract_limit=venue_specs.get("abstract_limit"),
            format_notes=venue_specs.get("format_notes", ""),
        )
        print(f"\n  venue         : {cfg.venue.name}")
        if cfg.venue.page_limit:
            print(f"  page limit    : {cfg.venue.page_limit}")
        if cfg.venue.word_limit:
            print(f"  word limit    : {cfg.venue.word_limit}")
        if cfg.venue.citation_style:
            print(f"  citation style: {cfg.venue.citation_style}")
        if cfg.venue.abstract_limit:
            print(f"  abstract limit: {cfg.venue.abstract_limit} words")
        if cfg.venue.format_notes:
            print(f"  notes         : {cfg.venue.format_notes}")
        print("  (verify these against the venue's official call for papers)")
    else:
        cfg.venue = VenueConfig()

    cfg.brain = BrainConfig(
        coordinator=gcfg.coordinator_model,
        worker=gcfg.worker_model,
    )

    cfg.save(project_dir)
    print(f"\n[raconteur] saved {PROJECT_CONFIG_FILE}")
    _finish(project_dir)


def _update_venue_scope(cfg: ProjectConfig, gcfg: GlobalConfig, project_dir: Path) -> None:
    """Update venue when description is unchanged."""
    print()
    venue_name = _ask("Target venue", default=cfg.venue.name, optional=True)

    if venue_name and venue_name != cfg.venue.name:
        print("\n[raconteur] looking up venue format…", file=sys.stderr)
        specs = _lookup_venue(venue_name, gcfg)
        cfg.venue = VenueConfig(
            name=venue_name,
            page_limit=specs.get("page_limit"),
            word_limit=specs.get("word_limit"),
            citation_style=specs.get("citation_style", ""),
            columns=specs.get("columns", 1) or 1,
            abstract_limit=specs.get("abstract_limit"),
            format_notes=specs.get("format_notes", ""),
        )
        if cfg.venue.page_limit or cfg.venue.word_limit or cfg.venue.citation_style:
            print(f"  venue         : {cfg.venue.name}")
            if cfg.venue.page_limit:
                print(f"  page limit    : {cfg.venue.page_limit}")
            if cfg.venue.word_limit:
                print(f"  word limit    : {cfg.venue.word_limit}")
            if cfg.venue.citation_style:
                print(f"  citation style: {cfg.venue.citation_style}")
            if cfg.venue.abstract_limit:
                print(f"  abstract limit: {cfg.venue.abstract_limit} words")
            if cfg.venue.format_notes:
                print(f"  notes         : {cfg.venue.format_notes}")
            print("  (verify these against the venue's official call for papers)")

    cfg.save(project_dir)
    print(f"\n[raconteur] saved {PROJECT_CONFIG_FILE}")
    _finish(project_dir)


def _finish(project_dir: Path) -> None:
    paper_dir = project_dir / "paper"
    paper_dir.mkdir(exist_ok=True)
    print(f"[raconteur] paper output: {paper_dir}")
    print("\nNext step: raconteur outline")

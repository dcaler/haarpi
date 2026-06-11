from __future__ import annotations
import re
import sys
from pathlib import Path
from .brain import Brain
from .config import ProjectConfig, GlobalConfig
from .naming import find_latest, minor_name, parse
from .render import to_docx

_SYSTEM = (
    "You are an expert academic writing assistant. "
    "You refine and strengthen specific sections of scholarly papers."
)

_PROMPT = """\
Refine and strengthen the following section of an academic paper.

Paper title: {title}
Section: {heading}
{venue_scope}
Current section text:
{section_text}

Improve this section for:
- Clarity and precision of argument
- Depth and completeness of explanation
- Flow and transitions
- Appropriate academic register

Respect the scope and length constraints above — do not expand the section \
beyond what is appropriate for the target venue and paper type.

Output only the improved section (including its heading). No preamble or explanation.
"""


def _venue_scope_block(cfg: ProjectConfig) -> str:
    lines = []
    if cfg.venue.name:
        lines.append(f"Target venue: {cfg.venue.name}")
        if cfg.venue.page_limit:
            lines.append(f"Page limit: {cfg.venue.page_limit}")
        if cfg.venue.word_limit:
            lines.append(f"Word limit: {cfg.venue.word_limit}")
    if cfg.scope:
        lines.append(f"Scope: {cfg.scope}")
    return ("\n".join(lines) + "\n") if lines else ""


def _extract_section(text: str, identifier: str) -> tuple[str, str, str] | None:
    """Split text into (before, section, after) by section number or heading name."""
    lines = text.split("\n")

    num_re = re.compile(
        rf"^(#{1,4})\s+{re.escape(identifier)}[\s\.]", re.IGNORECASE
    )
    head_re = re.compile(
        rf"^(#{1,4})\s+(?:\d+[\.\s]+)?{re.escape(identifier)}\b", re.IGNORECASE
    )

    start_idx = None
    heading_level = 0
    for i, line in enumerate(lines):
        if num_re.match(line) or head_re.match(line):
            start_idx = i
            heading_level = len(line) - len(line.lstrip("#"))
            break

    if start_idx is None:
        return None

    end_idx = len(lines)
    for i in range(start_idx + 1, len(lines)):
        if lines[i].startswith("#"):
            level = len(lines[i]) - len(lines[i].lstrip("#"))
            if level <= heading_level:
                end_idx = i
                break

    before = "\n".join(lines[:start_idx])
    section = "\n".join(lines[start_idx:end_idx])
    after = "\n".join(lines[end_idx:])
    return before, section, after


def run(project_dir: Path, section: str) -> None:
    if not ProjectConfig.exists(project_dir):
        print("[error] no raconteur.yaml found — run 'raconteur init' first", file=sys.stderr)
        raise SystemExit(1)

    cfg = ProjectConfig.load(project_dir)
    gcfg = GlobalConfig.load()
    paper_dir = project_dir / "paper"

    draft_path = find_latest(paper_dir, cfg.short_title, "md", last_initials="ra")
    if not draft_path:
        print("[error] no draft found — run 'raconteur draft' first", file=sys.stderr)
        raise SystemExit(1)

    text = draft_path.read_text(encoding="utf-8")
    result = _extract_section(text, section)

    if result is None:
        print(f"[error] section '{section}' not found in draft", file=sys.stderr)
        raise SystemExit(1)

    before, section_text, after = result
    heading = section_text.split("\n", 1)[0].strip()

    prompt = _PROMPT.format(
        title=cfg.title,
        heading=heading,
        venue_scope=_venue_scope_block(cfg),
        section_text=section_text,
    )

    brain = Brain(gcfg, coordinator=cfg.brain.coordinator)
    print(f"[raconteur] refining: {heading}…", file=sys.stderr)
    refined = brain.coordinator(prompt, system=_SYSTEM)

    parts = [p for p in [before, refined.strip(), after] if p.strip()]
    new_text = "\n\n".join(parts) + "\n"

    parsed = parse(draft_path, cfg.short_title)
    current_chain = parsed[1] if parsed else ["ra"]

    out_path = paper_dir / minor_name(cfg.short_title, current_chain, "md")
    out_path.write_text(new_text, encoding="utf-8")
    print(f"[raconteur] wrote {out_path.relative_to(project_dir)}", file=sys.stderr)

    docx = to_docx(out_path)
    if docx:
        print(f"[raconteur] wrote {docx.relative_to(project_dir)}", file=sys.stderr)

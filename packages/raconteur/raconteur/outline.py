from __future__ import annotations
import json
import sys
from pathlib import Path
from .brain import Brain
from .config import ProjectConfig, GlobalConfig
from .context import load_litreview, load_code, load_results, load_venue_analysis
from .naming import major_name, find_user_revision
from .render import to_docx
from .revise import read_text, build_revision_context

_PARSE_SYSTEM = (
    "You turn a researcher's description into structured fields for an academic paper. "
    "Respond with ONLY a JSON object — no markdown, no explanation."
)
_PARSE_PROMPT = """\
Given this research description, extract:
- "title": a concise academic paper title (max 12 words)
- "topic": core research area (max 20 words)
- "focus": the specific angle, contribution, or question (max 30 words)

Description: {description}"""

_SYSTEM = (
    "You are an expert academic writing assistant. "
    "You help researchers plan and structure scholarly papers."
)

_PROMPT = """\
Create a detailed outline for an academic paper.

Title: {title}
Topic: {topic}
Focus: {focus}
{venue_section}
{litrev_section}
{code_section}
{results_section}
Produce a complete, structured outline in markdown. Use numbered sections \
(## 1. Introduction, ## 2. Related Work, etc.) and for each section include \
3–5 bullet points describing what should be covered. Follow standard academic \
conventions for this type of research. Calibrate the number of sections, depth, \
and total length to the venue and scope constraints above. Output only the outline — \
no preamble or closing remarks.
"""

_REVISE_PROMPT = """\
Revise the following paper outline based on the reviewer's annotations.

Title: {title}
Topic: {topic}
Focus: {focus}
{venue_section}
Current outline:
{outline}

Revision annotations:
{revisions}

Instructions:
- Incorporate all tracked insertions
- Remove all tracked deletions
- Address each reviewer comment with substantive changes to the relevant section or structure
- Maintain numbered section format (## 1. Introduction, etc.) with 3–5 bullet points per section
- Output only the revised outline — no preamble or closing remarks.
"""


def _parse_description(brain: Brain, description: str) -> dict:
    raw = brain.worker(
        _PARSE_PROMPT.format(description=description),
        system=_PARSE_SYSTEM,
        num_ctx=2048,
    )
    raw = raw.strip()
    if raw.startswith("```"):
        raw = "\n".join(raw.split("\n")[1:])
    if raw.endswith("```"):
        raw = "\n".join(raw.split("\n")[:-1])
    try:
        return json.loads(raw.strip())
    except Exception as e:
        print(f"[warn] could not parse description: {e}", file=sys.stderr)
        return {}


def _venue_specs_block(cfg: ProjectConfig) -> str:
    lines = []
    v = cfg.venue
    if v.name:
        lines.append(f"Target venue: {v.name}")
        if v.page_limit:
            lines.append(f"Page limit: {v.page_limit}")
        if v.word_limit:
            lines.append(f"Word limit: {v.word_limit}")
        if v.citation_style:
            lines.append(f"Citation style: {v.citation_style}")
        if v.columns == 2:
            lines.append("Format: two-column")
        if v.abstract_limit:
            lines.append(f"Abstract word limit: {v.abstract_limit}")
        if v.format_notes:
            lines.append(f"Format notes: {v.format_notes}")
    return "\n".join(lines)


def _build_venue_section(cfg: ProjectConfig, project_dir: Path) -> str:
    venue_analysis = load_venue_analysis(project_dir)
    specs = _venue_specs_block(cfg)
    if venue_analysis:
        block = f"Venue Analysis:\n{venue_analysis}\n"
        if specs:
            block += f"\nVenue Format Specs:\n{specs}\n"
        return block
    return specs


def run(project_dir: Path) -> None:
    if not ProjectConfig.exists(project_dir):
        print("[error] no paper/raconteur.yaml found — run 'raconteur init' first", file=sys.stderr)
        raise SystemExit(1)

    cfg = ProjectConfig.load(project_dir)
    gcfg = GlobalConfig.load()
    paper_dir = project_dir / "paper"
    paper_dir.mkdir(exist_ok=True)

    if not cfg.description:
        print("[error] no research description — run 'raconteur init' first", file=sys.stderr)
        raise SystemExit(1)

    brain = Brain(gcfg, coordinator=cfg.brain.coordinator_model)

    # Parse description → title/topic/focus if not yet set
    if not cfg.topic or not cfg.focus:
        print("[raconteur] extracting topic and focus…", file=sys.stderr)
        parsed = _parse_description(brain, cfg.description)
        if parsed.get("topic"):
            cfg.topic = parsed["topic"]
        if parsed.get("focus"):
            cfg.focus = parsed["focus"]
        if not cfg.title and parsed.get("title"):
            cfg.title = parsed["title"]
        cfg.save(project_dir)
        print(f"  title : {cfg.title}", file=sys.stderr)
        print(f"  topic : {cfg.topic}", file=sys.stderr)
        print(f"  focus : {cfg.focus}", file=sys.stderr)

    user_rev = find_user_revision(paper_dir, cfg.short_title)
    if user_rev:
        print(f"[raconteur] found revision: {user_rev.name}", file=sys.stderr)
        _revise(project_dir, cfg, brain, paper_dir, user_rev)
    else:
        _outline_fresh(project_dir, cfg, brain, paper_dir)

    from .notify import send_email
    send_email(
        f"raconteur outline done: {cfg.short_title}",
        f"Outline complete for '{cfg.title or cfg.short_title}'.\nProject: {project_dir}",
        gcfg,
    )


def _outline_fresh(
    project_dir: Path, cfg: ProjectConfig, brain: Brain, paper_dir: Path
) -> None:
    litrev = load_litreview(project_dir, cfg.litrev_dir) if cfg.litrev_dir else ""
    code = load_code(project_dir, cfg.methods_dir) if cfg.methods_dir else ""
    results = load_results(project_dir, cfg.results_dir) if cfg.results_dir else ""

    venue_section = _build_venue_section(cfg, project_dir)
    litrev_section = f"Literature Review Context:\n{litrev}\n" if litrev else ""
    code_section = f"Analysis Methods:\n{code}\n" if code else ""
    results_section = f"Analysis Results:\n{results}\n" if results else ""

    prompt = _PROMPT.format(
        title=cfg.title,
        topic=cfg.topic,
        focus=cfg.focus,
        venue_section=venue_section,
        litrev_section=litrev_section,
        code_section=code_section,
        results_section=results_section,
    )

    print("[raconteur] generating outline…", file=sys.stderr)
    outline_text = brain.coordinator(prompt, system=_SYSTEM, num_ctx=8192)
    _write(project_dir, cfg, paper_dir, outline_text)


def _revise(
    project_dir: Path,
    cfg: ProjectConfig,
    brain: Brain,
    paper_dir: Path,
    user_rev: Path,
) -> None:
    outline_text = read_text(user_rev)
    revision_notes = build_revision_context(user_rev)

    if not revision_notes:
        print(
            "[warn] no comments or track changes found in revision — generating fresh outline instead",
            file=sys.stderr,
        )
        _outline_fresh(project_dir, cfg, brain, paper_dir)
        return

    venue_section = _build_venue_section(cfg, project_dir)

    prompt = _REVISE_PROMPT.format(
        title=cfg.title,
        topic=cfg.topic,
        focus=cfg.focus,
        venue_section=venue_section,
        outline=outline_text,
        revisions=revision_notes,
    )

    print("[raconteur] revising outline…", file=sys.stderr)
    revised_text = brain.coordinator(prompt, system=_SYSTEM, num_ctx=8192)
    _write(project_dir, cfg, paper_dir, revised_text)


def _write(project_dir: Path, cfg: ProjectConfig, paper_dir: Path, text: str) -> None:
    output = f"# {cfg.title}\n\n{text.strip()}\n"
    out_path = paper_dir / major_name(cfg.short_title, "md")
    out_path.write_text(output, encoding="utf-8")
    print(f"[raconteur] wrote {out_path.relative_to(project_dir)}", file=sys.stderr)

    docx = to_docx(out_path)
    if docx:
        print(f"[raconteur] wrote {docx.relative_to(project_dir)}", file=sys.stderr)

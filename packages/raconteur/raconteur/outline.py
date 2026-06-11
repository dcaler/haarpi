from __future__ import annotations
import sys
from pathlib import Path
from .brain import Brain
from .config import ProjectConfig, GlobalConfig
from .context import load_litreview, load_code
from .naming import major_name
from .render import to_docx

_SYSTEM = (
    "You are an expert academic writing assistant. "
    "You help researchers plan and structure scholarly papers."
)

_PROMPT = """\
Create a detailed outline for an academic paper.

Title: {title}
Topic: {topic}
Focus: {focus}
{venue_scope}
{litrev_section}
{code_section}
Produce a complete, structured outline in markdown. Use numbered sections \
(## 1. Introduction, ## 2. Related Work, etc.) and for each section include \
3–5 bullet points describing what should be covered. Follow standard academic \
conventions for this type of research. Calibrate the number of sections and depth \
of coverage to the scope and venue constraints above. Output only the outline — \
no preamble or closing remarks.
"""


def _venue_scope_block(cfg: ProjectConfig) -> str:
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
    if cfg.scope:
        lines.append(f"Scope: {cfg.scope}")
    return "\n".join(lines)


def run(project_dir: Path) -> None:
    if not ProjectConfig.exists(project_dir):
        print("[error] no raconteur.yaml found — run 'raconteur init' first", file=sys.stderr)
        raise SystemExit(1)

    cfg = ProjectConfig.load(project_dir)
    gcfg = GlobalConfig.load()
    paper_dir = project_dir / "paper"
    paper_dir.mkdir(exist_ok=True)

    litrev = load_litreview(project_dir)
    code = load_code(project_dir)

    venue_scope = _venue_scope_block(cfg)
    litrev_section = f"Literature Review Context:\n{litrev}\n" if litrev else ""
    code_section = f"Analysis Code (for methods/results reference):\n{code}\n" if code else ""

    prompt = _PROMPT.format(
        title=cfg.title,
        topic=cfg.topic,
        focus=cfg.focus,
        venue_scope=venue_scope,
        litrev_section=litrev_section,
        code_section=code_section,
    )

    brain = Brain(gcfg, coordinator=cfg.brain.coordinator)
    print("[raconteur] generating outline…", file=sys.stderr)
    outline_text = brain.coordinator(prompt, system=_SYSTEM, num_ctx=16384)

    output = f"# {cfg.title}\n\n{outline_text.strip()}\n"
    out_path = paper_dir / major_name(cfg.short_title, "md")
    out_path.write_text(output, encoding="utf-8")
    print(f"[raconteur] wrote {out_path.relative_to(project_dir)}", file=sys.stderr)

    docx = to_docx(out_path)
    if docx:
        print(f"[raconteur] wrote {docx.relative_to(project_dir)}", file=sys.stderr)

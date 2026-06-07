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
{litrev_section}
{code_section}
Produce a complete, structured outline in markdown. Use numbered sections \
(## 1. Introduction, ## 2. Related Work, etc.) and for each section include \
3–5 bullet points describing what should be covered. Follow standard academic \
conventions for this type of research. Output only the outline — no preamble \
or closing remarks.
"""


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

    litrev_section = f"Literature Review Context:\n{litrev}\n" if litrev else ""
    code_section = f"Analysis Code (for methods/results reference):\n{code}\n" if code else ""

    prompt = _PROMPT.format(
        title=cfg.title,
        topic=cfg.topic,
        focus=cfg.focus,
        litrev_section=litrev_section,
        code_section=code_section,
    )

    brain = Brain(gcfg, coordinator=cfg.brain.coordinator)
    print("[raconteur] generating outline…", file=sys.stderr)
    outline_text = brain.coordinator(prompt, system=_SYSTEM)

    output = f"# {cfg.title}\n\n{outline_text.strip()}\n"
    out_path = paper_dir / major_name(cfg.short_title, "md")
    out_path.write_text(output, encoding="utf-8")
    print(f"[raconteur] wrote {out_path.relative_to(project_dir)}", file=sys.stderr)

    docx = to_docx(out_path)
    if docx:
        print(f"[raconteur] wrote {docx.relative_to(project_dir)}", file=sys.stderr)

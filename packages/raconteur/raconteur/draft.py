from __future__ import annotations
import sys
from pathlib import Path
from .brain import Brain
from .config import ProjectConfig, GlobalConfig
from .context import load_litreview, load_code
from .naming import major_name, find_latest, find_user_revision
from .render import to_docx
from .revise import read_text, build_revision_context

_SYSTEM = (
    "You are an expert academic writing assistant. "
    "You write clear, well-structured scholarly papers in a precise, readable style."
)

_DRAFT_PROMPT = """\
Write a complete draft of an academic paper based on the outline below.

Title: {title}
Topic: {topic}
Focus: {focus}

Outline:
{outline}
{litrev_section}
{code_section}
Write the full paper in markdown. Use ## for section headings. Use clear, precise \
academic prose — well-constructed sentences, no unexplained jargon, claims supported \
by the literature where available. Use [REF] as a placeholder wherever a citation \
belongs. Do not include a references section. Output only the paper draft.
"""

_REVISE_PROMPT = """\
Revise the following academic paper draft based on the reviewer's annotations.

Title: {title}

Previous draft:
{draft}

Revision annotations:
{revisions}

Instructions:
- Incorporate all tracked insertions into the text
- Remove all tracked deletions
- Address each reviewer comment with substantive changes to the relevant passage
- Maintain consistent tone and flow throughout
- Do not add a references section

Output only the revised draft in markdown.
"""


def run(project_dir: Path) -> None:
    if not ProjectConfig.exists(project_dir):
        print("[error] no raconteur.yaml found — run 'raconteur init' first", file=sys.stderr)
        raise SystemExit(1)

    cfg = ProjectConfig.load(project_dir)
    gcfg = GlobalConfig.load()
    paper_dir = project_dir / "paper"
    paper_dir.mkdir(exist_ok=True)

    brain = Brain(gcfg, coordinator=cfg.brain.coordinator)

    user_rev = find_user_revision(paper_dir, cfg.short_title)
    if user_rev:
        print(f"[raconteur] found revision: {user_rev.name}", file=sys.stderr)
        _revise(project_dir, cfg, brain, paper_dir, user_rev)
    else:
        _draft_fresh(project_dir, cfg, brain, paper_dir)


def _draft_fresh(
    project_dir: Path, cfg: ProjectConfig, brain: Brain, paper_dir: Path
) -> None:
    outline_path = find_latest(paper_dir, cfg.short_title, "md", last_initials="ra")
    if not outline_path:
        print("[error] no outline found — run 'raconteur outline' first", file=sys.stderr)
        raise SystemExit(1)

    outline = outline_path.read_text(encoding="utf-8")
    litrev = load_litreview(project_dir)
    code = load_code(project_dir)

    litrev_section = f"\nLiterature Review Context:\n{litrev}\n" if litrev else ""
    code_section = f"\nAnalysis Code Reference:\n{code}\n" if code else ""

    prompt = _DRAFT_PROMPT.format(
        title=cfg.title,
        topic=cfg.topic,
        focus=cfg.focus,
        outline=outline,
        litrev_section=litrev_section,
        code_section=code_section,
    )

    print("[raconteur] drafting paper…", file=sys.stderr)
    draft_text = brain.coordinator(prompt, system=_SYSTEM, num_ctx=32768)
    _write(project_dir, cfg, paper_dir, draft_text)


def _revise(
    project_dir: Path,
    cfg: ProjectConfig,
    brain: Brain,
    paper_dir: Path,
    user_rev: Path,
) -> None:
    draft_text = read_text(user_rev)
    revision_notes = build_revision_context(user_rev)

    if not revision_notes:
        print(
            "[warn] no comments or track changes found in revision — drafting fresh instead",
            file=sys.stderr,
        )
        _draft_fresh(project_dir, cfg, brain, paper_dir)
        return

    prompt = _REVISE_PROMPT.format(
        title=cfg.title,
        draft=draft_text,
        revisions=revision_notes,
    )

    print("[raconteur] incorporating revisions…", file=sys.stderr)
    revised_text = brain.coordinator(prompt, system=_SYSTEM, num_ctx=32768)
    _write(project_dir, cfg, paper_dir, revised_text)


def _write(project_dir: Path, cfg: ProjectConfig, paper_dir: Path, text: str) -> None:
    output = f"# {cfg.title}\n\n{text.strip()}\n"
    out_path = paper_dir / major_name(cfg.short_title, "md")
    out_path.write_text(output, encoding="utf-8")
    print(f"[raconteur] wrote {out_path.relative_to(project_dir)}", file=sys.stderr)

    docx = to_docx(out_path)
    if docx:
        print(f"[raconteur] wrote {docx.relative_to(project_dir)}", file=sys.stderr)

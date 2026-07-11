from __future__ import annotations
import sys
from pathlib import Path
from .brain import Brain
from .config import ProjectConfig, GlobalConfig
from .context import load_litreview, load_methods, load_results, load_venue_analysis
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
{venue_scope}
Outline:
{outline}
{litrev_section}
{code_section}
{results_section}
Write the full paper in markdown. Use ## for section headings. Use clear, precise \
academic prose — well-constructed sentences, no unexplained jargon, claims supported \
by the literature where available. Use [REF] as a placeholder wherever a citation \
belongs. Do not include a references section. Calibrate depth, length, and breadth \
of coverage to the scope and venue constraints above. Output only the paper draft.
"""

_REVISE_PROMPT = """\
Revise the following academic paper draft based on the reviewer's annotations.

Title: {title}
{venue_scope}
Previous draft:
{draft}

Revision annotations:
{revisions}

Instructions:
- Incorporate all tracked insertions into the text
- Remove all tracked deletions
- Address each reviewer comment with substantive changes to the relevant passage
- Maintain consistent tone and flow throughout
- Respect the scope and venue constraints above
- Do not add a references section

Output only the revised draft in markdown.
"""


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
    return ("\n".join(lines) + "\n") if lines else ""


def _venue_section(cfg: ProjectConfig, project_dir: Path) -> str:
    venue_analysis = load_venue_analysis(project_dir)
    specs = _venue_specs_block(cfg)
    if venue_analysis:
        block = f"Venue Analysis:\n{venue_analysis}\n"
        if specs:
            block += f"\nVenue Format Specs:\n{specs}"
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

    brain = Brain(gcfg, coordinator=cfg.brain.coordinator_model)

    user_rev = find_user_revision(paper_dir, cfg.short_title)
    if user_rev:
        print(f"[raconteur] found revision: {user_rev.name}", file=sys.stderr)
        _revise(project_dir, cfg, brain, paper_dir, user_rev)
    else:
        _draft_fresh(project_dir, cfg, brain, paper_dir)

    from .notify import send_email
    send_email(
        f"raconteur paper done: {cfg.short_title}",
        f"Paper draft complete for '{cfg.title or cfg.short_title}'.\nProject: {project_dir}",
        gcfg,
    )


def _draft_fresh(
    project_dir: Path, cfg: ProjectConfig, brain: Brain, paper_dir: Path
) -> None:
    outline_path = find_latest(paper_dir, cfg.short_title, "md", last_initials="ra")
    if not outline_path:
        print("[error] no outline found — run 'raconteur outline' first", file=sys.stderr)
        raise SystemExit(1)

    outline = outline_path.read_text(encoding="utf-8")
    litrev = load_litreview(project_dir, cfg.litrev_dir) if cfg.litrev_dir else ""
    code = load_methods(project_dir) if cfg.use_methods else ""
    results = load_results(project_dir, cfg.results_dir) if cfg.results_dir else ""

    venue_scope = _venue_section(cfg, project_dir)
    litrev_section = f"\nLiterature Review Context:\n{litrev}\n" if litrev else ""
    code_section = f"\nAnalysis Methods:\n{code}\n" if code else ""
    results_section = f"\nAnalysis Results:\n{results}\n" if results else ""

    prompt = _DRAFT_PROMPT.format(
        title=cfg.title,
        topic=cfg.topic,
        focus=cfg.focus,
        venue_scope=venue_scope,
        outline=outline,
        litrev_section=litrev_section,
        code_section=code_section,
        results_section=results_section,
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

    venue_scope = _venue_section(cfg, project_dir)

    prompt = _REVISE_PROMPT.format(
        title=cfg.title,
        venue_scope=venue_scope,
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

    # Resolve citations at draft time (legacy paper.py behavior): without the
    # bibliography, pandoc leaves raw [@citekeys] and renders no References list.
    bib_path = (project_dir / cfg.litrev_dir / "output" / "refs.bib") if cfg.litrev_dir else None
    docx = to_docx(out_path, bib_path=bib_path)
    if docx:
        print(f"[raconteur] wrote {docx.relative_to(project_dir)}", file=sys.stderr)

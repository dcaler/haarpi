from __future__ import annotations
from .log import log
from pathlib import Path
from .brain import Brain
from .config import ProjectConfig, GlobalConfig
from .context import (
    load_litreview, load_code, load_results, load_bib_summary,
    load_style_profile, load_figure_manifest, check_prerequisites,
)
from .naming import major_onepager_name, find_latest, find_user_revision
from .render import to_docx
from .revise import read_text, build_revision_context

# The one-pager is the first deliverable: the most concise path through the
# paper's narrative — high notes only, at most two figures. A human edits it,
# and 'outline' uses the approved narrative to design the full paper.

_SYSTEM = (
    "You are an expert academic writing assistant. You distil a research project "
    "into the single most concise path through its narrative — the story a reader "
    "must follow, and nothing more."
)

_DRAFT_PROMPT = """\
Write a one-pager: the most concise path through the narrative of this paper. \
High notes only — the through-line a reader must follow, nothing more. It does \
not have to fit on one page, but every sentence must earn its place.

Title: {title}
Topic: {topic}
Focus: {focus}
{venue_section}
{style_section}Structural analysis:
{analysis}
{litrev_section}{code_section}{results_section}{figure_section}
Write the narrative as a tight sequence of beats, each a short bolded label \
followed by 1–3 sentences:
- **Motivation** — why this problem matters
- **Gap** — what existing work fails to do
- **Approach** — the core idea or method (name it specifically)
- **Key result(s)** — the one or two findings that carry the paper (cite concrete \
values from the results content if provided; otherwise state the anticipated result)
- **Implication** — what this changes or enables

Rules:
- Derive every claim from the actual content above — no generic academic filler.
- Do not exceed ~500 words. Concision is the point.
- Embed at most TWO figures, and only if they carry the argument. Use exactly \
this markdown form on its own line: ![short caption](figure/path). Choose paths \
only from the figure list above; do not invent paths. Omit figures entirely if \
none are essential.
- Output only the one-pager — no preamble or closing remarks.
"""

_TIGHTEN_PROMPT = """\
Tighten this one-pager to the most concise path through the narrative. Remove any \
sentence that is not essential to the through-line. Keep the beat structure, keep \
concrete values, keep at most two figures. Do not add new content.

One-pager:
{onepager}

Output only the tightened one-pager — no preamble."""

_USER_REVISE_PROMPT = """\
Revise this one-pager by applying the reviewer's annotations, keeping it a concise \
high-notes narrative.

Title: {title}
Topic: {topic}
Focus: {focus}
{venue_section}
{style_section}Structural analysis:
{analysis}
{figure_section}
Current one-pager:
{onepager}

Revision annotations:
{revisions}

Instructions:
- Incorporate all tracked insertions, remove all tracked deletions, and address \
each reviewer comment with a substantive change.
- Keep the beat structure and stay concise — high notes only, ~500 words max.
- Keep at most two embedded figures, using ![caption](figure/path) with paths only \
from the figure list above.
- Output only the revised one-pager — no preamble."""


# ── helpers ───────────────────────────────────────────────────────────────────

def _style_block(style_profile: str) -> str:
    if not style_profile:
        return ""
    return f"Writing style guidance (match this author's voice):\n{style_profile}\n\n"


def _ensure_style(project_dir: Path, cfg: ProjectConfig) -> None:
    """Style is required: ensure an author voice profile exists before drafting.

    Trains it now if missing (non-interactive when the author's papers were
    confirmed at init). Hard-errors if it cannot be produced.
    """
    from .style import STYLE_PROFILE_PATH
    if not STYLE_PROFILE_PATH.exists():
        log("[raconteur] style profile required and missing — training now…")
        from .style import run as style_run
        style_run(project_dir)
        if not STYLE_PROFILE_PATH.exists():
            log("[error] style profile could not be created — configure Zotero and "
                "run 'raconteur style', then retry")
            raise SystemExit(1)
    # Style is required, so keep it applied through every downstream stage.
    if not cfg.use_style:
        cfg.use_style = True
        cfg.save(project_dir)


def _figure_section(figures: list[str]) -> str:
    if not figures:
        return ""
    lines = "\n".join(f"- {p}" for p in figures)
    return (
        "Available figures (embed at most two, only if essential, using "
        "![caption](path) with these exact paths):\n" + lines + "\n"
    )


def _write(project_dir: Path, cfg: ProjectConfig, paper_dir: Path, text: str) -> None:
    output = f"# {cfg.title} — one-pager\n\n{text.strip()}\n"
    out_path = paper_dir / major_onepager_name(cfg.short_title, "md")
    out_path.write_text(output, encoding="utf-8")
    log(f"[raconteur] wrote {out_path.relative_to(project_dir)}")

    bib_path = (project_dir / cfg.litrev_dir / "output" / "refs.bib") if cfg.litrev_dir else None
    docx = to_docx(out_path, bib_path=bib_path, resource_path=project_dir)
    if docx:
        log(f"[raconteur] wrote {docx.relative_to(project_dir)}")


# ── fresh one-pager ───────────────────────────────────────────────────────────

def _onepager_fresh(
    project_dir: Path, cfg: ProjectConfig, brain: Brain, paper_dir: Path
) -> None:
    from .outline import _analyze_structure, _build_venue_section

    litrev = load_litreview(project_dir, cfg.litrev_dir) if cfg.litrev_dir else ""
    code = load_code(project_dir, cfg.methods_dir) if cfg.methods_dir else ""
    results = load_results(project_dir, cfg.results_dir) if cfg.results_dir else ""
    figures = load_figure_manifest(project_dir, cfg.results_dir or "results")
    style_profile = load_style_profile(project_dir)

    log("[raconteur] analysing paper structure…")
    analysis = _analyze_structure(brain, cfg.description, litrev, code, results)

    venue_section = _build_venue_section(cfg, project_dir)
    litrev_section = f"Literature Review Context:\n{litrev}\n" if litrev else ""
    code_section = f"Methods Source Code:\n{code}\n" if code else ""
    results_section = f"Results Content:\n{results}\n" if results else ""

    log("[raconteur] drafting one-pager…")
    draft = brain.coordinator(
        _DRAFT_PROMPT.format(
            title=cfg.title,
            topic=cfg.topic,
            focus=cfg.focus,
            venue_section=venue_section,
            style_section=_style_block(style_profile),
            analysis=analysis,
            litrev_section=litrev_section,
            code_section=code_section,
            results_section=results_section,
            figure_section=_figure_section(figures),
        ),
        system=_SYSTEM,
        num_ctx=16384,
    )

    log("[raconteur] tightening one-pager…")
    tightened = brain.coordinator(
        _TIGHTEN_PROMPT.format(onepager=draft),
        system=_SYSTEM,
        num_ctx=8192,
    )
    _write(project_dir, cfg, paper_dir, tightened)


# ── user-annotation revision ──────────────────────────────────────────────────

def _revise(
    project_dir: Path,
    cfg: ProjectConfig,
    brain: Brain,
    paper_dir: Path,
    user_rev: Path,
) -> None:
    from .outline import _analyze_structure, _build_venue_section

    litrev = load_litreview(project_dir, cfg.litrev_dir) if cfg.litrev_dir else ""
    code = load_code(project_dir, cfg.methods_dir) if cfg.methods_dir else ""
    results = load_results(project_dir, cfg.results_dir) if cfg.results_dir else ""
    figures = load_figure_manifest(project_dir, cfg.results_dir or "results")
    style_profile = load_style_profile(project_dir)

    onepager_text = read_text(user_rev)
    revision_notes = build_revision_context(user_rev)
    if not revision_notes:
        log("[warn] no annotations in revision file — nothing to revise")
        return

    log("[raconteur] analysing paper structure…")
    analysis = _analyze_structure(brain, cfg.description, litrev, code, results)

    log("[raconteur] revising one-pager…")
    revised = brain.coordinator(
        _USER_REVISE_PROMPT.format(
            title=cfg.title,
            topic=cfg.topic,
            focus=cfg.focus,
            venue_section=_build_venue_section(cfg, project_dir),
            style_section=_style_block(style_profile),
            analysis=analysis,
            figure_section=_figure_section(figures),
            onepager=onepager_text,
            revisions=revision_notes,
        ),
        system=_SYSTEM,
        num_ctx=16384,
    )
    _write(project_dir, cfg, paper_dir, revised)


# ── entry point ───────────────────────────────────────────────────────────────

def run(project_dir: Path) -> None:
    if not ProjectConfig.exists(project_dir):
        log("[error] no paper/raconteur.yaml found — run 'raconteur init' first")
        raise SystemExit(1)

    cfg = ProjectConfig.load(project_dir)
    gcfg = GlobalConfig.load()
    paper_dir = project_dir / "paper"
    paper_dir.mkdir(exist_ok=True)

    if not cfg.description:
        log("[error] no research description — run 'raconteur init' first")
        raise SystemExit(1)

    check_prerequisites(project_dir, cfg)
    _ensure_style(project_dir, cfg)

    brain = Brain(gcfg, coordinator=cfg.brain.coordinator_model)

    if not cfg.topic or not cfg.focus:
        from .outline import _parse_description
        log("[raconteur] extracting topic and focus…")
        parsed = _parse_description(brain, cfg.description)
        if parsed.get("topic"):
            cfg.topic = parsed["topic"]
        if parsed.get("focus"):
            cfg.focus = parsed["focus"]
        if not cfg.title and parsed.get("title"):
            cfg.title = parsed["title"]
        cfg.save(project_dir)
        log(f"  title : {cfg.title}")
        log(f"  topic : {cfg.topic}")
        log(f"  focus : {cfg.focus}")

    user_rev = find_user_revision(paper_dir, cfg.short_title, chain_includes="onepager")
    existing = find_latest(paper_dir, cfg.short_title, "md",
                           last_initials="ra", chain_includes="onepager")

    if not existing:
        _onepager_fresh(project_dir, cfg, brain, paper_dir)
    elif user_rev:
        log(f"[raconteur] found revision: {user_rev.name}")
        _revise(project_dir, cfg, brain, paper_dir, user_rev)
    else:
        log("[raconteur] one-pager already exists — annotate the docx with your "
            "initials and re-run to revise, or run 'raconteur outline'")
        return

    from .notify import send_email
    send_email(
        f"raconteur one-pager done: {cfg.short_title}",
        f"One-pager complete for '{cfg.title or cfg.short_title}'.\nProject: {project_dir}",
        gcfg,
    )

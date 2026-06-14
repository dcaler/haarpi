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

# ── description → title/topic/focus (worker) ─────────────────────────────────

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

# ── structural analysis (coordinator) ────────────────────────────────────────

_ANALYZE_SYSTEM = (
    "You extract the intellectual structure of academic research for paper planning. "
    "Respond with ONLY a JSON object — no markdown, no explanation."
)
_ANALYZE_PROMPT = """\
Analyze this academic paper description and literature review context to extract \
the paper's intellectual structure.

Description:
{description}
{litrev_context}
{content_status}
Extract the following and return as JSON with exactly these keys:
- "contribution": the core claimed contribution — name the specific method, \
approach, or finding (one sentence)
- "background_pillars": 2–5 named intellectual areas that need background \
coverage; derive the names from the paper's actual content (these become \
subsections of a Background section, not a generic Related Work)
- "method_steps": ordered list of the specific methodological steps or pipeline \
stages described; name each step from what the paper actually does. If methods \
content is not available, list only steps described in the description or \
literature review.
- "empirical_elements": list of any named case studies, datasets, or real-world \
grounding mentioned (use their actual names as given in the description or \
literature review)
- "results_structure": ordered list describing how results should be presented. \
If results content is not available, describe anticipated or expected results \
only — do not imply specific empirical findings that have not been provided.
- "discussion_angle": specifically what this paper's method or findings reveal or \
enable that existing approaches do not; be concrete
- "limitations": 1–3 key limitations or caveats to address

Return ONLY valid JSON."""

# ── shared system ─────────────────────────────────────────────────────────────

_SYSTEM = (
    "You are an expert academic writing assistant. "
    "You help researchers plan and structure scholarly papers."
)

# ── draft outline (coordinator) ───────────────────────────────────────────────

_DRAFT_PROMPT = """\
Create a detailed outline for an academic paper. Use the structural analysis \
below to derive all section and subsection structure from this paper's actual \
intellectual content.

Title: {title}
Topic: {topic}
Focus: {focus}
{venue_section}
Structural analysis:
{analysis}
{litrev_section}
{code_section}
{results_section}
Rules:
- All section and subsection names must be derived from the paper's content — \
do not use generic names such as "Related Work", "Case Study", "Implications", \
or "Theoretical Framework"
- Use ## for major sections (numbered: ## 1. Introduction, ## 2. …, etc.)
- Use ### for subsections wherever the structural analysis identifies multiple \
distinct pillars, steps, or stages
- Background subsections should map to the background_pillars in the analysis
- Methods subsections should map to the method_steps in the analysis, in order
- If empirical_elements lists named cases or datasets, each must appear as a \
named subsection, not a generic placeholder
- Results must follow the sequence in results_structure from the analysis
- Discussion must address the discussion_angle from the analysis, and include \
a Limitations subsection
- Calibrate the specificity of Methods, Results, Discussion, and Conclusion to \
the available content: if methods content is absent, Methods describes planned \
approach only; if results content is absent, Results describes anticipated \
findings only; Discussion and Conclusion must not claim specific empirical \
outcomes that have not been provided
- Include 3–5 bullet points per subsection describing what that subsection \
specifically argues, shows, or demonstrates for this paper
- Output only the outline — no preamble or closing remarks
"""

# ── critique (coordinator) ────────────────────────────────────────────────────

_CRITIQUE_PROMPT = """\
Critique this paper outline against the structural analysis. Identify every \
specific problem.

Structural analysis:
{analysis}

Outline to critique:
{outline}

Check for:
1. Section or subsection names that are generic templates rather than derived \
from the analysis content
2. Method steps from method_steps that are missing, merged incorrectly, or \
out of order
3. Background pillars from background_pillars that are absent or mislabelled
4. Empirical elements from empirical_elements that appear as generic \
placeholders rather than named
5. Results sequence that does not follow results_structure from the analysis
6. Discussion that does not address discussion_angle from the analysis, or \
lacks a Limitations subsection
7. Bullet points that describe generic academic moves rather than specific \
claims, steps, or findings for this paper
8. Missing ### subsections where the analysis indicates multiple distinct \
components exist
9. Methods, Results, Discussion, or Conclusion sections that claim specific \
empirical detail not supported by the available content noted in the analysis \
(e.g. specific findings, measured outcomes, or evaluation results when no \
results content was provided)

Output: a numbered list of specific, actionable problems. One line each. \
Skip checks with no issues found. No preamble."""

# ── revise (coordinator) ──────────────────────────────────────────────────────

_REVISE_PROMPT = """\
Revise this paper outline to fix every problem in the critique below.

Structural analysis:
{analysis}

Current outline:
{outline}

Problems to fix:
{critique}

Fix every listed problem. Preserve what is already correct. Maintain ## major \
sections and ### subsections. All names must be derived from the paper's actual \
content. Output only the revised outline. No preamble."""

# ── user-annotation revision (coordinator) ────────────────────────────────────

_USER_REVISE_PROMPT = """\
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
- Address each reviewer comment with substantive changes to the relevant \
section or structure
- Maintain numbered section format (## 1. Introduction, etc.) with ### \
subsections and 3–5 bullet points per subsection
- Output only the revised outline — no preamble or closing remarks.
"""


# ── helpers ───────────────────────────────────────────────────────────────────

def _strip_fence(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = "\n".join(raw.split("\n")[1:])
    if raw.endswith("```"):
        raw = "\n".join(raw.split("\n")[:-1])
    return raw.strip()


def _parse_description(brain: Brain, description: str) -> dict:
    raw = brain.worker(
        _PARSE_PROMPT.format(description=description),
        system=_PARSE_SYSTEM,
        num_ctx=2048,
    )
    try:
        return json.loads(_strip_fence(raw))
    except Exception as e:
        print(f"[warn] could not parse description: {e}", file=sys.stderr)
        return {}


def _content_status(litrev: str, code: str, results: str) -> str:
    lines = [
        "Content availability:",
        f"  - Literature review : {'yes' if litrev else 'no'}",
        f"  - Methods / code    : {'yes' if code else 'no'}",
        f"  - Results / data    : {'yes' if results else 'no'}",
    ]
    if not code or not results:
        lines.append(
            "Sections covering unavailable content must describe planned "
            "approaches or anticipated findings only — do not claim specific "
            "empirical detail that has not been provided. Discussion and "
            "Conclusion must be scoped to what the available content supports."
        )
    return "\n".join(lines)


def _analyze_structure(
    brain: Brain, description: str, litrev: str, code: str, results: str
) -> str:
    """Return structural analysis as a JSON string (coordinator call)."""
    litrev_context = f"\nLiterature Review Context:\n{litrev}\n" if litrev else ""
    status = _content_status(litrev, code, results)
    raw = brain.coordinator(
        _ANALYZE_PROMPT.format(
            description=description,
            litrev_context=litrev_context,
            content_status=status,
        ),
        system=_ANALYZE_SYSTEM,
        num_ctx=8192,
    )
    cleaned = _strip_fence(raw)
    try:
        parsed = json.loads(cleaned)
        # embed content status so all downstream prompts see it
        return f"{status}\n\n{json.dumps(parsed, indent=2)}"
    except Exception as e:
        print(f"[warn] could not parse structural analysis: {e}", file=sys.stderr)
        return f"{status}\n\n{cleaned}"


def _critique_revise(brain: Brain, outline: str, analysis: str, n: int) -> str:
    """One critique→revise cycle. Returns the revised outline."""
    print(f"[raconteur] critique {n}…", file=sys.stderr)
    critique = brain.coordinator(
        _CRITIQUE_PROMPT.format(analysis=analysis, outline=outline),
        system=_SYSTEM,
        num_ctx=8192,
    )
    print(f"[raconteur] critique {n} findings:\n{critique}", file=sys.stderr)

    print(f"[raconteur] revise {n}…", file=sys.stderr)
    revised = brain.coordinator(
        _REVISE_PROMPT.format(analysis=analysis, outline=outline, critique=critique),
        system=_SYSTEM,
        num_ctx=8192,
    )
    return revised


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


# ── entry point ───────────────────────────────────────────────────────────────

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


# ── fresh outline: analyse → draft → critique→revise × 2 ─────────────────────

def _outline_fresh(
    project_dir: Path, cfg: ProjectConfig, brain: Brain, paper_dir: Path
) -> None:
    litrev = load_litreview(project_dir, cfg.litrev_dir) if cfg.litrev_dir else ""
    code = load_code(project_dir, cfg.methods_dir) if cfg.methods_dir else ""
    results = load_results(project_dir, cfg.results_dir) if cfg.results_dir else ""

    # Pass 1: structural analysis
    print("[raconteur] analysing paper structure…", file=sys.stderr)
    analysis = _analyze_structure(brain, cfg.description, litrev, code, results)

    venue_section = _build_venue_section(cfg, project_dir)
    litrev_section = f"Literature Review Context:\n{litrev}\n" if litrev else ""
    code_section = f"Analysis Methods:\n{code}\n" if code else ""
    results_section = f"Analysis Results:\n{results}\n" if results else ""

    # Pass 2: draft
    print("[raconteur] drafting outline…", file=sys.stderr)
    draft = brain.coordinator(
        _DRAFT_PROMPT.format(
            title=cfg.title,
            topic=cfg.topic,
            focus=cfg.focus,
            venue_section=venue_section,
            analysis=analysis,
            litrev_section=litrev_section,
            code_section=code_section,
            results_section=results_section,
        ),
        system=_SYSTEM,
        num_ctx=8192,
    )

    # Passes 3–4 and 5–6: two critique→revise cycles
    outline = _critique_revise(brain, draft, analysis, n=1)
    outline = _critique_revise(brain, outline, analysis, n=2)

    _write(project_dir, cfg, paper_dir, outline)


# ── user-annotation revision ──────────────────────────────────────────────────

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

    prompt = _USER_REVISE_PROMPT.format(
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


# ── write output ──────────────────────────────────────────────────────────────

def _write(project_dir: Path, cfg: ProjectConfig, paper_dir: Path, text: str) -> None:
    output = f"# {cfg.title}\n\n{text.strip()}\n"
    out_path = paper_dir / major_name(cfg.short_title, "md")
    out_path.write_text(output, encoding="utf-8")
    print(f"[raconteur] wrote {out_path.relative_to(project_dir)}", file=sys.stderr)

    docx = to_docx(out_path)
    if docx:
        print(f"[raconteur] wrote {docx.relative_to(project_dir)}", file=sys.stderr)

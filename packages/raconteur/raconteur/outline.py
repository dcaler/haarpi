from __future__ import annotations
import json
import sys
from .log import log
from pathlib import Path
from .brain import Brain
from .config import ProjectConfig, GlobalConfig
from .context import load_litreview, load_methods, load_results, load_venue_analysis, check_prerequisites, load_onepager
from .naming import (
    major_name, major_outline_name, find_latest, find_user_revision,
)
from .render import to_docx

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
{narrative_context}{litrev_context}
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

# ── equation extraction (worker) ──────────────────────────────────────────────

_EXTRACT_EQUATIONS_PROMPT = """\
List every named mathematical equation, formula, update rule, or computational \
expression described in this methods writeup — including those written inline or \
in prose (e.g. opinion update rules, confidence bounds, weight decay, trust decay, \
distance metrics, threshold conditions).
For each return: {{"name": "short name", "symbol": "the expression as written", \
"purpose": "what it computes or represents"}}
Return ONLY a JSON array. Return [] only if the writeup contains no mathematical expressions.

Methods writeup:
{code}"""

# ── findings extraction (worker) ──────────────────────────────────────────────

_EXTRACT_FINDINGS_PROMPT = """\
Extract the concrete, reportable findings from this results content.
Focus on extractable facts: named outcomes, quantitative values, percentages, \
effect sizes, named patterns or categories, statistical test results. \
Do not summarise prose — extract facts that would appear as specific claims in a paper.

For each finding return:
{{"finding": "one-sentence statement of the specific result", \
"value": "the number, percentage, or named value if present (else null)", \
"section": "which Results subsection this belongs in"}}

Return ONLY a JSON array. Return [] if no concrete findings are present.

Results content:
{results}"""

# ── design extraction (worker) ────────────────────────────────────────────────

# rayleigh's digest interleaves how the experiments were set up with what they
# found. The one-pager's Approach beat needs the former and must not see the
# latter, so the two are pulled apart here rather than left to a prompt to
# self-censor.
_EXTRACT_DESIGN_PROMPT = """\
Extract the EXPERIMENTAL DESIGN from this results content — how the experiments \
were set up, not what they found.

For each experiment return:
{{"experiment": "short name", "setup": "what was run, and on what", \
"conditions": "the conditions, arms, or comparisons", \
"parameters": "the parameters swept or held fixed, with values if given"}}

Report no outcomes, findings, or result values of any kind — only the design.

Return ONLY a JSON array. Return [] if no experimental design is described.

Results content:
{results}"""

# ── shared system ─────────────────────────────────────────────────────────────

_SYSTEM = (
    "You are an expert academic writing assistant. "
    "You help researchers plan and structure scholarly papers."
)

# ── draft outline (coordinator) ───────────────────────────────────────────────

# CRediT contributor-role taxonomy (credit.niso.org) — reproduced in the
# outline's Acknowledgements section verbatim, as the reference list the author
# assigns from. The tool never assigns roles itself.
_CREDIT_ROLES = [
    "Conceptualization",
    "Data curation",
    "Formal analysis",
    "Funding acquisition",
    "Investigation",
    "Methodology",
    "Project administration",
    "Resources",
    "Software",
    "Supervision",
    "Validation",
    "Visualization",
    "Writing – original draft",
    "Writing – review & editing",
]

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
{narrative_section}{litrev_section}
{code_section}
{results_section}
Rules:
- The outline must expand the author-approved narrative spine, if provided — every \
beat of that narrative must be represented, and the section structure must serve \
that through-line rather than diverge from it
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
- If methods source code is provided above: Methods subsections must be grounded \
in the actual code — reference specific algorithms, functions, parameters, and \
implementation choices present in the code; method_steps gives structural order \
but the code gives the specific content; bullet points must specify which equations \
from key_equations are introduced or derived there (if key_equations is empty, \
extract equations directly from the code_section above)
- If results content is provided above: Results subsections must be grounded in \
the actual results — cite specific values, outcomes, patterns, or model outputs \
present in the results content; results_structure gives structural order but the \
results give the specific content; bullet points must cite specific findings from \
key_findings with values where present (if key_findings is empty, extract concrete \
facts directly from the results_section above)
- If methods source code is absent, Methods describes the planned approach only; \
if results content is absent, Results describes anticipated findings only; \
Discussion and Conclusion must not claim specific empirical outcomes not supported \
by the available content
- Include 3–5 bullet points per subsection describing what that subsection \
specifically argues, shows, or demonstrates for this paper
- Open the outline with an unnumbered "## Abstract" section: 2–3 bullets naming \
what the abstract must distil (the contribution, the key result, the implication). \
The abstract text itself is drafted last, from the finished paper, and is expected \
to be redrafted across editing rounds — the bullets state its brief, not its prose
- After the Conclusion, add an unnumbered "## Acknowledgements" section whose \
bullets reproduce EXACTLY the following CRediT contributor-role taxonomy, one \
role per bullet, as the author's reference for assigning contributions. Do not \
invent contributor names, do not assign any role, do not omit or reword a role:
{credit_roles}
- End with an unnumbered "## References" heading with no bullets (the \
bibliography is rendered at draft time)
- Do not include appendices
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
10. Methods subsections that do not specify which equations from key_equations \
are introduced or derived there (only applies when key_equations is non-empty)
11. Results subsections that do not cite specific findings from key_findings \
(only applies when key_findings is non-empty)
12. A missing unnumbered Abstract section at the top, or one whose bullets \
write abstract prose instead of naming what the abstract must distil
13. A missing Acknowledgements section between the Conclusion and References, \
or one whose bullets do not reproduce all 14 CRediT contributor roles exactly, \
or that assigns roles or names contributors
14. Any appendix sections (none belong in this outline)

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

# ── content refresh (coordinator) ────────────────────────────────────────────

_REFRESH_CONTENT_PROMPT = """\
Update the Methods and/or Results sections of this paper outline using newly \
available content. All other sections must be reproduced exactly as they appear \
— do not paraphrase, reorder, or alter them.

Title: {title}
Topic: {topic}
Focus: {focus}
{venue_section}
Structural analysis:
{analysis}

{code_section}{results_section}
Current outline:
{outline}

Instructions:
- If methods source code is provided above: identify the Methods section and rewrite \
it and all its ### subsections grounded in the actual code — reference specific \
algorithms, functions, parameters, and implementation choices; method_steps gives \
structural order; each subsection must specify which equations from key_equations \
are introduced there (if key_equations is empty, extract equations directly from \
the source code above)
- If results content is provided above: identify the Results section and rewrite it \
and all its ### subsections grounded in the actual results — cite specific values, \
outcomes, and patterns present in the results content; results_structure gives \
structural order; each Results subsection must cite specific findings from \
key_findings with values where present (if key_findings is empty, extract concrete \
facts directly from the results content above)
- Every other ## section and its subsections must be copied verbatim
- Output only the complete outline — no preamble or closing remarks
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
        log(f"[warn] could not parse description: {e}")
        return {}


def _content_status(litrev: str, code: str, results: str) -> str:
    lines = [
        "Content availability:",
        f"  - Literature review : {'yes' if litrev else 'no'}",
        f"  - Methods writeup   : {'yes' if code else 'no'}",
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


def _extract_equations(brain: Brain, code: str) -> list[dict]:
    """Worker call: extract named equations from code."""
    raw = brain.worker(
        _EXTRACT_EQUATIONS_PROMPT.format(code=code[:16000]),
        num_ctx=16384,
    )
    try:
        result = json.loads(_strip_fence(raw))
        return result if isinstance(result, list) else []
    except Exception:
        return []


def _extract_findings(brain: Brain, results: str) -> list[dict]:
    """Worker call: extract concrete findings from results content."""
    raw = brain.worker(
        _EXTRACT_FINDINGS_PROMPT.format(results=results[:8000]),
        num_ctx=8192,
    )
    try:
        result = json.loads(_strip_fence(raw))
        return result if isinstance(result, list) else []
    except Exception:
        return []


def _extract_design(brain: Brain, results: str) -> list[dict]:
    """Worker call: extract experimental design (not findings) from results."""
    raw = brain.worker(
        _EXTRACT_DESIGN_PROMPT.format(results=results[:8000]),
        num_ctx=8192,
    )
    try:
        result = json.loads(_strip_fence(raw))
        return result if isinstance(result, list) else []
    except Exception:
        return []


def analysis_view(analysis: str, drop: tuple[str, ...] = ()) -> str:
    """Re-serialise the structural analysis with some keys withheld.

    ``_analyze_structure`` returns "<status>\\n\\n<json>". A caller that must not
    show a beat some part of the analysis — Approach may not see key_findings —
    takes a filtered copy. Falls back to the whole thing if the body is not JSON,
    which is what _analyze_structure emits when the model's output would not parse.
    """
    status, sep, body = analysis.partition("\n\n")
    if not sep:
        return analysis
    try:
        parsed = json.loads(body)
    except Exception:
        return analysis
    kept = {k: v for k, v in parsed.items() if k not in drop}
    return f"{status}\n\n{json.dumps(kept, indent=2)}"


def _analyze_structure(
    brain: Brain, description: str, litrev: str, code: str, results: str,
    narrative: str = "",
) -> str:
    """Return structural analysis as a JSON string (coordinator call).

    ``narrative`` is the human-approved one-pager: the concise path through the
    paper. When present it anchors the intellectual structure — the extracted
    contribution, pillars, and discussion angle must honour that through-line.
    """
    litrev_context = f"\nLiterature Review Context:\n{litrev}\n" if litrev else ""
    narrative_context = (
        "\nNarrative spine (author-approved concise path through the paper — the "
        "structure you extract must follow this through-line):\n"
        f"{narrative}\n"
        if narrative else ""
    )
    status = _content_status(litrev, code, results)
    raw = brain.coordinator(
        _ANALYZE_PROMPT.format(
            description=description,
            narrative_context=narrative_context,
            litrev_context=litrev_context,
            content_status=status,
        ),
        system=_ANALYZE_SYSTEM,
        num_ctx=16384,
    )
    cleaned = _strip_fence(raw)
    try:
        parsed = json.loads(cleaned)
    except Exception as e:
        log(f"[warn] could not parse structural analysis: {e}")
        return f"{status}\n\n{cleaned}"

    if code:
        log("[raconteur] extracting equations from methods writeup…")
        parsed["key_equations"] = _extract_equations(brain, code)

    if results:
        log("[raconteur] extracting findings from results…")
        parsed["key_findings"] = _extract_findings(brain, results)
        log("[raconteur] extracting experimental design from results…")
        parsed["key_design"] = _extract_design(brain, results)

    return f"{status}\n\n{json.dumps(parsed, indent=2)}"


def _critique_revise(brain: Brain, outline: str, analysis: str, n: int) -> str:
    """One critique→revise cycle. Returns the revised outline."""
    log(f"[raconteur] critique {n}…")
    critique = brain.coordinator(
        _CRITIQUE_PROMPT.format(analysis=analysis, outline=outline),
        system=_SYSTEM,
        num_ctx=8192,
    )
    log(f"[raconteur] critique {n} findings:\n{critique}")

    log(f"[raconteur] revise {n}…")
    revised = brain.coordinator(
        _REVISE_PROMPT.format(analysis=analysis, outline=outline, critique=critique),
        system=_SYSTEM,
        num_ctx=8192,
    )
    return revised


def _venue_specs_block(cfg: ProjectConfig, venue: str = "") -> str:
    from . import slate
    return slate.specs_block(cfg.venue(venue) if venue else None)


def _build_venue_section(cfg: ProjectConfig, project_dir: Path, venue: str = "") -> str:
    """What the writer is told about where this is going.

    An outline is written FOR a venue — its length, its columns, what it publishes — so the
    venue is an argument, not a project-wide setting. The one-pager passes none: the
    narrative belongs to the work, not to whoever might publish it.
    """
    specs = _venue_specs_block(cfg, venue)
    venue_analysis = load_venue_analysis(project_dir) if venue else ""
    if venue_analysis:
        block = f"Venue Analysis:\n{venue_analysis}\n"
        if specs:
            block += f"\n{specs}\n"
        return block
    return specs


# ── entry point ───────────────────────────────────────────────────────────────

def run(project_dir: Path, venue: str = "") -> None:
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

    if not load_onepager(project_dir, cfg.short_title):
        log("[error] no one-pager found — run 'raconteur onepager' first")
        raise SystemExit(1)

    # Train style profile before outlining if opted in but profile is missing.
    if cfg.use_style:
        from .style import STYLE_PROFILE_PATH
        if not STYLE_PROFILE_PATH.exists():
            log("[raconteur] style profile missing — training now…")
            from .style import run as style_run
            style_run(project_dir)

    brain = Brain(gcfg, coordinator=cfg.brain.coordinator_model)

    if not cfg.topic or not cfg.focus:
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

    from . import slate
    venue = slate.resolve(cfg, venue)
    if venue:
        log(f"[raconteur] outlining for {cfg.venues[venue].name} ({venue})")

    # An outline belongs to ONE venue: its length, its columns, what it publishes. The JASSS
    # outline sits beside the ISMIR one and neither sees the other's markup.
    scope = ([venue] if venue else []) + ["outline"]
    others = [v for v in cfg.venues if v != venue]
    user_rev = find_user_revision(paper_dir, cfg.short_title, chain_includes=scope,
                                  chain_excludes=others)
    existing = find_latest(paper_dir, cfg.short_title, "md", last_initials="ra",
                           chain_includes=scope, chain_excludes=others)

    if not existing:
        _outline_fresh(project_dir, cfg, brain, paper_dir, venue)
    elif user_rev:
        log(f"[raconteur] found revision: {user_rev.name}")
        _revise(project_dir, cfg, brain, paper_dir, user_rev, venue)
    else:
        code = load_methods(project_dir) if cfg.use_methods and not cfg.methods_drafted else ""
        results = load_results(project_dir, cfg.results_dir) if cfg.results_dir and not cfg.results_dir_drafted else ""
        if code or results:
            _refresh_content(project_dir, cfg, brain, paper_dir, existing, code, results, venue)
        else:
            log("[raconteur] outline already exists — annotate the docx with your initials and re-run to revise")
            return



# ── fresh outline: analyse → draft → critique→revise × 2 ─────────────────────

def _outline_fresh(
    project_dir: Path, cfg: ProjectConfig, brain: Brain, paper_dir: Path,
    venue: str = "",
) -> None:
    litrev = load_litreview(project_dir, cfg.litrev_dir) if cfg.litrev_dir else ""
    code = load_methods(project_dir) if cfg.use_methods else ""
    results = load_results(project_dir, cfg.results_dir) if cfg.results_dir else ""
    narrative = load_onepager(project_dir, cfg.short_title)

    # Pass 1: structural analysis
    log("[raconteur] analysing paper structure…")
    analysis = _analyze_structure(brain, cfg.description, litrev, code, results, narrative)

    venue_section = _build_venue_section(cfg, project_dir, venue)
    narrative_section = f"Narrative spine (author-approved):\n{narrative}\n" if narrative else ""
    litrev_section = f"Literature Review Context:\n{litrev}\n" if litrev else ""
    code_section = f"Methods (raster writeup):\n{code}\n" if code else ""
    results_section = f"Results Content:\n{results}\n" if results else ""

    # Pass 2: draft
    log("[raconteur] drafting outline…")
    draft = brain.coordinator(
        _DRAFT_PROMPT.format(
            title=cfg.title,
            topic=cfg.topic,
            focus=cfg.focus,
            venue_section=venue_section,
            analysis=analysis,
            narrative_section=narrative_section,
            litrev_section=litrev_section,
            code_section=code_section,
            results_section=results_section,
            credit_roles="\n".join(f"  - {r}" for r in _CREDIT_ROLES),
        ),
        system=_SYSTEM,
        num_ctx=16384,
    )

    # Passes 3–4 and 5–6: two critique→revise cycles
    outline = _critique_revise(brain, draft, analysis, n=1)
    outline = _critique_revise(brain, outline, analysis, n=2)

    _write(project_dir, cfg, paper_dir, outline, venue)
    if code:
        cfg.methods_drafted = True
    if results:
        cfg.results_dir_drafted = True
    cfg.save(project_dir)


# ── content refresh ───────────────────────────────────────────────────────────

def _refresh_content(
    project_dir: Path,
    cfg: ProjectConfig,
    brain: Brain,
    paper_dir: Path,
    existing_md: Path,
    code: str,
    results: str,
    venue: str = "",
) -> None:
    litrev = load_litreview(project_dir, cfg.litrev_dir) if cfg.litrev_dir else ""
    narrative = load_onepager(project_dir, cfg.short_title)

    log("[raconteur] analysing paper structure…")
    analysis = _analyze_structure(brain, cfg.description, litrev, code, results, narrative)

    existing_text = existing_md.read_text(encoding="utf-8")
    venue_section = _build_venue_section(cfg, project_dir, venue)
    code_section = f"Methods (raster writeup):\n{code}\n\n" if code else ""
    results_section = f"Results Content:\n{results}\n\n" if results else ""

    what = " + ".join(filter(None, ["Methods" if code else "", "Results" if results else ""]))
    log(f"[raconteur] refreshing {what} section(s)…")
    updated = brain.coordinator(
        _REFRESH_CONTENT_PROMPT.format(
            title=cfg.title,
            topic=cfg.topic,
            focus=cfg.focus,
            venue_section=venue_section,
            analysis=analysis,
            code_section=code_section,
            results_section=results_section,
            outline=existing_text,
        ),
        system=_SYSTEM,
        num_ctx=16384,
    )
    updated = _critique_revise(brain, updated, analysis, n=1)
    updated = _critique_revise(brain, updated, analysis, n=2)
    _write(project_dir, cfg, paper_dir, updated, venue)
    if code:
        cfg.methods_drafted = True
    if results:
        cfg.results_dir_drafted = True
    cfg.save(project_dir)


# ── user-annotation revision ──────────────────────────────────────────────────

def _revise(
    project_dir: Path,
    cfg: ProjectConfig,
    brain: Brain,
    paper_dir: Path,
    user_rev: Path,
    venue: str = "",
) -> None:
    """Answer each anchored comment with an in-place tracked change (paper parity).

    New upstream content is _refresh_content's job, not this path's — here the
    reviewer's annotations are the whole brief. The accepted-text .md sibling is
    what 'draft' binds.
    """
    from .paper import _bib_block
    from .context import load_bib_summary, load_bib_keys
    from .redline_revise import redline_revise

    litrev = load_litreview(project_dir, cfg.litrev_dir) if cfg.litrev_dir else ""
    code = load_methods(project_dir) if cfg.use_methods else ""
    results = load_results(project_dir, cfg.results_dir) if cfg.results_dir else ""
    bib_summary = load_bib_summary(project_dir, cfg.litrev_dir) if cfg.litrev_dir else ""
    bib_keys = load_bib_keys(project_dir, cfg.litrev_dir) if cfg.litrev_dir else set()

    redline_revise(project_dir, cfg, brain, paper_dir, user_rev,
                   litrev, code, results, _bib_block(bib_summary), bib_keys,
                   md_sibling=True)


# ── write output ──────────────────────────────────────────────────────────────

def _write(project_dir: Path, cfg: ProjectConfig, paper_dir: Path, text: str,
           venue: str = "") -> None:
    output = f"# {cfg.title}\n\n{text.strip()}\n"
    out_path = paper_dir / major_outline_name(cfg.short_title, "md", venue=venue)
    out_path.write_text(output, encoding="utf-8")
    log(f"[raconteur] wrote {out_path.relative_to(project_dir)}")

    docx = to_docx(out_path)
    if docx:
        log(f"[raconteur] wrote {docx.relative_to(project_dir)}")

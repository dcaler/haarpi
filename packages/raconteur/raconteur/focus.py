from __future__ import annotations
import re
from pathlib import Path

from .brain import Brain
from .config import ProjectConfig, GlobalConfig
from .context import load_litreview, load_methods, load_results, load_bib_summary, load_style_profile, load_onepager
from .guards import (
    LITREV_KW as _LITREV_KW,
    CODE_KW as _CODE_KW,
    RESULTS_KW as _RESULTS_KW,
)
from .log import log
from .naming import find_latest, minor_name, parse
from .render import to_docx

# ── system ────────────────────────────────────────────────────────────────────

_SYSTEM = (
    "You are an expert academic writing assistant. "
    "You refine and strengthen specific sections of scholarly papers."
)

# ── section refine (coordinator) ──────────────────────────────────────────────

_REFINE_SECTION_PROMPT = """\
Refine and strengthen the {heading} section of this academic paper.

Title: {title}
Topic: {topic}
Focus: {focus}
{venue_section}\
{style_section}\
Structural analysis:
{analysis}

Section outline:
{section_outline}

{context_section}\
{bib_section}\
Current section text:
{current_text}

Instructions:
- Improve clarity, precision, and depth of argument
- Ensure all subsections are fully developed prose (150–300 words each)
- For Methods sections: strengthen specificity — reference specific algorithms, \
functions, parameters, and equations from the source code above
- For Results sections: cite specific values, outcomes, and patterns from the \
results content above
- For Background sections: improve synthesis — integrate ideas into argument \
rather than listing or summarising
- For Discussion sections: sharpen the connection to background and address \
the discussion_angle and limitations from the structural analysis
- Do not include the ## section heading in your output
- Output only the refined section text — no preamble, no closing remarks
"""

# ── section critique (coordinator) ────────────────────────────────────────────

_CRITIQUE_SECTION_PROMPT = """\
Critique the {heading} section of this academic paper draft.

Structural analysis:
{analysis}

Section outline (what this section must cover):
{section_outline}

Section text:
{text}

Check for:
1. Subsections missing, out of order, or misnamed relative to the outline
2. Bullet-list or outline thinking not converted to connected prose
3. Generic academic statements not grounded in this paper's specific content
4. Methods text that does not reference specific code details when source code \
was available
5. Results text that does not cite specific values or findings when results \
were available
6. Background that lists or summarises individual papers rather than synthesising \
ideas into argument
7. Discussion that does not address the discussion_angle or limitations from the analysis
8. Subsections under 100 words or over 500 words

Output: numbered list of specific, actionable problems. One line each. \
Write "No issues found." if all checks pass. No preamble.
"""

# ── section revise (coordinator) ──────────────────────────────────────────────

_REVISE_SECTION_PROMPT = """\
Revise the {heading} section to fix every problem listed below.

Structural analysis:
{analysis}

Section outline (maintain this structure):
{section_outline}

Current text:
{text}

Problems to fix:
{critique}

Fix every listed problem. Preserve what is already correct. \
Output only the revised section text — no heading, no preamble.
"""


# ── helpers ───────────────────────────────────────────────────────────────────

def _extract_section(text: str, query: str) -> tuple[str, str, str] | None:
    """Split text into (before, section_text, after) by section number or heading name.

    query can be a section number ("3"), a heading word ("Methods"), or both ("3. Methods").
    Returns None if no match found.
    """
    lines = text.split("\n")

    num_re = re.compile(rf"^(#{1,4})\s+{re.escape(query)}[\s\.]", re.IGNORECASE)
    head_re = re.compile(
        rf"^(#{1,4})\s+(?:\d+[\.\s]+)?{re.escape(query)}\b", re.IGNORECASE
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
            if len(lines[i]) - len(lines[i].lstrip("#")) <= heading_level:
                end_idx = i
                break

    before = "\n".join(lines[:start_idx])
    section = "\n".join(lines[start_idx:end_idx])
    after = "\n".join(lines[end_idx:])
    return before, section, after


def _heading_from_section_text(section_text: str) -> str:
    """Return the heading text (without ## prefix) from a section block."""
    first_line = section_text.split("\n", 1)[0].strip()
    return re.sub(r"^#+\s*", "", first_line)


def _section_outline_for(heading: str, outline_path: Path | None, short_title: str) -> str:
    """Find the matching section in the outline file and return its body."""
    if outline_path is None:
        return ""
    outline_text = outline_path.read_text(encoding="utf-8")
    result = _extract_section(outline_text, heading)
    if result is None:
        # try stripping section number from heading for matching
        bare = re.sub(r"^\d+[\.\s]+", "", heading).strip()
        result = _extract_section(outline_text, bare)
    if result:
        _, section, _ = result
        # return just the body (strip the heading line)
        body_lines = section.split("\n")[1:]
        return "\n".join(body_lines).strip()
    return ""


def _style_block(style_profile: str) -> str:
    if not style_profile:
        return ""
    return f"Writing style guidance (match this author's voice):\n{style_profile}\n\n"


def _bib_block(bib_summary: str) -> str:
    if not bib_summary:
        return ""
    return f"Available citations (use [@citekey] format):\n{bib_summary}\n\n"


# Section keyword sets are imported from guards at the top of this module — one definition,
# shared with paper.py and the guards themselves.


def _context_for_section(heading: str, litrev: str, code: str, results: str) -> str:
    h = heading.lower()
    parts = []
    if any(kw in h for kw in _LITREV_KW) and litrev:
        parts.append(f"Literature review:\n{litrev}")
    if any(kw in h for kw in _CODE_KW) and code:
        parts.append(f"Methods (raster writeup):\n{code}")
    if any(kw in h for kw in _RESULTS_KW) and results:
        parts.append(f"Results Content:\n{results}")
    return ("\n\n".join(parts) + "\n\n") if parts else ""


def _venue_block(cfg: ProjectConfig, venue: str = "") -> str:
    from . import slate
    return slate.specs_block(cfg.venue(venue) if venue else None)


def _critique_revise(
    brain: Brain,
    heading: str,
    text: str,
    section_outline: str,
    analysis: str,
    n: int,
) -> str:
    log(f"[raconteur] critique '{heading}' ({n})…")
    critique = brain.coordinator(
        _CRITIQUE_SECTION_PROMPT.format(
            heading=heading,
            analysis=analysis,
            section_outline=section_outline,
            text=text,
        ),
        system=_SYSTEM,
        num_ctx=8192,
    )
    log(f"[raconteur] critique {n} findings:\n{critique}")
    if "no issues found" in critique.lower():
        return text
    log(f"[raconteur] revise '{heading}' ({n})…")
    return brain.coordinator(
        _REVISE_SECTION_PROMPT.format(
            heading=heading,
            analysis=analysis,
            section_outline=section_outline,
            text=text,
            critique=critique,
        ),
        system=_SYSTEM,
        num_ctx=8192,
    )


# ── entry point ───────────────────────────────────────────────────────────────

def run(project_dir: Path, section: str) -> None:
    if not ProjectConfig.exists(project_dir):
        log("[error] no paper/raconteur.yaml — run 'raconteur init' first")
        raise SystemExit(1)

    cfg = ProjectConfig.load(project_dir)
    gcfg = GlobalConfig.load()
    paper_dir = project_dir / "paper"

    paper_path = find_latest(
        paper_dir, cfg.short_title, "md",
        last_initials="ra", chain_excludes=["outline", "venue", "onepager"],
    )
    if paper_path is None:
        log("[error] no paper found — run 'raconteur paper' first")
        raise SystemExit(1)

    outline_path = find_latest(
        paper_dir, cfg.short_title, "md",
        last_initials="ra", chain_includes="outline",
    )

    paper_text = paper_path.read_text(encoding="utf-8")
    result = _extract_section(paper_text, section)
    if result is None:
        log(f"[error] section '{section}' not found in {paper_path.name}")
        # list available sections
        headings = [
            line[3:].strip()
            for line in paper_text.splitlines()
            if line.startswith("## ")
        ]
        log("  available sections: " + ", ".join(headings))
        raise SystemExit(1)

    before, section_text, after = result
    heading = _heading_from_section_text(section_text)
    log(f"[raconteur] focusing on: {heading}")

    litrev = load_litreview(project_dir, cfg.litrev_dir) if cfg.litrev_dir else ""
    code = load_methods(project_dir) if cfg.use_methods else ""
    results = load_results(project_dir, cfg.results_dir) if cfg.results_dir else ""
    bib_summary = load_bib_summary(project_dir, cfg.litrev_dir) if cfg.litrev_dir else ""
    style_profile = load_style_profile(project_dir) if cfg.use_style else ""
    narrative = load_onepager(project_dir, cfg.short_title)

    from .outline import _analyze_structure
    log("[raconteur] analysing paper structure…")
    analysis = _analyze_structure(
        Brain(gcfg, coordinator=cfg.brain.coordinator_model),
        cfg.description, litrev, code, results, narrative,
    )

    brain = Brain(gcfg, coordinator=cfg.brain.coordinator_model)
    section_outline = _section_outline_for(heading, outline_path, cfg.short_title)
    ctx = _context_for_section(heading, litrev, code, results)
    venue_section = _venue_block(cfg)
    bib_section = _bib_block(bib_summary)
    style_section = _style_block(style_profile)

    log(f"[raconteur] refining '{heading}'…")
    refined = brain.coordinator(
        _REFINE_SECTION_PROMPT.format(
            heading=heading,
            title=cfg.title,
            topic=cfg.topic,
            focus=cfg.focus,
            venue_section=venue_section,
            style_section=style_section,
            analysis=analysis,
            section_outline=section_outline,
            context_section=ctx,
            bib_section=bib_section,
            current_text=section_text,
        ),
        system=_SYSTEM,
        num_ctx=16384,
    )
    refined = _critique_revise(brain, heading, refined, section_outline, analysis, 1)
    refined = _critique_revise(brain, heading, refined, section_outline, analysis, 2)

    # reassemble: before + ## heading + refined body + after
    refined_section = f"## {heading}\n\n{refined.strip()}"
    parts = [p for p in [before.rstrip(), refined_section, after.lstrip()] if p.strip()]
    new_text = "\n\n".join(parts) + "\n"

    parsed = parse(paper_path, cfg.short_title)
    current_chain = parsed[1] if parsed else ["ra"]
    datestamp = parsed[0] if parsed else None
    out_path = paper_dir / minor_name(cfg.short_title, current_chain, "md", datestamp)
    out_path.write_text(new_text, encoding="utf-8")
    log(f"[raconteur] wrote {out_path.relative_to(project_dir)}")

    bib_path = (project_dir / cfg.litrev_dir / "output" / "refs.bib") if cfg.litrev_dir else None
    docx = to_docx(out_path, bib_path=bib_path)
    if docx:
        log(f"[raconteur] wrote {docx.relative_to(project_dir)}")

    from .notify import send_email
    send_email(
        f"raconteur focus done: {cfg.short_title} — {heading}",
        f"Section refined: {heading}\nProject: {project_dir}",
        gcfg,
    )

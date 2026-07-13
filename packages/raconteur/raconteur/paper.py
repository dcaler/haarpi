from __future__ import annotations
from pathlib import Path

from .brain import Brain
from .config import ProjectConfig, GlobalConfig
from . import guards
from .guards import (
    LITREV_KW as _LITREV_KW,
    CODE_KW as _CODE_KW,
    RESULTS_KW as _RESULTS_KW,
    is_references as _is_references,
    is_abstract as _is_abstract,
    is_acknowledgements as _is_acknowledgements,
)
from .context import (
    load_litreview, load_methods, load_results, load_bib_summary,
    load_bib_keys, load_style_profile, load_onepager,
)
from .log import log
from .naming import major_name, find_latest, find_user_revision
from .render import to_docx
from .revise import read_text, build_revision_context

# ── system ────────────────────────────────────────────────────────────────────

_SYSTEM = (
    "You are an expert academic writing assistant. "
    "You write clear, precise scholarly prose for peer-reviewed publication."
)

# ── section draft (coordinator) ───────────────────────────────────────────────

_DRAFT_SECTION_PROMPT = """\
Write the full text of the {heading} section for an academic paper.

Title: {title}
Topic: {topic}
Focus: {focus}
{venue_section}\
{style_section}\
Structural analysis:
{analysis}

Section outline (follow this structure exactly):
{section_outline}

{context_section}\
{bib_section}\
Instructions:
- Write fully developed academic prose; do not reproduce the outline bullets verbatim
- Use ### for subsection headings, matching names from the outline
- Each subsection should be 150–300 words of connected prose
- For Methods sections: reference specific algorithms, functions, parameters, and \
equations from the source code above; do not use vague descriptions
- For Results sections: cite specific values, outcomes, and patterns from the results \
content above; do not describe anticipated findings
- For Background/Introduction sections: synthesise ideas from the literature into \
argument — do not list or summarise individual papers; cite using [@citekey] format \
from the bibliography above
- For Discussion sections: connect results to background, address the discussion_angle \
and limitations from the structural analysis concretely; cite relevant background \
using [@citekey] format
- Use [@citekey] for all citations — only citekeys from the bibliography above are valid
- Do not include the ## section heading in your output — start with the first \
subsection or opening paragraph
- Output only this section's prose — no preamble, no closing remarks
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
2. Outline bullets reproduced as bullet points rather than converted to prose
3. Generic academic statements not grounded in this paper's specific content
4. Methods text that does not reference specific details (algorithms, equations, \
parameters) when a methods writeup was available
5. Results text that does not cite specific values or findings when results were available
6. Discussion that does not address the discussion_angle or limitations from the analysis
7. Subsections under 100 words or over 500 words

Do not comment on citation density or on whether the prose lists rather than synthesises — \
those are checked mechanically after this critique and reported separately.

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

# ── abstract (coordinator) ────────────────────────────────────────────────────

_DRAFT_ABSTRACT_PROMPT = """\
Write a concise academic abstract for this paper.

Title: {title}
Topic: {topic}
Focus: {focus}
{venue_section}\
{style_section}\
Structural analysis:
{analysis}

Instructions:
- {word_limit} words
- Cover: motivation/problem, method or approach, key results or contributions, implications
- Name the specific method, model, or approach; cite key findings with values if available
- Do not use citations or [CITE] placeholders
- Output only the abstract text — no label, no preamble
"""

# ── user-annotation revision (coordinator) ────────────────────────────────────

_REVISE_WITH_ANNOTATIONS_PROMPT = """\
Revise the {heading} section incorporating the reviewer annotations below.

Structural analysis:
{analysis}

Section outline:
{section_outline}

{context_section}\
{bib_section}\
Current text:
{text}

Reviewer annotations (apply only those relevant to this section; ignore the rest):
{annotations}

Instructions:
- Incorporate all tracked insertions, remove all tracked deletions in this section
- Address each reviewer comment relevant to this section with substantive changes
- Maintain academic prose register and subsection structure
- If methods source code is provided: update Methods to reference it specifically
- If results content is provided: update Results to cite specific values and findings
- Output only the revised section text — no heading, no preamble
"""


# ── helpers ───────────────────────────────────────────────────────────────────

def _parse_sections(text: str) -> list[tuple[str, str]]:
    """Split markdown on ## headings → [(heading_text, body_text), ...]."""
    sections: list[tuple[str, str]] = []
    current_heading: str | None = None
    current_lines: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if current_heading is not None:
                sections.append((current_heading, "\n".join(current_lines).strip()))
            current_heading = line[3:].strip()
            current_lines = []
        elif current_heading is not None:
            current_lines.append(line)
    if current_heading is not None:
        sections.append((current_heading, "\n".join(current_lines).strip()))
    return sections


# Section classification lives in guards.py so the guards and the context selector agree on
# what a "Methods" section is. Context selection deliberately lets a heading match more than
# one kind ("Model Evaluation" wants both code and results context), so it uses the keyword
# sets directly rather than guards.section_kind(), which picks a single winner.


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


def _venue_block(cfg: ProjectConfig) -> str:
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
    return ("Venue specifications:\n" + "\n".join(lines) + "\n") if lines else ""


# _is_references is imported from guards at the top of this module.


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


_GUARD_REPAIR_PROMPT = """\
Revise the {heading} section to satisfy every requirement below. These were computed \
mechanically from the text, not judged — each one is a fact about what you wrote.

Section outline (maintain this structure):
{section_outline}

{bib_section}Current text:
{text}

Requirements:
{imperatives}

Change only what the requirements demand. Do not restructure the section, and do not \
remove any [@citekey] the text already carries. Output only the revised section — no \
preamble."""


def _guard_section(
    text: str, heading: str, known: set[str], have_results: bool
) -> list[guards.Finding]:
    """Draft-phase guards for one section.

    Wrapping the section body in its own heading lets ``parse_paragraphs`` classify it, so
    the citation floor is gated on section kind — a Methods paragraph is grounded in the
    writeup, not the bibliography.
    """
    md = f"## {heading}\n\n{text}"
    paras = guards.parse_paragraphs(md)
    findings: list[guards.Finding] = []
    if known:  # an empty bib would make every key "unresolved"
        findings += guards.unresolved_keys(text, known)
    findings += guards.author_year_prose(text)
    findings += guards.uncited_paragraphs(paras)
    findings += guards.sparse_paragraphs(paras)
    if have_results:
        findings += guards.unnumbered_results_paragraphs(paras)
    return findings


def _guard_repair(
    brain: Brain,
    heading: str,
    text: str,
    section_outline: str,
    bib_section: str,
    known: set[str],
    have_results: bool,
    rounds: int = 2,
) -> str:
    """Feed mechanical findings back as imperatives until they clear or rounds run out.

    Python decides THAT something is wrong; the LLM decides how to fix it. A finding that
    survives every round is logged, not silently dropped.
    """
    for n in range(1, rounds + 1):
        findings = _guard_section(text, heading, known, have_results)
        if not findings:
            return text
        log(f"[raconteur] guards '{heading}' ({n}): {len(findings)} finding(s)")
        for f in findings:
            log(f"  · {f.kind} — {f.where}")
        imperatives = "\n".join(f"- {f.imperative}" for f in findings)
        text = brain.coordinator(
            _GUARD_REPAIR_PROMPT.format(
                heading=heading,
                section_outline=section_outline,
                bib_section=bib_section,
                text=text,
                imperatives=imperatives,
            ),
            system=_SYSTEM,
            num_ctx=16384,
        )
    remaining = _guard_section(text, heading, known, have_results)
    if remaining:
        log(f"[warn] '{heading}': {len(remaining)} guard finding(s) survived "
            f"{rounds} repair round(s) — shipping anyway:")
        for f in remaining:
            log(f"[warn]   · {f.kind} — {f.where}")
    return text


def _draft_abstract(
    brain: Brain,
    cfg: ProjectConfig,
    venue_section: str,
    style_section: str,
    analysis: str,
) -> str:
    limit = str(cfg.venue.abstract_limit) if cfg.venue.abstract_limit else "150–250"
    log("[raconteur] drafting abstract…")
    return brain.coordinator(
        _DRAFT_ABSTRACT_PROMPT.format(
            title=cfg.title,
            topic=cfg.topic,
            focus=cfg.focus,
            venue_section=venue_section,
            style_section=style_section,
            analysis=analysis,
            word_limit=limit,
        ),
        system=_SYSTEM,
        num_ctx=4096,
    )


def _ack_passthrough(section_outline: str) -> str:
    """The outline's CRediT reference list, verbatim; canonical list if bare."""
    text = section_outline.strip()
    if text:
        return text
    from .outline import _CREDIT_ROLES
    return "\n".join(f"- {r}" for r in _CREDIT_ROLES)


def _ensure_acknowledgements(sections: list[tuple[str, str]]) -> None:
    """Outlines minted before the Acknowledgements rule lack the section; the
    paper must still carry the CRediT reference list for the author."""
    if not any(_is_acknowledgements(h) for h, _ in sections):
        sections.append(("Acknowledgements", _ack_passthrough("")))


def _assemble(title: str, abstract: str, sections: list[tuple[str, str]]) -> str:
    parts = [f"# {title}", "", "**Abstract**", "", abstract.strip(), ""]
    for heading, text in sections:
        parts += [f"## {heading}", "", text.strip(), ""]
    parts += ["## References", ""]
    return "\n".join(parts)


def _write(project_dir: Path, cfg: ProjectConfig, paper_dir: Path, text: str) -> None:
    out_path = paper_dir / major_name(cfg.short_title, "md")
    out_path.write_text(text, encoding="utf-8")
    log(f"[raconteur] wrote {out_path.relative_to(project_dir)}")
    bib_path = (project_dir / cfg.litrev_dir / "output" / "refs.bib") if cfg.litrev_dir else None
    docx = to_docx(out_path, bib_path=bib_path)
    if docx:
        log(f"[raconteur] wrote {docx.relative_to(project_dir)}")


# ── fresh paper draft ─────────────────────────────────────────────────────────

def _style_block(style_profile: str) -> str:
    if not style_profile:
        return ""
    return f"Writing style guidance (match this author's voice):\n{style_profile}\n\n"


def _bib_block(bib_summary: str) -> str:
    if not bib_summary:
        return ""
    return f"Available citations (use [@citekey] format):\n{bib_summary}\n\n"


def _draft_paper(
    project_dir: Path,
    cfg: ProjectConfig,
    brain: Brain,
    paper_dir: Path,
    outline_text: str,
) -> None:
    litrev = load_litreview(project_dir, cfg.litrev_dir) if cfg.litrev_dir else ""
    code = load_methods(project_dir) if cfg.use_methods else ""
    results = load_results(project_dir, cfg.results_dir) if cfg.results_dir else ""
    bib_summary = load_bib_summary(project_dir, cfg.litrev_dir) if cfg.litrev_dir else ""
    bib_keys = load_bib_keys(project_dir, cfg.litrev_dir) if cfg.litrev_dir else set()
    style_profile = load_style_profile(project_dir) if cfg.use_style else ""
    narrative = load_onepager(project_dir, cfg.short_title)

    from .outline import _analyze_structure
    log("[raconteur] analysing paper structure…")
    analysis = _analyze_structure(brain, cfg.description, litrev, code, results, narrative)

    venue_section = _venue_block(cfg)
    bib_section = _bib_block(bib_summary)
    style_section = _style_block(style_profile)
    drafted: list[tuple[str, str]] = []

    for heading, section_outline in _parse_sections(outline_text):
        if _is_references(heading) or _is_abstract(heading):
            # References render at write time; the abstract is drafted last,
            # from the finished paper — the outline's Abstract states its brief.
            continue
        if _is_acknowledgements(heading):
            # Human-owned: the outline's CRediT role list passes through
            # verbatim for the author to assign. Never drafted — the tool
            # cannot know who contributed what.
            drafted.append((heading, _ack_passthrough(section_outline)))
            continue
        ctx = _context_for_section(heading, litrev, code, results)
        log(f"[raconteur] drafting '{heading}'…")
        text = brain.coordinator(
            _DRAFT_SECTION_PROMPT.format(
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
            ),
            system=_SYSTEM,
            num_ctx=16384,
        )
        text = _critique_revise(brain, heading, text, section_outline, analysis, 1)
        text = _critique_revise(brain, heading, text, section_outline, analysis, 2)
        text = _guard_repair(brain, heading, text, section_outline, bib_section,
                             bib_keys, bool(results))
        drafted.append((heading, text))
        log(f"[raconteur] section complete: {heading}")

    _ensure_acknowledgements(drafted)
    abstract = _draft_abstract(brain, cfg, venue_section, style_section, analysis)
    assembled = _assemble(cfg.title, abstract, drafted)
    _write(project_dir, cfg, paper_dir, assembled)
    log(f"[raconteur] {guards.metrics(assembled, bib_keys)}")


# ── user-annotation revision ──────────────────────────────────────────────────

def _revise_paper(
    project_dir: Path,
    cfg: ProjectConfig,
    brain: Brain,
    paper_dir: Path,
    user_rev: Path,
    outline_text: str,
) -> None:
    litrev = load_litreview(project_dir, cfg.litrev_dir) if cfg.litrev_dir else ""
    code = load_methods(project_dir) if cfg.use_methods else ""
    results = load_results(project_dir, cfg.results_dir) if cfg.results_dir else ""
    bib_summary = load_bib_summary(project_dir, cfg.litrev_dir) if cfg.litrev_dir else ""
    style_profile = load_style_profile(project_dir) if cfg.use_style else ""
    narrative = load_onepager(project_dir, cfg.short_title)

    from .outline import _analyze_structure
    log("[raconteur] analysing paper structure…")
    analysis = _analyze_structure(brain, cfg.description, litrev, code, results, narrative)

    existing_text = read_text(user_rev)
    annotations = build_revision_context(user_rev)
    if not annotations:
        log("[warn] no annotations in revision file — nothing to revise")
        return

    venue_section = _venue_block(cfg)
    bib_section = _bib_block(bib_summary)
    style_section = _style_block(style_profile)
    existing_map = dict(_parse_sections(existing_text))
    revised: list[tuple[str, str]] = []

    for heading, section_outline in _parse_sections(outline_text):
        if _is_references(heading) or _is_abstract(heading):
            continue
        if _is_acknowledgements(heading):
            # Keep whatever the author has filled in; fall back to the
            # outline's CRediT reference list if the section is still bare.
            revised.append((heading, existing_map.get(heading, "").strip()
                            or _ack_passthrough(section_outline)))
            continue
        existing = existing_map.get(heading, "")
        ctx = _context_for_section(heading, litrev, code, results)
        log(f"[raconteur] revising '{heading}'…")
        text = brain.coordinator(
            _REVISE_WITH_ANNOTATIONS_PROMPT.format(
                heading=heading,
                analysis=analysis,
                section_outline=section_outline,
                context_section=ctx,
                bib_section=bib_section,
                text=existing,
                annotations=annotations,
            ),
            system=_SYSTEM,
            num_ctx=16384,
        )
        text = _critique_revise(brain, heading, text, section_outline, analysis, 1)
        text = _critique_revise(brain, heading, text, section_outline, analysis, 2)
        revised.append((heading, text))
        log(f"[raconteur] section complete: {heading}")

    _ensure_acknowledgements(revised)
    abstract = _draft_abstract(brain, cfg, venue_section, style_section, analysis)
    _write(project_dir, cfg, paper_dir,
           _assemble(cfg.title, abstract, revised))


# ── entry point ───────────────────────────────────────────────────────────────

def _redline_paper(
    project_dir: Path,
    cfg: ProjectConfig,
    brain: Brain,
    paper_dir: Path,
    user_rev: Path,
) -> None:
    """Answer each anchored comment with an in-place tracked change on the reviewer's .docx.

    The default revise path. It edits a copy of the annotated document rather than
    regenerating markdown, so the reviewer sees a redline they can accept or reject, and the
    sections they approved are provably untouched.
    """
    from .redline_revise import redline_revise

    litrev = load_litreview(project_dir, cfg.litrev_dir) if cfg.litrev_dir else ""
    code = load_methods(project_dir) if cfg.use_methods else ""
    results = load_results(project_dir, cfg.results_dir) if cfg.results_dir else ""
    bib_summary = load_bib_summary(project_dir, cfg.litrev_dir) if cfg.litrev_dir else ""
    bib_keys = load_bib_keys(project_dir, cfg.litrev_dir) if cfg.litrev_dir else set()

    redline_revise(project_dir, cfg, brain, paper_dir, user_rev,
                   litrev, code, results, _bib_block(bib_summary), bib_keys)


def run(project_dir: Path, resynth: bool = False) -> None:
    if not ProjectConfig.exists(project_dir):
        log("[error] no paper/raconteur.yaml — run 'raconteur init' first")
        raise SystemExit(1)

    cfg = ProjectConfig.load(project_dir)
    gcfg = GlobalConfig.load()
    paper_dir = project_dir / "paper"
    paper_dir.mkdir(exist_ok=True)

    if not cfg.title or not cfg.topic:
        log("[error] no title/topic — run 'raconteur outline' first")
        raise SystemExit(1)

    brain = Brain(gcfg, coordinator=cfg.brain.coordinator_model)

    from haarpi.naming import find_latest_release
    outline_path = find_latest_release(
        paper_dir / "output", cfg.short_title, "md", chain_includes="outline",
    ) or find_latest(paper_dir, cfg.short_title, "md", last_initials="ra",
                     chain_includes="outline")
    if outline_path is None:
        log("[error] no outline found in paper/ — run 'raconteur outline' first")
        raise SystemExit(1)
    outline_text = outline_path.read_text(encoding="utf-8")
    log(f"[raconteur] using outline: {outline_path.name}")

    user_rev = find_user_revision(paper_dir, cfg.short_title,
                                  chain_excludes=["outline", "venue", "onepager"])
    existing = find_latest(paper_dir, cfg.short_title, "md", last_initials="ra",
                           chain_excludes=["outline", "venue", "onepager"])

    if not existing:
        _draft_paper(project_dir, cfg, brain, paper_dir, outline_text)
    elif user_rev:
        log(f"[raconteur] found revision: {user_rev.name}")
        if resynth:
            # Opt-in clean rewrite: regenerates the whole manuscript from markdown, which
            # discards the reviewer's comments and gives them no redline to read the edits
            # against. Major version — new datestamp, chain reset.
            log("[raconteur] --resynth: regenerating the whole draft (no redline)")
            _revise_paper(project_dir, cfg, brain, paper_dir, user_rev, outline_text)
        else:
            _redline_paper(project_dir, cfg, brain, paper_dir, user_rev)
    else:
        log("[raconteur] draft exists — annotate paper/*.docx with your initials and re-run")
        return

    from .notify import send_email
    send_email(
        f"raconteur paper done: {cfg.short_title}",
        f"Paper draft complete for '{cfg.title or cfg.short_title}'.\nProject: {project_dir}",
        gcfg,
    )

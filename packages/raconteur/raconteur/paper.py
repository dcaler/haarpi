from __future__ import annotations
from pathlib import Path

from .brain import Brain
from .config import ProjectConfig, GlobalConfig
from . import guards
from .guards import (
    LITREV_KW as _LITREV_KW,
    CODE_KW as _CODE_KW,
    RESULTS_KW as _RESULTS_KW,
    DISCUSSION_KW as _DISCUSSION_KW,
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
- Use ### for subsection headings and #### for sub-subsections, matching both the \
names AND the heading levels the outline uses — do not flatten a #### into a ###
- Each subsection should be {words_low}–{words_high} words of connected prose. This is derived from the venue's word limit and this section's share of it — the whole manuscript must fit, so a section that overruns is taking words from another
- For Methods sections: reference specific algorithms, functions, parameters, and \
equations from the source code above; do not use vague descriptions
- For Results sections: cite specific values, outcomes, and patterns from the results \
content above; do not describe anticipated findings
- Where the section outline names a figure — a line of the form \
"Figure N: <caption> (<path>)" — render it as a Markdown image `![Figure N: <caption>](<path>)` at \
the point in the prose where it belongs, using that EXACT caption and path. Render ONLY the \
figures THIS section's outline names: do not invent a figure or a path, do not add a figure \
the section outline did not name here, and never repeat one
- Keep the figure's number from the outline in its caption ("Figure 3: …") and introduce \
every figure in the prose BEFORE it appears ("Figure 3 shows …") — a figure no sentence \
points at is one the reader is never told to look at
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
5b. A figure the section outline names (a "Figure: … (<path>)" line) that is not \
rendered in the prose as `![…](path)` with the exact path, or an image whose path the \
section outline did not name here
6. Discussion that does not address the discussion_angle or limitations from the analysis
7. Subsections under {words_low} or over {words_high} words

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
- Preserve any figures (`![…](…)`) the section outline names; if the section outline names a \
figure the text lacks — a "Figure: <caption> (<path>)" line — add it as \
`![<caption>](<path>)` with that exact path. Remove any image whose path the section \
outline did not name here
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

# A Discussion connects results to background: the guards classify it "other" and demand a
# citation per paragraph, but the drafter used to hand it NEITHER the litreview NOR results —
# so the model wrote uncited prose or author-year from memory, and every Discussion drew a
# wall of uncited findings the repair rounds could not clear. It gets the litreview in full
# (the citations it was missing) plus a bounded results slice: the analysis already carries
# key_findings, so full litrev + full results would overrun the num_ctx budget for no gain.
_MAX_DISCUSSION_RESULTS_CHARS = 4000


def _context_for_section(heading: str, litrev: str, code: str, results: str) -> str:
    h = heading.lower()
    is_discussion = any(kw in h for kw in _DISCUSSION_KW)
    parts = []
    if (is_discussion or any(kw in h for kw in _LITREV_KW)) and litrev:
        parts.append(f"Literature review:\n{litrev}")
    if any(kw in h for kw in _CODE_KW) and code:
        parts.append(f"Methods (raster writeup):\n{code}")
    if any(kw in h for kw in _RESULTS_KW) and results:
        parts.append(f"Results Content:\n{results}")
    elif is_discussion and results:
        parts.append(f"Results Content:\n{results[:_MAX_DISCUSSION_RESULTS_CHARS]}")
    return ("\n\n".join(parts) + "\n\n") if parts else ""


def _venue_block(cfg: ProjectConfig, venue: str = "") -> str:
    """A manuscript is written FOR a venue. Which one is an argument, not a global."""
    from . import slate
    return slate.specs_block(cfg.venue(venue) if venue else None)


# The band a section is written to when the venue states no word limit. A venue that
# publishes no length is not a licence to assume one, so this is the old fixed pair kept
# as a floor — the derived band replaces it wherever a limit actually exists.
_DEFAULT_BAND = (150, 300)

# _is_references is imported from guards at the top of this module.


def _critique_revise(
    brain: Brain,
    heading: str,
    text: str,
    section_outline: str,
    analysis: str,
    n: int,
    band: tuple[int, int] = (0, 0),
) -> str:
    lo, hi = band if band and band[0] else _DEFAULT_BAND
    log(f"[raconteur] critique '{heading}' ({n})…")
    critique = brain.coordinator(
        _CRITIQUE_SECTION_PROMPT.format(
            heading=heading,
            analysis=analysis,
            section_outline=section_outline,
            text=text,
            words_low=lo,
            words_high=hi,
        ),
        system=_SYSTEM,
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
    text: str, heading: str, known: set[str], have_results: bool,
    expect_figures: int | None = None,
) -> list[guards.Finding]:
    """Draft-phase guards for one section.

    Wrapping the section body in its own heading lets ``parse_paragraphs`` classify it, so
    the citation floor is gated on section kind — a Methods paragraph is grounded in the
    writeup, not the bibliography.

    ``figure_findings`` ran only in the one-pager until now, which is how sixteen figures
    and four unnumbered captions shipped in a manuscript without a single warning. The
    outline numbers its figures; this is what checks the draft kept the number and wrote a
    sentence pointing at it.
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
    findings += guards.figure_findings(text, expect=expect_figures)
    return findings


def _outline_figure_count(section_outline: str) -> int:
    """How many figures THIS section's outline places. The draft must render exactly that
    many — no more (the flood) and no fewer (a figure the reader never sees)."""
    return len(guards.OUTLINE_FIGURE_RE.findall(section_outline))


def _guard_repair(
    brain: Brain,
    heading: str,
    text: str,
    section_outline: str,
    bib_section: str,
    known: set[str],
    have_results: bool,
    rounds: int = 2,
    expect_figures: int | None = None,
) -> str:
    """Feed mechanical findings back as imperatives until they clear or rounds run out.

    Python decides THAT something is wrong; the LLM decides how to fix it. A finding that
    survives every round is logged, not silently dropped.
    """
    for n in range(1, rounds + 1):
        findings = _guard_section(text, heading, known, have_results, expect_figures)
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
    remaining = _guard_section(text, heading, known, have_results, expect_figures)
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
    venue: str = "",
) -> str:
    v = cfg.venue(venue) if venue else None
    limit = str(v.abstract_limit) if v and v.abstract_limit else "150–250"
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


def _insert_authors(text: str, block: str) -> str:
    """Put the author block directly under the title.

    The manuscript arrives with its own "# Title" line, so this inserts rather than
    prepends — a block above the title is a block in the wrong place, and a manuscript
    with no title line gets it at the top, which is the only sensible fallback.
    """
    if not block:
        return text
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("# "):
            rest = lines[i + 1:]
            while rest and not rest[0].strip():
                rest.pop(0)
            return "\n".join(lines[:i + 1] + ["", block, ""] + rest)
    return f"{block}\n\n{text}"


def _write(project_dir: Path, cfg: ProjectConfig, paper_dir: Path, text: str,
           venue: str = "") -> None:
    from .context import load_authors_block
    v = cfg.venue(venue) if venue else None
    # A double-blind venue gets no block at all. This is the whole reason authorship is
    # data rather than prose: identity can be stripped on the venue's say-so, and prose
    # written by an earlier stage cannot be.
    anon = bool(v and v.anonymized)
    who = load_authors_block(project_dir, anonymized=anon)
    text = _insert_authors(text, who)
    # Checked after insertion, not during the repair loop: the block is rendered here, so
    # until this point the manuscript is legitimately without one.
    for f in guards.authorship(text, load_authors_block(project_dir), anonymized=anon):
        log(f"[warn] {f.kind} — {f.imperative}")
    out_path = paper_dir / major_name(cfg.short_title, "md", venue=venue)
    out_path.write_text(text, encoding="utf-8")
    log(f"[raconteur] wrote {out_path.relative_to(project_dir)}")
    bib_path = (project_dir / cfg.litrev_dir / "output" / "refs.bib") if cfg.litrev_dir else None
    # resource_path lets pandoc resolve project-relative figure paths (results/figures/x.png)
    # embedded in the prose — the .md lives in paper/, the figures do not.
    docx = to_docx(out_path, bib_path=bib_path, resource_path=project_dir)
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


_REBALANCE_PROMPT = """\
This manuscript does not yet fit the shape its venue and outline call for. Fix it.

{findings}

Manuscript:
{text}

The paper is currently {actual} words of prose against a target of {budget}.

Rules:
- Where a section must SHRINK: tighten prose — remove hedging, redundant restatement, and \
sentences that repeat what an adjacent sentence already says
- Where a section must GROW: develop what its outline bullets already promise — draw out \
the reasoning, state the mechanism, interpret the evidence already present. Never pad, \
never restate, never introduce new claims, values, or sources
- The total matters less than the shape: a section carrying the paper's contribution must \
not be the thinnest one in it
- Do NOT drop a [@citekey], a figure (`![…](…)`), a heading, or a subsection — the \
structure was approved by the author and the citations are what ground the claims
- Preserve every heading exactly as written, at the same level
- Output the complete revised manuscript and nothing else — no preamble
"""


def _prose_budget(cfg: ProjectConfig, venue: str, outline_text: str,
                  bib_keys: set[str], project_dir: Path | None = None) -> int:
    """The venue's whole-document limit, less what is not drafted prose.

    Shares the arithmetic with the outline stage on purpose: an outline planned against one
    budget and a draft written against another is how a structure that fits produces a
    manuscript that does not.

    Figures are counted from the OUTLINE, not from rayleigh's manifest. The outline is the
    placement authority and names each figure exactly once, so it knows what the paper will
    actually contain — a manifest figure the outline chose not to place costs nothing, and
    an author illustration the manifest never held still costs its page. Reloading the
    manifest here is also what flooded every section with every figure; see
    ``test_the_paper_stage_does_not_reload_the_figure_manifest``.
    """
    v = cfg.venue(venue) if venue else None
    if not v or not v.word_limit:
        return 0
    # The length to AIM AT. Where the venue states a range, deriving the budget from the
    # ceiling makes it a bound the draft can satisfy by being short — and it was: 3,552
    # words with Results at 17% of a 30% share, reported clean.
    target = guards.word_target(v.word_min, v.word_limit)
    placed = list(guards.OUTLINE_FIGURE_RE.finditer(outline_text))
    from .context import load_authors_block
    who = (load_authors_block(project_dir, anonymized=bool(v.anonymized))
           if project_dir is not None else "")
    return guards.prose_budget(
        target,
        guards.expected_references(target, len(bib_keys)),
        len(placed),
        sum(len((m.group("caption") or "").split()) for m in placed),
        front_matter_words=len(who.split()))


def _manuscript_findings(assembled: str, outline_text: str, budget: int,
                         shares: dict | None = None) -> list:
    """Everything checkable about the whole document at once.

    Length is checked in BOTH directions and per section, not just as a ceiling on the
    total. A sum under budget says nothing about shape: the css2026 draft came in at 3,552
    against 3,688 with Introduction and Background at 36% of a manuscript budgeted for 28%
    and Results at 17% of a 30% share — every whole-document check green, the paper about
    the wrong thing.
    """
    return (guards.outline_conformance(assembled, outline_text)
            + guards.over_budget(assembled, budget)
            + guards.under_budget(assembled, budget)
            + guards.section_lengths(assembled, outline_text, budget, shares))


def _whole_document_repair(brain: Brain, assembled: str, outline_text: str,
                           budget: int, bib_keys: set[str], rounds: int = 2,
                           shares: dict | None = None) -> str:
    """The checks no section can make about itself: did it follow the outline, and does the
    manuscript have the length and the shape the venue and the outline call for.

    Every section can sit inside its own band and the manuscript still overrun — that is
    exactly what happened, 19 legal subsections summing to 6,975 words against a 5,000-word
    cap, reported clean. Conformance is the same shape of blind spot: a section the outline
    never named is invisible to a guard that only ever reads one section at a time.
    """
    for n in range(1, rounds + 1):
        findings = _manuscript_findings(assembled, outline_text, budget, shares)
        if not findings:
            return assembled
        log(f"[raconteur] manuscript guards ({n}): {len(findings)} finding(s)")
        for f in findings:
            log(f"  · {f.kind} — {f.where}")
        assembled = brain.coordinator(
            _REBALANCE_PROMPT.format(
                findings="\n".join(f"- {f.imperative}" for f in findings),
                text=assembled,
                actual=guards.word_count(assembled),
                budget=budget),
            system=_SYSTEM,
            num_ctx=16384,
        )
    remaining = _manuscript_findings(assembled, outline_text, budget, shares)
    if remaining:
        log(f"[warn] {len(remaining)} manuscript finding(s) survived {rounds} round(s) "
            f"— shipping anyway:")
        for f in remaining:
            log(f"[warn]   · {f.kind} — {f.where}")
    return assembled


def _draft_paper(
    project_dir: Path,
    cfg: ProjectConfig,
    brain: Brain,
    paper_dir: Path,
    outline_text: str,
    venue: str = "",
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
    # Figures are placed by the (human-approved) outline itself — once each, in the Results
    # subsection they belong to. We deliberately do NOT feed the manifest into the per-section
    # analysis: key_figures there handed the full figure list to every section, and the model
    # then rendered all of them in every section. The outline is the sole placement authority.
    analysis = _analyze_structure(brain, cfg.description, litrev, code, results, narrative)

    venue_section = _venue_block(cfg, venue)
    bib_section = _bib_block(bib_summary)
    style_section = _style_block(style_profile)
    drafted: list[tuple[str, str]] = []

    # The venue's budget, apportioned the same way the outline apportioned it — one source
    # of arithmetic for both stages, or the outline plans a structure the draft then ignores.
    budget = _prose_budget(cfg, venue, outline_text, bib_keys, project_dir)
    leaf_counts = guards.section_leaf_counts(outline_text)

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
        band = guards.section_target(heading, budget, leaf_counts.get(heading, 1),
                                     cfg.section_shares or None)
        lo, hi = band if band[0] else _DEFAULT_BAND
        log(f"[raconteur] drafting '{heading}'… ({lo}–{hi} words per subsection)")
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
                words_low=lo,
                words_high=hi,
            ),
            system=_SYSTEM,
            num_ctx=16384,
        )
        text = _critique_revise(brain, heading, text, section_outline, analysis, 1, band)
        text = _critique_revise(brain, heading, text, section_outline, analysis, 2, band)
        text = _guard_repair(brain, heading, text, section_outline, bib_section,
                             bib_keys, bool(results), expect_figures=_outline_figure_count(
                                 section_outline))
        drafted.append((heading, text))
        log(f"[raconteur] section complete: {heading}")

    _ensure_acknowledgements(drafted)
    abstract = _draft_abstract(brain, cfg, venue_section, style_section, analysis,
                               venue=venue)
    assembled = _assemble(cfg.title, abstract, drafted)
    assembled = _whole_document_repair(brain, assembled, outline_text, budget, bib_keys,
                                       shares=cfg.section_shares or None)
    _write(project_dir, cfg, paper_dir, assembled, venue)
    log(f"[raconteur] {guards.metrics(assembled, bib_keys, budget)}")


# ── user-annotation revision ──────────────────────────────────────────────────

def _revise_paper(
    project_dir: Path,
    cfg: ProjectConfig,
    brain: Brain,
    paper_dir: Path,
    user_rev: Path,
    outline_text: str,
    venue: str = "",
) -> None:
    litrev = load_litreview(project_dir, cfg.litrev_dir) if cfg.litrev_dir else ""
    code = load_methods(project_dir) if cfg.use_methods else ""
    results = load_results(project_dir, cfg.results_dir) if cfg.results_dir else ""
    bib_summary = load_bib_summary(project_dir, cfg.litrev_dir) if cfg.litrev_dir else ""
    style_profile = load_style_profile(project_dir) if cfg.use_style else ""
    narrative = load_onepager(project_dir, cfg.short_title)

    from .outline import _analyze_structure
    log("[raconteur] analysing paper structure…")
    # Figures are placed by the (human-approved) outline itself — once each, in the Results
    # subsection they belong to. We deliberately do NOT feed the manifest into the per-section
    # analysis: key_figures there handed the full figure list to every section, and the model
    # then rendered all of them in every section. The outline is the sole placement authority.
    analysis = _analyze_structure(brain, cfg.description, litrev, code, results, narrative)

    existing_text = read_text(user_rev)
    annotations = build_revision_context(user_rev)
    if not annotations:
        log("[warn] no annotations in revision file — nothing to revise")
        return

    venue_section = _venue_block(cfg, venue)
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
    abstract = _draft_abstract(brain, cfg, venue_section, style_section, analysis,
                               venue=venue)
    _write(project_dir, cfg, paper_dir,
           _assemble(cfg.title, abstract, revised), venue)


# ── entry point ───────────────────────────────────────────────────────────────

def _redline_paper(
    project_dir: Path,
    cfg: ProjectConfig,
    brain: Brain,
    paper_dir: Path,
    user_rev: Path,
    venue: str = "",
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


def run(project_dir: Path, resynth: bool = False, venue: str = "") -> None:
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

    from . import slate
    venue = slate.resolve(cfg, venue)
    if venue:
        log(f"[raconteur] writing for {cfg.venues[venue].name} ({venue})")

    # Every binding is scoped to this venue: its outline, its markup, its draft. The JASSS
    # paper sits beside the ISMIR one and neither can see the other's redline.
    scope = [venue] if venue else []
    others = [v for v in cfg.venues if v != venue]

    from haarpi.naming import find_latest_release
    outline_path = find_latest_release(
        paper_dir / "output", cfg.short_title, "md",
        chain_includes=scope + ["outline"],
    ) or find_latest(paper_dir, cfg.short_title, "md", last_initials="ra",
                     chain_includes=scope + ["outline"])
    if outline_path is None:
        where = f" for {venue}" if venue else ""
        log(f"[error] no outline{where} found in paper/ — run 'raconteur outline"
            + (f" --venue {venue}'" if venue else "'") + " first")
        raise SystemExit(1)
    outline_text = outline_path.read_text(encoding="utf-8")
    log(f"[raconteur] using outline: {outline_path.name}")

    excludes = ["outline", "venue", "onepager"] + others
    user_rev = find_user_revision(paper_dir, cfg.short_title,
                                  chain_includes=scope, chain_excludes=excludes)
    existing = find_latest(paper_dir, cfg.short_title, "md", last_initials="ra",
                           chain_includes=scope, chain_excludes=excludes)

    if not existing:
        _draft_paper(project_dir, cfg, brain, paper_dir, outline_text, venue)
    elif user_rev:
        log(f"[raconteur] found revision: {user_rev.name}")
        if resynth:
            # Opt-in clean rewrite: regenerates the whole manuscript from markdown, which
            # discards the reviewer's comments and gives them no redline to read the edits
            # against. Major version — new datestamp, chain reset.
            log("[raconteur] --resynth: regenerating the whole draft (no redline)")
            _revise_paper(project_dir, cfg, brain, paper_dir, user_rev, outline_text,
                          venue)
        else:
            _redline_paper(project_dir, cfg, brain, paper_dir, user_rev, venue)
    else:
        # DECLINED, not done. Exiting 0 here told trundlr the run succeeded: the task closed
        # green in 26 seconds, the ladder advanced, and a human gate was scheduled against a
        # draft that had never been rewritten. A stage that does no work must not report the
        # same status as a stage that did.
        log("[raconteur] draft exists — annotate paper/*.docx with your initials and re-run")
        log("[error] nothing to do: this run made no changes (exit 3)")
        raise SystemExit(3)


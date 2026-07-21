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
from .naming import major_name, find_latest, find_user_revision, deliverable_dir
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
- This WHOLE section must come to {words_low}–{words_high} words of connected prose, \
across all its subsections together — not each. It is this section's share of the venue's \
limit, so a section that overruns is taking words from another
- Write ONE PARAGRAPH per outline bullet, in the bullet's order. A bullet is a paragraph's \
worth of argument — so do not merge two bullets into one paragraph, and do not spread one \
bullet across three
- EVERY paragraph must be {para_low}–{para_high} words. This is a hard bound and it is not \
in tension with the section total: the section's words are the bullet count times \
{para_words}, so writing one paragraph per bullet at this length lands the section on its \
number automatically. If a paragraph is running long you are arguing something the outline \
did not ask this bullet to argue — cut it, do not let the paragraph grow
- For Methods sections: reference specific algorithms, functions, parameters, and \
equations from the source code above; do not use vague descriptions
- For Results sections: cite specific values, outcomes, and patterns from the results \
content above; do not describe anticipated findings
- Where the section outline names a figure — a line of the form \
"Figure: <caption> (<path>)" — render it as a Markdown image `![Figure N: <caption>](<path>)` at \
the point in the prose where it belongs, using that EXACT caption and path. Render ONLY the \
figures THIS section's outline names: do not invent a figure or a path, do not add a figure \
the section outline did not name here, and never repeat one
- NUMBER THE FIGURES FROM {figure_start}. Figures are numbered across the whole paper, not \
within a section, and earlier sections have already used every number below {figure_start}. \
This section's first figure is Figure {figure_start}, its second is Figure {figure_start} + 1, \
and so on. Do not start at 1
- Introduce every figure in the prose BEFORE it appears ("Figure {figure_start} shows …"), \
using the same number as its caption — a figure no sentence points at is one the reader is \
never told to look at, and a sentence pointing at the wrong number sends them to someone \
else's plot
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
7. The section as a whole (all subsections together) under {words_low} or over \
{words_high} words
7b. A paragraph count that does not match the outline's bullet count for a subsection — \
one bullet is one paragraph
7c. ANY paragraph outside {para_low}–{para_high} words. Report each one by its opening \
words. A paragraph over {para_high} is the most common defect in this pipeline and the \
one most often missed — count before you answer

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


# The order sections are WRITTEN in, which is not the order they are read in. A section
# that interprets or restates should see the thing it is interpreting, in the words the
# paper actually used — not a second-hand distillation of the same source material.
#
#   Background    <- the literature
#   Methods       <- the methods writeup (+ the literature, for the method's provenance)
#   Results       <- the results digest and the figures
#   Discussion    <- the literature + Results AS WRITTEN
#   Conclusion    <- Results + Discussion AS WRITTEN
#   Introduction  <- the narrative's motivation + the analysis + Conclusion AS WRITTEN
#
# The Introduction previews the result last, from the conclusion the paper actually
# reached. Written first, it previewed findings it had never seen.
WRITE_ORDER = ("litrev", "methods", "results", "other", "conclusion", "intro")

# What each kind is handed from the sections already written.
_FEEDS: dict[str, tuple[str, ...]] = {
    "other":      ("results",),
    "conclusion": ("results", "other"),
    "intro":      ("conclusion",),
}

_KIND_LABEL = {"litrev": "Background", "methods": "Methods", "results": "Results",
               "other": "Discussion", "conclusion": "Conclusion", "intro": "Introduction"}


def _writes_as(heading: str) -> str:
    """The kind that decides WHEN a heading is written.

    ``budget_kind`` falls through to "other" for anything it does not recognise, which
    makes a venue-mandated "Data Availability" indistinguishable from a Discussion — and a
    Discussion is fed the Results as written. A section this codebase has never heard of
    should not inherit a Discussion's dependencies on a keyword miss.
    """
    kind = guards.budget_kind(heading)
    if kind == "other" and not any(kw in heading.lower() for kw in _DISCUSSION_KW):
        return ""
    return kind


def write_order(headings: list[str]) -> list[str]:
    """Document headings, re-ordered for writing.

    A heading this codebase does not recognise is written LAST and depends on nothing —
    predictable, and it cannot pick up another section's inputs by accident.
    """
    rank = {k: i for i, k in enumerate(WRITE_ORDER)}
    return sorted(headings,
                  key=lambda h: (rank.get(_writes_as(h), len(WRITE_ORDER)),
                                 headings.index(h)))


def _context_for_section(heading: str, litrev: str, code: str, results: str,
                         written: dict[str, str] | None = None,
                         narrative: str = "") -> str:
    kind = _writes_as(heading)
    parts = []

    if kind in ("litrev", "intro", "other") and litrev:
        parts.append(f"Literature review:\n{litrev}")
    elif kind == "methods" and litrev:
        # Methods may cite where the method descends from prior work — an offshoot has a
        # provenance, and it belongs in the text. It carries no citation FLOOR: citing is
        # not this section's job, so an uncited paragraph here is not a defect.
        parts.append("Literature review (cite ONLY where this project's method derives "
                     f"from prior work; there is no requirement to cite here):\n{litrev}")

    if kind == "methods" and code:
        parts.append(f"Methods (raster writeup):\n{code}")
    if kind == "results" and results:
        parts.append(f"Results Content:\n{results}")

    if kind == "intro" and narrative:
        parts.append(f"Narrative spine — the motivation, in the author's framing:\n{narrative}")

    for dep in _FEEDS.get(kind, ()):
        text = (written or {}).get(dep, "").strip()
        if text:
            parts.append(
                f"{_KIND_LABEL[dep]} AS WRITTEN — this is the paper's own text. Refer to "
                f"it, restate from it, and do not contradict it:\n{text}")
    return ("\n\n".join(parts) + "\n\n") if parts else ""


def _venue_block(cfg: ProjectConfig, venue: str = "") -> str:
    """A manuscript is written FOR a venue. Which one is an argument, not a global."""
    from . import slate
    return slate.specs_block(cfg.venue(venue) if venue else None)


# The band a SECTION is written to when the venue states no word limit. A venue that
# publishes no length is not a licence to assume one, so this stays a loose fallback — but
# it is a section band now, not a per-subsection one. Left at per-subsection scale it told
# a three-subsection Methods to write 150-300 words in total.
_DEFAULT_BAND = (450, 900)

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
            para_low=guards.PARAGRAPH_BAND[0],
            para_high=guards.PARAGRAPH_BAND[1],
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
    band: tuple[int, int] | None = None,
    bullets: int = 0,
    figure_start: int = 1,
) -> list[guards.Finding]:
    """Draft-phase guards for one section.

    Wrapping the section body in its own heading lets ``parse_paragraphs`` classify it, so
    the citation floor is gated on section kind — a Methods paragraph is grounded in the
    writeup, not the bibliography.

    ``figure_findings`` ran only in the one-pager until now, which is how sixteen figures
    and four unnumbered captions shipped in a manuscript without a single warning. The
    outline numbers its figures; this is what checks the draft kept the number and wrote a
    sentence pointing at it.

    ``band`` and ``bullets`` are the section's SHAPE, and they belong here rather than only
    in the whole-document pass. A section is repaired against its own outline in two cheap
    rounds; the manuscript is repaired once every section already exists, when the same
    correction is a larger and worse-posed rewrite. Length was checked only in the second
    place, so css2026's Background reached 1,046 words against a 480-720 band having passed
    every round of the loop that could have caught it cheaply — that loop was never told
    what the section was supposed to weigh.
    """
    md = f"## {heading}\n\n{text}"
    paras = guards.parse_paragraphs(md)
    findings: list[guards.Finding] = []
    if known:  # an empty bib would make every key "unresolved"
        findings += guards.unresolved_keys(text, known)
    findings += guards.author_year_prose(text)
    findings += guards.uncited_paragraphs(paras)
    findings += guards.sparse_paragraphs(paras)
    findings += guards.padded_citations(paras)
    findings += guards.wide_paragraphs(paras)
    if band:
        findings += guards.section_size(heading, text, band)
    findings += guards.paragraph_count(heading, text, bullets)
    if have_results:
        findings += guards.unnumbered_results_paragraphs(paras)
    findings += guards.figure_findings(text, expect=expect_figures, start=figure_start)
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
    band: tuple[int, int] | None = None,
    bullets: int = 0,
    figure_start: int = 1,
) -> str:
    """Feed mechanical findings back as imperatives until they clear or rounds run out.

    Python decides THAT something is wrong; the LLM decides how to fix it. A finding that
    survives every round is logged, not silently dropped.
    """
    kw = dict(expect_figures=expect_figures, band=band, bullets=bullets,
              figure_start=figure_start)
    for n in range(1, rounds + 1):
        findings = _guard_section(text, heading, known, have_results, **kw)
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
    remaining = _guard_section(text, heading, known, have_results, **kw)
    if remaining:
        log(f"[warn] '{heading}': {len(remaining)} guard finding(s) survived "
            f"{rounds} repair round(s) — shipping anyway:")
        for f in remaining:
            log(f"[warn]   · {f.kind} — {f.where}")
    return text


def _abstract_limit(cfg: ProjectConfig, venue: str = "") -> int | None:
    """The venue's abstract limit, or None where it states none."""
    v = cfg.venue(venue) if venue else None
    return v.abstract_limit if v else None


def _draft_abstract(
    brain: Brain,
    cfg: ProjectConfig,
    venue_section: str,
    style_section: str,
    analysis: str,
    venue: str = "",
) -> str:
    v = cfg.venue(venue) if venue else None
    # The venue's own limit wins; 225 is the fallback, and the abstract is not charged to
    # the body budget either way — no venue counts it against the paper's length.
    limit = str(guards.abstract_words(_abstract_limit(cfg, venue)))
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


def _ack_passthrough(section_outline: str, project_dir: Path | None = None) -> str:
    """The outline's CRediT statement, verbatim — or a skeleton of it from the author list.

    Never drafted: the tool cannot know who contributed what, and a plausible guess at
    authorship credit is worse than a blank. When the outline carries nothing, the recorded
    authors are emitted with empty role slots for the author to fill, rather than the bare
    taxonomy — the names are in the manifest and re-typing them is how they used to drift.
    """
    text = section_outline.strip()
    if text:
        return text
    people = []
    if project_dir is not None:
        try:
            from haarpi import project as hproject
            root = hproject.find_root(project_dir)
            if root is not None:
                people = hproject.authors(hproject.load_manifest(root))
        except Exception:                      # noqa: BLE001 — a manifest must not fail a draft
            people = []
    if people:
        return ("CRediT authorship contribution statement\n\n"
                + "\n\n".join(f"{a['name']}: " for a in people))
    from .outline import _CREDIT_ROLES
    return "\n".join(f"- {r}" for r in _CREDIT_ROLES)


def _ensure_acknowledgements(sections: list[tuple[str, str]],
                             project_dir: Path | None = None) -> None:
    """Outlines minted before the Acknowledgements rule lack the section; the
    paper must still carry the CRediT reference list for the author."""
    if not any(_is_acknowledgements(h) for h, _ in sections):
        sections.append(("Acknowledgements", _ack_passthrough("", project_dir)))


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
    from .refdoc import render as _render_docx
    docx = _render_docx(out_path, project_dir, bib_path=bib_path,
                        resource_path=project_dir)
    if docx:
        log(f"[raconteur] wrote {docx.relative_to(project_dir)}")


# ── fresh paper draft ─────────────────────────────────────────────────────────

def _style_block(style_profile: str, style_note: str = "") -> str:
    """The author's measured voice, and the register THIS paper wants within it.

    The note goes LAST and is stated as the tie-breaker, because it is the more specific
    instruction and the profile is long enough to drown it. A profile trained across an
    author's whole corpus describes their mean register; a given paper is rarely written at
    the mean, and nothing else in the pipeline can say so.
    """
    if not style_profile and not style_note:
        return ""
    out = ""
    if style_profile:
        out += f"Writing style guidance (match this author's voice):\n{style_profile}\n\n"
    if style_note:
        out += ("Register for THIS paper — where it and the voice guidance above pull in "
                f"different directions, this wins:\n{style_note}\n\n")
    return out


def _bib_block(bib_summary: str) -> str:
    if not bib_summary:
        return ""
    return f"Available citations (use [@citekey] format):\n{bib_summary}\n\n"


def _prose_budget(cfg: ProjectConfig, venue: str, outline_text: str = "",
                  bib_keys: set[str] | None = None) -> int:
    """The BODY words this venue's manuscript should come out at.

    Shares the arithmetic with the outline stage on purpose: an outline planned against one
    budget and a draft written against another is how a structure that fits produces a
    manuscript that does not.

    Nothing is deducted. Section labels, figure captions and [@citekey] tags are not
    counted as prose; the bibliography is rendered by pandoc at write time and never
    appears in the markdown being measured; the abstract, the title and the author block
    sit outside the body. See ``guards.prose_budget``.
    """
    v = cfg.venue(venue) if venue else None
    if not v or not v.word_limit:
        return 0
    # The length to AIM AT, not the venue's ceiling: a budget derived from a maximum is
    # satisfied by any short paper, and it was — 3,552 words with Results at 17% of its
    # share, every whole-document check green.
    return guards.prose_budget(guards.word_target(v.word_min, v.word_limit))


def _manuscript_findings(assembled: str, outline_text: str, budget: int,
                         shares: dict | None = None,
                         abstract_limit: int | None = None) -> list:
    """Everything checkable about the whole document at once.

    Length is checked in BOTH directions and per section, not just as a ceiling on the
    total. A sum under budget says nothing about shape: the css2026 draft came in at 3,552
    against 3,688 with Introduction and Background at 36% of a manuscript budgeted for 28%
    and Results at 17% of a 30% share — every whole-document check green, the paper about
    the wrong thing.

    Three of these can only be seen from up here. Figures are numbered across the document
    but drafted section by section, so a per-section check counting from 1 called two Figure
    1s correct. A sentence is only an echo relative to the OTHER section that also has it.
    And the abstract is written last, by its own prompt, outside every section band.
    """
    return (guards.outline_conformance(assembled, outline_text)
            + guards.over_budget(assembled, budget)
            + guards.under_budget(assembled, budget)
            + guards.section_lengths(assembled, outline_text, budget, shares)
            + guards.paragraph_conformance(assembled, outline_text)
            + guards.figure_findings(assembled)
            + guards.abstract_length(assembled, abstract_limit)
            + guards.echoed_sentences(assembled))


_SECTION_REPAIR_PROMPT = """\
Revise ONE section of a paper to fix the problems listed. Change nothing else.

Section: {heading}

Its outline (the approved contract — every subsection heading stays, in this order):
{section_outline}

Current text:
{text}

Problems to fix:
{findings}

Rules:
- This section must come to {words_low}–{words_high} words in total, across all its \
subsections together
- One outline bullet is one paragraph, and every paragraph must be {para_low}–{para_high} \
words. To shorten, tighten prose — remove hedging, redundant restatement, sentences that \
repeat an adjacent one. To lengthen, develop what the bullets already promise: draw out \
the reasoning, state the mechanism, interpret the evidence already present
- A paragraph over {para_high} words is SPLIT, not trimmed, only when it is carrying two \
bullets' worth of argument; otherwise cut it back to length
- Never pad, never restate, never introduce a new claim, value, or source
- Do NOT drop a [@citekey], a figure (`![…](…)`), or a subsection heading
- Do NOT add or rename a subsection heading
- Output only this section's revised text — no ## heading, no preamble
"""


def _findings_by_section(findings: list, headings: list[str]) -> dict[str, list]:
    """Route each finding to the section that can fix it.

    Whole-document findings (over- and under-budget) are not fixed by rewriting the whole
    document: the total is wrong BECAUSE some section is, so they belong to whichever
    sections are already out of band. When the total is off and every section is inside its
    band, the arithmetic disagrees with itself and there is nothing to route.
    """
    by: dict[str, list] = {}
    for f in findings:
        if f.where in headings:
            by.setdefault(f.where, []).append(f)
    return by


def _whole_document_repair(brain: Brain, assembled: str, outline_text: str,
                           budget: int, bib_keys: set[str], rounds: int = 2,
                           shares: dict | None = None,
                           abstract_limit: int | None = None) -> str:
    """Fix what the whole-document checks find, SECTION BY SECTION.

    Every section can sit inside its own band and the manuscript still overrun — that is
    exactly what happened, 19 legal subsections summing to 6,975 words against a 5,000-word
    cap, reported clean. So the checks have to see the whole document.

    The REPAIR does not. It used to rewrite the entire manuscript in one call: 4,000 words
    in and 4,000 out, twice, with every section re-emitted and free to drift while the model
    attended to one. Twice now the findings fired and then survived both rounds. Rewriting
    only the sections that are actually wrong is a smaller, better-posed problem, and it
    cannot damage a section that was already right.
    """
    outline_sections = dict(_parse_sections(outline_text))

    for n in range(1, rounds + 1):
        findings = _manuscript_findings(assembled, outline_text, budget, shares,
                                        abstract_limit)
        if not findings:
            return assembled
        log(f"[raconteur] manuscript guards ({n}): {len(findings)} finding(s)")
        for f in findings:
            log(f"  · {f.kind} — {f.where}")

        parts = _parse_sections(assembled)
        by_section = _findings_by_section(findings, [h for h, _ in parts])
        if not by_section:
            # Nothing routable — a conformance or arithmetic finding no single section owns.
            log("[warn] no section owns these findings; leaving the manuscript alone "
                "rather than rewriting it wholesale")
            break

        head, _, _ = assembled.partition("\n## ")
        repaired: list[tuple[str, str]] = []
        for heading, text in parts:
            mine = by_section.get(heading)
            if not mine:
                repaired.append((heading, text))
                continue
            lo, hi = guards.section_band(heading, outline_text, budget, shares)
            if not lo:
                lo, hi = _DEFAULT_BAND
            log(f"[raconteur] repairing '{heading}' ({len(mine)} finding(s))")
            repaired.append((heading, brain.coordinator(
                _SECTION_REPAIR_PROMPT.format(
                    heading=heading,
                    section_outline=outline_sections.get(heading, ""),
                    text=text,
                    findings="\n".join(f"- {f.imperative}" for f in mine),
                    words_low=lo, words_high=hi,
                    para_low=guards.PARAGRAPH_BAND[0],
                    para_high=guards.PARAGRAPH_BAND[1]),
                system=_SYSTEM, num_ctx=16384)))
        assembled = head + "\n" + "\n".join(
            f"## {h}\n\n{t.strip()}\n" for h, t in repaired)

    remaining = _manuscript_findings(assembled, outline_text, budget, shares,
                                     abstract_limit)
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
    style_section = _style_block(style_profile, cfg.style_note)
    drafted: list[tuple[str, str]] = []

    # The venue's budget, apportioned the same way the outline apportioned it — one source
    # of arithmetic for both stages, or the outline plans a structure the draft then ignores.
    budget = _prose_budget(cfg, venue, outline_text, bib_keys)

    # Sections are WRITTEN in dependency order and ASSEMBLED in document order. A
    # Conclusion that has read the Results and Discussion restates them; one that has read
    # only the same digests re-derives them, at three times its length. See WRITE_ORDER.
    sections = dict(_parse_sections(outline_text))
    written: dict[str, str] = {}
    order = write_order(list(sections))
    log("[raconteur] writing order: "
        + " → ".join(h for h in order
                     if not (_is_references(h) or _is_abstract(h)
                             or _is_acknowledgements(h))))

    # Figures are numbered across the DOCUMENT but drafted one section at a time, and
    # `write_order` is not document order — so each section has to be told the number its
    # first figure carries. Taken from the outline, in the order a reader meets them.
    figure_start: dict[str, int] = {}
    running = 1
    for heading in sections:
        figure_start[heading] = running
        running += _outline_figure_count(sections[heading])

    for heading in order:
        section_outline = sections[heading]
        if _is_references(heading) or _is_abstract(heading):
            # References render at write time; the abstract is drafted last,
            # from the finished paper — the outline's Abstract states its brief.
            continue
        if _is_acknowledgements(heading):
            # Human-owned: the outline's CRediT role list passes through
            # verbatim for the author to assign. Never drafted — the tool
            # cannot know who contributed what.
            drafted.append((heading, _ack_passthrough(section_outline, project_dir)))
            continue
        ctx = _context_for_section(heading, litrev, code, results, written, narrative)
        band = guards.section_band(heading, outline_text, budget,
                                   cfg.section_shares or None)
        lo, hi = band if band[0] else _DEFAULT_BAND
        n_bullets = guards.section_bullets(outline_text).get(
            guards._norm_heading(heading), 0)
        fig_start = figure_start.get(heading, 1)
        log(f"[raconteur] drafting '{heading}'… ({lo}–{hi} words "
            f"across {n_bullets} paragraph(s))")
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
                para_low=guards.PARAGRAPH_BAND[0],
                para_high=guards.PARAGRAPH_BAND[1],
                para_words=guards.WORDS_PER_PARAGRAPH,
                figure_start=fig_start,
            ),
            system=_SYSTEM,
            num_ctx=16384,
        )
        text = _critique_revise(brain, heading, text, section_outline, analysis, 1, band)
        text = _critique_revise(brain, heading, text, section_outline, analysis, 2, band)
        text = _guard_repair(brain, heading, text, section_outline, bib_section,
                             bib_keys, bool(results),
                             expect_figures=_outline_figure_count(section_outline),
                             band=band if band[0] else None,
                             bullets=n_bullets,
                             figure_start=fig_start)
        drafted.append((heading, text))
        written[guards.budget_kind(heading)] = text
        log(f"[raconteur] section complete: {heading}")

    # Back into the order a reader meets them in.
    doc_order = {h: i for i, h in enumerate(sections)}
    drafted.sort(key=lambda hv: doc_order.get(hv[0], len(doc_order)))
    _ensure_acknowledgements(drafted, project_dir)
    abstract = _draft_abstract(brain, cfg, venue_section, style_section, analysis,
                               venue=venue)
    abstract_limit = _abstract_limit(cfg, venue)
    assembled = _assemble(cfg.title, abstract, drafted)
    assembled = _whole_document_repair(brain, assembled, outline_text, budget, bib_keys,
                                       shares=cfg.section_shares or None,
                                       abstract_limit=abstract_limit)
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
    style_section = _style_block(style_profile, cfg.style_note)
    existing_map = dict(_parse_sections(existing_text))
    revised: list[tuple[str, str]] = []
    bib_keys = load_bib_keys(project_dir, cfg.litrev_dir) if cfg.litrev_dir else set()
    budget = _prose_budget(cfg, venue)

    # The same dependency order as a fresh draft: a Conclusion revised without seeing the
    # revised Results restates a version of the paper that no longer exists.
    sections = dict(_parse_sections(outline_text))
    written: dict[str, str] = {}

    for heading in write_order(list(sections)):
        section_outline = sections[heading]
        if _is_references(heading) or _is_abstract(heading):
            continue
        if _is_acknowledgements(heading):
            # Keep whatever the author has filled in; fall back to the recorded authors
            # if the section is still bare.
            revised.append((heading, existing_map.get(heading, "").strip()
                            or _ack_passthrough(section_outline, project_dir)))
            continue
        existing = existing_map.get(heading, "")
        ctx = _context_for_section(heading, litrev, code, results, written, narrative)
        band = guards.section_band(heading, outline_text, budget,
                                   cfg.section_shares or None)
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
        text = _critique_revise(brain, heading, text, section_outline, analysis, 1, band)
        text = _critique_revise(brain, heading, text, section_outline, analysis, 2, band)
        text = _guard_repair(brain, heading, text, section_outline, bib_section,
                             bib_keys, bool(results),
                             expect_figures=_outline_figure_count(section_outline))
        revised.append((heading, text))
        written[guards.budget_kind(heading)] = text
        log(f"[raconteur] section complete: {heading}")

    doc_order = {h: i for i, h in enumerate(sections)}
    revised.sort(key=lambda hv: doc_order.get(hv[0], len(doc_order)))

    _ensure_acknowledgements(revised, project_dir)
    abstract = _draft_abstract(brain, cfg, venue_section, style_section, analysis,
                               venue=venue)
    assembled = _assemble(cfg.title, abstract, revised)

    # MEASURED, not repaired. The fresh path hands these findings to a whole-document
    # rewrite; here the manuscript has just incorporated the author's annotations, and a
    # wholesale rewrite is exactly how those get undone. The author is told what drifted
    # and decides — the same reason accepting and resolving are human-only.
    findings = _manuscript_findings(assembled, outline_text, budget,
                                    cfg.section_shares or None,
                                    _abstract_limit(cfg, venue))
    if findings:
        log(f"[warn] {len(findings)} manuscript finding(s) after revision — NOT repaired "
            f"automatically, so your annotations are not rewritten:")
        for f in findings:
            log(f"[warn]   · {f.kind} — {f.where}")

    _write(project_dir, cfg, paper_dir, assembled, venue)
    log(f"[raconteur] {guards.metrics(assembled, bib_keys, budget)}")


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

    # One folder per deliverable per venue: this venue's manuscript, and the outline it is
    # written from. The chain tokens still scope the search inside them — belt and braces,
    # and the filenames stay self-describing when read outside their folder.
    work = deliverable_dir(paper_dir, "manuscript", venue)
    work.mkdir(parents=True, exist_ok=True)
    outline_home = deliverable_dir(paper_dir, "outline", venue)

    from haarpi.naming import find_latest_release
    outline_path = find_latest_release(
        outline_home / "output", cfg.short_title, "md",
        chain_includes=scope + ["outline"],
    ) or find_latest(outline_home, cfg.short_title, "md", last_initials="ra",
                     chain_includes=scope + ["outline"])
    if outline_path is None:
        where = f" for {venue}" if venue else ""
        log(f"[error] no outline{where} found in {outline_home.relative_to(project_dir)}/ "
            f"— run 'raconteur outline"
            + (f" --venue {venue}'" if venue else "'") + " first")
        raise SystemExit(1)
    outline_text = outline_path.read_text(encoding="utf-8")
    log(f"[raconteur] using outline: {outline_path.name}")

    excludes = ["outline", "venue", "onepager"] + others
    user_rev = find_user_revision(work, cfg.short_title,
                                  chain_includes=scope, chain_excludes=excludes)
    existing = find_latest(work, cfg.short_title, "md", last_initials="ra",
                           chain_includes=scope, chain_excludes=excludes)
    paper_dir = work

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


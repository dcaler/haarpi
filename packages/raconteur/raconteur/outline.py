from __future__ import annotations
import json
import re
import sys
from .log import log
from pathlib import Path
from .brain import Brain
from .config import ProjectConfig, GlobalConfig
from .context import (
    load_litreview, load_methods, load_results, load_venue_analysis,
    check_prerequisites, load_onepager, load_figure_manifest,
    load_author_figures, author_figure_sections,
)
from .naming import (
    major_name, major_outline_name, find_latest, find_user_revision, deliverable_dir,
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

# CRediT contributor-role taxonomy (credit.niso.org). Held here as data, not written into
# the outline: the outline's Acknowledgements is an empty heading, and the contribution
# statement belongs at the paper stage where the paper is. Asking the outline for it bought
# fourteen role bullets with both authors' names attached to every one — in defiance of a
# prompt that said "do not assign any role" — and the tool cannot know who did what.
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

def _bullets_by_heading(outline_md: str) -> dict[str, list[str]]:
    """heading -> its bullet lines, keyed by normalised heading text."""
    from . import guards
    out: dict[str, list[str]] = {}
    current = None
    for raw in outline_md.splitlines():
        st = raw.lstrip()
        if st.startswith("#"):
            lvl = len(st) - len(st.lstrip("#"))
            text = st[lvl:].strip()
            current = guards._norm_heading(text) if text and lvl >= 2 else None
            if current is not None:
                out.setdefault(current, [])
            continue
        if current is not None and st:
            out[current].append(raw.rstrip())
    return out


def _canonical_figure(caption: str, path: str) -> str:
    """The one form a figure is written in: ``[[Figure: <caption> (<path>)]]``."""
    return f"[[Figure: {caption} ({path})]]" if caption else f"[[Figure: ({path})]]"


def normalise_figures(markdown: str, figures: dict[str, list]) -> tuple[str, list[str]]:
    """Rewrite every figure reference into the one accepted form, captions from the manifest.

    THE MODEL DECIDES ONE THING ABOUT A FIGURE: which bullet it hangs on. The caption and
    the path are data — rayleigh wrote one and produced the other — so neither is worth a
    token of inference, and both were being got wrong.

    Two failures this removes, seen together in the same outline. The model attached all
    five figures to exactly the right bullets but wrote them bare, as ``Figure: … (path)``;
    ``FIGURE_APPENDED_RE`` requires the brackets, so ``figure_variance`` reported all five
    unplaced, ``_recount`` was asked to place them again, and its properly-bracketed answers
    landed on the parent H2 — five duplicate beats, ten references for five figures, and a
    word plan inflated to 5,410 against a 5,000 ceiling. And because the per-subsection plan
    lists figures by path alone, those duplicates carried the FILENAME where the caption
    belongs.

    Matching is driven by the KNOWN PATHS, not by a pattern for "something that looks like a
    figure": a caption is prose and prose contains brackets and parentheses. Anything left
    that names a figure at a path this paper does not have is returned as a fault, because
    the alternative is a figure reference nobody can resolve reaching the draft.

    Returns (markdown, unresolved references).
    """
    from . import guards
    known = {f.path: (f.caption or "") for figs in figures.values() for f in figs}
    for path, caption in known.items():
        # Both forms, with or without a number, backticked or not — collapsed to one.
        # Anchored on the PATH, and the span may not cross a "]]" or a second "Figure":
        # a caption is prose, and this project's captions carry parentheses of their own
        # ("Fast freeze (small radius / high tolerance) vs. …"), so a pattern that tried to
        # describe a caption would either stop inside one or swallow the next figure whole.
        ref = re.compile(
            r"(?:\[\[[ \t]*)?Figure[ \t]*\d*[ \t]*[:.]?[ \t]*"
            r"(?:(?!\]\]|\bFigure\b).)*?\([ \t]*`?"
            + re.escape(path) + r"`?[ \t]*\)(?:[ \t]*\]\])?",
            re.IGNORECASE)
        markdown = ref.sub(_canonical_figure(caption, path).replace("\\", "\\\\"), markdown)
    unresolved: list[str] = []
    for m in guards.FIGURE_APPENDED_RE.finditer(markdown):
        if m.group("path").strip() not in known:
            unresolved.append(m.group("path").strip())
    for m in guards.OUTLINE_FIGURE_RE.finditer(markdown):
        if m.group("path").strip() not in known:
            unresolved.append(m.group("path").strip())
    return markdown, unresolved


def _add_ghosts(faults: dict[str, list[str]], ghosts: list[str]) -> None:
    """A figure reference at a path this paper does not have, folded in with the rest.

    It converges or blocks like any other figure fault: a path nobody can resolve reaching
    the draft becomes a missing image in the manuscript, found at render."""
    for path in dict.fromkeys(ghosts):
        faults.setdefault("", []).append(
            f"{path}: this paper has no figure at that path — remove the reference")


def figure_variance(outline_md: str, figures: dict[str, list]) -> dict[str, list[str]]:
    """Section → what is wrong with its figures. Empty when the outline matches the plan.

    A figure is evidence the author produced. Leaving it out is not a stylistic choice, and
    neither is filing it under a section the plan did not name — an author's figure states
    its own section, and rayleigh's carry the results. So this is deviance of exactly the
    kind a beat count is, and it converges or blocks the same way.

    Three failures, all silent before this: never placed (css2026 lost its Methods
    illustration because nothing had told the model Methods owed one), placed twice, or
    placed somewhere the plan did not put it.
    """
    from . import guards
    placed = guards.outline_figures(guards.parse_outline(outline_md), outline_md)
    where: dict[str, list[str]] = {}
    for path, heading in placed:
        where.setdefault(path, []).append(heading)
    # which section each heading belongs to
    section_of, cur = {}, ""
    for line in outline_md.splitlines():
        st = line.lstrip()
        if st.startswith("## ") and not st.startswith("### "):
            cur = st[3:].strip()
        elif st.startswith("### ") and cur:
            section_of[st[4:].strip()] = cur
    out: dict[str, list[str]] = {}
    expected_paths = {f.path: sec for sec, figs in figures.items() for f in figs}
    for sec, figs in figures.items():
        for f in figs:
            spots = where.get(f.path, [])
            name = f.path.rsplit("/", 1)[-1]
            if not spots:
                out.setdefault(sec, []).append(f"{name}: not placed anywhere")
            elif len(spots) > 1:
                out.setdefault(sec, []).append(f"{name}: placed {len(spots)} times")
            else:
                got = section_of.get(spots[0], spots[0])
                if got != sec:
                    out.setdefault(sec, []).append(
                        f"{name}: placed under {got!r}; the plan puts it in {sec!r}")
    for path, spots in where.items():
        if path not in expected_paths:
            sec = section_of.get(spots[0], spots[0])
            out.setdefault(sec, []).append(
                f"{path.rsplit('/', 1)[-1]}: not a figure this paper has")
    return out


def lock_to_skeleton(outline_md: str, skeleton_md: str, title: str,
                     authors_block: str = "",
                     planned: list | None = None
                     ) -> tuple[str, list[str], list[tuple[str, list[str]]]]:
    """Rebuild the outline from the APPROVED skeleton, keeping only the model's bullets.

    Conformance stops being something the model is asked for and then checked on. The
    headings, their order, their levels, the title, the author block and the furniture are
    all already known — from the skeleton the author gated, and from the project config —
    so none of them is worth a token of inference, and every one of them was a way to
    deviate. Phase two supplies bullets; this supplies everything else.

    "Almost honoured the structure" is not a pass. The run that prompted this obeyed
    "APPROVED and FIXED — do not add, remove, merge or rename" for sixteen of seventeen
    subsections, dropped one, invented another, and duplicated the title because it had
    been shown a document containing one.

    ``planned`` is what each heading is OWED — the (label, heading, words, bullets) rows
    ``planned_bullets`` produces. Given it, any heading whose beat count differs is reported
    with the beats it wrote, so the caller can ask for a rewrite. Nothing is removed here.

    Returns the locked outline, headings left with no bullets, and (heading, beats) for
    every heading that does not match its plan.
    """
    from . import guards
    bullets = _bullets_by_heading(outline_md)
    heads = [(len(st) - len(st.lstrip("#")), st[len(st) - len(st.lstrip("#")):].strip())
             for st in (r.lstrip() for r in skeleton_md.splitlines())
             if st.startswith("#") and st[len(st) - len(st.lstrip("#")):].strip()]
    heads = [(lvl, text) for lvl, text in heads if lvl >= 2]

    lines = [f"# {title}", ""]
    if authors_block:
        lines += [authors_block, ""]
    empty: list[str] = []
    surplus: list[tuple[str, list[str]]] = []
    want = {guards._norm_heading(h): n for _, h, _, n in (planned or [])}
    for i, (lvl, text) in enumerate(heads):
        lines += [f"{'#' * lvl} {text}", ""]
        key = guards._norm_heading(text)
        # FURNITURE CARRIES NO BULLETS, and that is ensured rather than asked for. All three
        # are written elsewhere: the abstract is drafted last from the finished paper, the
        # bibliography is rendered at draft time, and the contribution statement belongs at
        # the paper stage. The prompt says so for each, and the prompt said "do not assign
        # any role" to a model that then assigned all fourteen to both authors.
        furniture = (guards.is_abstract(text) or guards.is_references(text)
                     or guards.is_acknowledgements(text))
        got = [] if furniture else bullets.get(key, [])
        # THE COUNT IS NOT THE MODEL'S TO SET. A bullet is a paragraph, and at the draft the
        # section's band is bullets x rate — so six extra beats here became up to 1,100 words
        # of authorised band, every section legal and the document 2,600 over, discovered
        # only after a GPU run by the one repair that has never yet succeeded in cutting.
        # This is the last rung where a bullet costs nothing to remove.
        # NOTHING IS CUT HERE. Truncating is deleting content, and deleting is the author's
        # act, not the tool's — the only thing that stood between a cut beat and outright
        # loss was a merge pass that has never been observed working, and a beat carrying a
        # figure took the figure down with it. The variance is REPORTED; the caller re-asks
        # the model to rewrite the subsection to its count, and refuses to write an outline
        # that still deviates. The tool is not capable of deviance; the author is.
        # EXPECTED ZERO IS AN EXPECTATION. `want` holds only the headings the plan names —
        # the subsections — so `want.get(key, 0)` used to mean "not planned", and `if n`
        # then skipped the check and wrote the bullets through untouched. A section that
        # owns no beats of its own thereby acquired five: the recount's figure beats landed
        # on `## Methods` and `## Results`, were never reported as variance, and were then
        # counted as real beats by reconcile_plan — 5,410 planned words against a 5,000
        # ceiling, with no comment saying anything was wrong. Furniture is exempt because
        # its bullets are furniture; everything else is owed exactly what the plan says,
        # and for an unplanned heading that is none.
        n = want.get(key, 0)
        if not furniture and len(got) != n:
            surplus.append((text, got))
        if got:
            lines += got + [""]
            continue
        # A section with subsections carries its bullets in them; only a LEAF that came
        # back empty is a heading the model failed to plan.
        has_child = i + 1 < len(heads) and heads[i + 1][0] > lvl
        if not has_child and not (guards.is_references(text)
                                  or guards.is_acknowledgements(text)
                                  or guards.is_abstract(text)):
            empty.append(text)
    return "\n".join(lines).rstrip() + "\n", empty, surplus


def _skeleton_section(skeleton: str) -> str:
    """The approved structure phase two writes bullets onto.

    It is a CONTRACT, not a suggestion: the author redlined these headings and gated them.
    Phase two adds beats; it does not get to rename a section, add one, or drop one.
    """
    if not skeleton.strip():
        return ""
    return (
        "APPROVED STRUCTURE (the author gated this — reproduce every heading EXACTLY, at "
        "the same level, in this order; add none, drop none, rename none):\n"
        f"{skeleton.strip()}\n\n")


_DRAFT_PROMPT = """\
Add the content beats to an APPROVED paper structure. The headings are fixed; you are \
writing what each subsection must argue.

{skeleton_section}\
Use the structural analysis below to derive the beats from this paper's actual \
intellectual content.

Title: {title}
Topic: {topic}
Focus: {focus}
{venue_section}
Structural analysis:
{analysis}
{narrative_section}{litrev_section}
Rules:
- The outline must expand the author-approved narrative spine, if provided — every \
beat of that narrative must be represented, and the section structure must serve \
that through-line rather than diverge from it
- All section and subsection names must be derived from the paper's content — \
do not use generic names such as "Related Work", "Case Study", "Implications", \
or "Theoretical Framework"
- Use ## for major sections (numbered: ## 1. Introduction, ## 2. …, etc.)
- Use ### for subsections wherever the structural analysis identifies multiple \
distinct pillars, steps, or stages, numbered within their section (### 2.1, ### 2.2)
- Use #### for a third tier where a subsection genuinely decomposes — the parts of a \
model, the stages of a protocol. Do NOT flatten a third tier into ### : a ### that \
contains other ### headings renders in Word as a list of siblings and loses the \
hierarchy the reader needs. A #### heading is not numbered
- Never skip a level (## must not be followed directly by ####), and number \
subsections consecutively from 1 with no gaps — a gap reads as a missing section \
and the draft will invent one to fill it
- A heading either carries bullets of its own or contains subsections beneath it, \
never neither
- Background subsections should map to the background_pillars in the analysis
- Methods subsections should map to the method_steps in the analysis, in order
- If empirical_elements lists named cases or datasets, each must appear as a \
named subsection, not a generic placeholder
- Results must follow the sequence in results_structure from the analysis
- Discussion must address the discussion_angle from the analysis, and include \
a Limitations subsection
- Methods subsections must be grounded in the analysis's method_steps, key_equations, \
and key_design — name the specific algorithms, parameters, and design choices they \
record; method_steps gives the order, and each subsection must specify which equations \
from key_equations it introduces or derives
- Results subsections must be grounded in the analysis's key_findings — cite the \
specific values, outcomes, and patterns key_findings records; results_structure gives \
the order
- If key_figures is non-empty in the analysis: every figure must be PLACED exactly once, \
APPENDED IN DOUBLE SQUARE BRACKETS to the bullet whose finding it shows — "- <the beat> \
[[Figure N: <that figure's caption> (<that figure's exact path>)]]" — numbered from 1 in \
the order the figures appear in the finished paper. A figure is not a beat and never gets \
a bullet of its own; a bullet may carry more than one. A figure \
whose origin is "results" goes in the Results subsection whose finding it shows. A figure \
whose origin is "author" is an illustration the author placed deliberately — put it in the \
section its "section" hint names (a model schematic belongs in Methods, not Results), and \
never move it into Results. Use each figure's exact path; never invent a figure, a path, \
or a caption that key_figures does not give, and never place one twice
- If the analysis carries no key_equations or key_design, Methods describes the planned \
approach only; if it carries no key_findings, Results describes anticipated findings only; \
Discussion and Conclusion must not claim specific empirical outcomes not supported \
by the available content
- Include 3–5 bullet points per subsection describing what that subsection \
specifically argues, shows, or demonstrates for this paper
- Leave the unnumbered "## Abstract" heading EMPTY — write no bullets under it. The \
abstract is drafted last, from the finished paper, and never reads this outline: bullets \
here are written, gated and then discarded, and being unplanned they arrive in whatever \
house style the model invents. Furniture, like Acknowledgements and References. \
to be redrafted across editing rounds — the bullets state its brief, not its prose
- After the Conclusion, add an unnumbered "## Acknowledgements" heading with no \
bullets (the contribution statement is written at the paper stage)
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
8. Heading levels: a skipped level (## followed by ####), a third tier
flattened into ###, a subsection numbering gap, or missing ### subsections where
the analysis indicates multiple distinct \
components exist
9. Methods, Results, Discussion, or Conclusion sections that claim specific \
empirical detail not supported by the available content noted in the analysis \
(e.g. specific findings, measured outcomes, or evaluation results when no \
results content was provided)
10. Methods subsections that do not specify which equations from key_equations \
are introduced or derived there (only applies when key_equations is non-empty)
11. Results subsections that do not cite specific findings from key_findings \
(only applies when key_findings is non-empty)
11b. A figure in key_figures that is not placed, placed more than once, or written \
any way other than APPENDED IN DOUBLE SQUARE BRACKETS to the bullet that discusses \
it — "- <the beat> [[Figure: <caption> (<path>)]]". A figure is not a beat and never \
gets a bullet of its own (only applies when key_figures is non-empty)
11c. A figure moved out of the section it belongs to: one showing a result belongs \
with the finding it shows, and an illustration belongs in the section its own \
"section" hint names — a model schematic belongs in Methods and must NOT be moved \
into Results
12. A missing unnumbered Abstract section at the top, or any bullets written \
under it — it carries none
13. A missing Acknowledgements heading between the Conclusion and References, \
or any bullets written under it — it carries none
13b. Any bullets under the References heading — it carries none either
13c. A bullet sitting directly under a "## " section that has "### " subsections. \
Beats belong to subsections; a section with subsections carries none of its own
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

Fix every listed problem. Preserve what is already correct.

- The HEADINGS ARE FIXED. Reproduce every "## " and "### " heading exactly as it \
appears above — same words, same level, same order. You are revising beats, never \
structure: do not rename, add, merge, drop or reorder a heading, and do not invent a \
name from the content
- Keep every "[[Figure: … (path)]]" exactly as written, attached to the bullet that \
discusses it. A figure may not be dropped, reworded, duplicated, moved to another \
section, or given a bullet of its own
- A bullet is ONE PARAGRAPH of the finished paper. Do not split one into two or merge \
two into one unless the critique asks for it — the count is the author's, not yours
- Abstract, Acknowledgements and References carry no bullets

Output only the revised outline. No preamble."""

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
it and all its ###/#### subsections grounded in the actual code — reference specific \
algorithms, functions, parameters, and implementation choices; method_steps gives \
structural order; each subsection must specify which equations from key_equations \
are introduced there (if key_equations is empty, extract equations directly from \
the source code above)
- If results content is provided above: identify the Results section and rewrite it \
and all its ###/#### subsections grounded in the actual results — cite specific values, \
outcomes, and patterns present in the results content; results_structure gives \
structural order; each Results subsection must cite specific findings from \
key_findings with values where present (if key_findings is empty, extract concrete \
facts directly from the results content above); and if key_figures is non-empty, place \
each figure by APPENDING it IN DOUBLE SQUARE BRACKETS to the bullet whose finding it \
shows — "- <the beat> [[Figure: <caption> (`<exact path>`)]]" — every figure exactly once, \
exact paths only. A figure is not a beat and never gets a bullet of its own: it belongs to the \
paragraph that introduces it, and a bullet may carry more than one
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
    narrative: str = "", figures=None, project_dir: Path | None = None,
) -> str:
    """Return structural analysis as a JSON string (coordinator call).

    ``narrative`` is the human-approved one-pager: the concise path through the
    paper. When present it anchors the intellectual structure — the extracted
    contribution, pillars, and discussion angle must honour that through-line.

    ``figures`` is rayleigh's manifest plus the author's own illustrations (path +
    caption + origin). Carried into the analysis as ``key_figures`` so every downstream
    pass — draft, critique, revise, refresh — knows which figures exist and where each
    belongs: a 'results' figure with the finding it shows, an 'author' figure in the
    section the author named. ``project_dir`` supplies those section hints.
    An outline that never names a figure leaves the draft to guess where they go.
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

    if figures:
        hints = author_figure_sections(project_dir) if project_dir else {}
        parsed["key_figures"] = [
            {"path": f.path, "caption": f.caption, "origin": f.origin,
             **({"section": hints[f.path]} if hints.get(f.path) else {})}
            for f in figures]
        log(f"[raconteur] {len(figures)} figure(s) carried into the analysis for placement")

    return f"{status}\n\n{json.dumps(parsed, indent=2)}"


def _critique_revise(brain: Brain, outline: str, analysis: str, n: int,
                     structural: str = "") -> str:
    """One critique→revise cycle. Returns the revised outline.

    ``structural`` is the deterministic guard battery's verdict, prepended to the LLM's
    critique. Two critique passes previously marked their own homework — a 1.1→1.3
    numbering gap survived both and cost a 4.5-hour draft run. What Python can compute,
    Python states; the model is left only what it alone can judge.
    """
    log(f"[raconteur] critique {n}…")
    critique = brain.coordinator(
        _CRITIQUE_PROMPT.format(analysis=analysis, outline=outline),
        system=_SYSTEM,
        num_ctx=16384,     # analysis + a growing outline overran the 8k budget (5,324 tok)
    )
    if structural:
        critique = f"{structural}\n{critique}"
    log(f"[raconteur] critique {n} findings:\n{critique}")

    log(f"[raconteur] revise {n}…")
    revised = brain.coordinator(
        _REVISE_PROMPT.format(analysis=analysis, outline=outline, critique=critique),
        system=_SYSTEM,
        num_ctx=16384,     # analysis + outline + critique; same window the draft uses
    )
    return revised


def _venue_specs_block(cfg: ProjectConfig, venue: str = "") -> str:
    from . import slate
    return slate.specs_block(cfg.venue(venue) if venue else None)


def _outline_guard_inputs(cfg: ProjectConfig, project_dir: Path, venue: str,
                          skeleton: str = "",
                          rates: dict[str, int] | None = None) -> dict:
    """Everything the structural battery needs to judge an outline against its venue."""
    from . import guards
    from .context import load_bib_keys, load_figure_manifest, load_author_figures
    v = cfg.venue(venue) if venue else None
    figs = (load_figure_manifest(project_dir, cfg.results_dir or "results")
            if cfg.results_dir else []) + load_author_figures(project_dir)
    corpus = len(load_bib_keys(project_dir, cfg.litrev_dir)) if cfg.litrev_dir else 0
    # The length to AIM AT, not the venue's ceiling — see guards.word_target.
    target = guards.word_target(v.word_min, v.word_limit) if v else 0
    budget = guards.prose_budget(target) if target else 0
    return {
        "budget": budget,
        "expected_figures": {f.path: f.origin for f in figs} or None,
        "required": (v.required_sections if v else "") or "",
        "shares": cfg.section_shares or None,
        # The approved structure. Without it phase two can rename, add or drop a section
        # and the author's redline is discarded in silence.
        "skeleton": skeleton,
        # And what a bullet is worth in each section, so the battery checks the outline
        # against the plan the author gated rather than re-deriving one from the share.
        "rates": rates or {},
    }


def _structural_critique(cfg: ProjectConfig, project_dir: Path, outline: str,
                         venue: str = "", skeleton: str = "", rates: dict | None = None) -> str:
    """The guard battery, phrased as critique the reviser must act on."""
    from . import guards
    findings = guards.outline_findings(
        outline, **_outline_guard_inputs(cfg, project_dir, venue, skeleton, rates))
    if not findings:
        return ""
    log(f"[raconteur] structural guards: {len(findings)} finding(s)")
    for f in findings:
        log(f"  · {f.kind} — {f.where}")
    lines = "\n".join(f"- {f.where}: {f.imperative}" for f in findings)
    return ("Structural defects found mechanically. These are not matters of judgement — "
            "fix every one:\n" + lines + "\n")


def _log_structure(cfg: ProjectConfig, project_dir: Path, outline: str,
                   venue: str = "", skeleton: str = "", rates: dict | None = None) -> None:
    """What survived. An outline that still fails its venue is the author's call to make,
    but they must be told before they gate it, not after a draft run discovers it."""
    from . import guards
    inputs = _outline_guard_inputs(cfg, project_dir, venue, skeleton, rates)
    heads = guards.parse_outline(outline)
    n_leaves = len(guards.leaves(heads))
    budget = inputs["budget"]
    if budget:
        afford = sum(guards.leaf_allowance(budget, inputs["shares"]).values())
        log(f"[raconteur] structure: {n_leaves} subsection(s), venue affords ~{afford} "
            f"({budget} prose words)")
    remaining = guards.outline_findings(outline, **inputs)
    if remaining:
        log(f"[warn] {len(remaining)} structural finding(s) survived the critique passes:")
        for f in remaining:
            log(f"[warn]   · {f.kind} — {f.where}")


def _build_venue_section(cfg: ProjectConfig, project_dir: Path, venue: str = "",
                         skeleton: str = "", rates: dict[str, int] | None = None,
                         figures: dict[str, list] | None = None) -> str:
    """What the writer is told about where this is going.

    An outline is written FOR a venue — its length, its columns, what it publishes — so the
    venue is an argument, not a project-wide setting. The one-pager passes none: the
    narrative belongs to the work, not to whoever might publish it.
    """
    specs = _venue_specs_block(cfg, venue)
    budget = _budget_block(cfg, project_dir, venue, skeleton, rates, figures)
    if budget:
        specs = f"{specs}\n{budget}" if specs else budget
    venue_analysis = load_venue_analysis(project_dir, selected=bool(venue)) if venue else ""
    if venue_analysis:
        block = f"Venue Analysis:\n{venue_analysis}\n"
        if specs:
            block += f"\n{specs}\n"
        return block
    return specs


# ── the word plan, carried forward ───────────────────────────────────────────
# The skeleton pins what a bullet is worth and its release carries it on the section
# headings. The outline is the manuscript's input, so the plan has to travel one more rung —
# a drafter made to reach back two rungs for a number is a drafter coupled to a document it
# does not read. What changes here is what the row COUNTS: bullets are real at this stage,
# so they are counted from the document rather than derived from the structure, and adding
# one adds its words.

def plan_row(top: str, n_subs: int, bullets: int, wpb: int) -> str:
    """One section's plan, as the author reads it on the heading."""
    return (f"{top} — {bullets * wpb} words · {n_subs} sub · {bullets} bullets · "
            f"{wpb} each. Add a bullet and this section grows by {wpb} words; remove one "
            f"and it shrinks by the same. Words per bullet is fixed for this section.")


def _sections_of(doc) -> list[tuple[str, list[str], int]]:
    """(section, its subsection names, bullets) from a rendered outline, in document order.

    A bullet counts where the PLAN puts one. ``planned_bullets`` names a section's
    subsections where it has them and the section itself where it does not, so a beat
    sitting on ``## Results`` above its first subsection is in neither place — it is beats
    the plan never granted, and counting it wrote it INTO the plan instead of against it.
    That is how one outline came to carry a Results comment reading "1660 words · 10
    bullets" against a plan of 996 and 6, and a document planned 410 words past the venue's
    ceiling with no comment saying anything was wrong. Those beats are reported as variance
    by lock_to_skeleton; here they are simply not counted.
    """
    from haarpi.redline import _list_level
    out: list[list] = []            # [name, subsections, in-subsection beats, stray beats]
    for para in doc.paragraphs:
        text = para.text.strip()
        style = para.style.name if para.style is not None else ""
        if not text:
            continue
        if style.startswith("Heading"):
            level = int("".join(c for c in style if c.isdigit()) or 1)
            if level == 2:
                out.append([text, [], 0, 0])
            elif level >= 3 and out:
                out[-1][1].append(text)
            continue
        if out and _list_level(para) is not None:
            out[-1][2 if out[-1][1] else 3] += 1
    return [(name, subs, inside if subs else stray)
            for name, subs, inside, stray in out]


def plan_notes(doc, rates: dict[str, int], budget: int,
               shares: dict | None,
               variance: dict[str, tuple[int, int]] | None = None,
               fig_faults: dict[str, list[str]] | None = None
               ) -> list[tuple[str, str]]:
    """(heading, comment) for every section that spends body prose.

    ``variance`` maps a subsection to (written, approved) where the model would not write
    the approved count. The outline is still WRITTEN — throwing away a run because two
    subsections of fifteen are off by one costs a GPU hour to save thirty seconds of your
    time — but the note says so, and an unresolved tool comment blocks the mint. So the
    deviance cannot reach the draft, and the fix is where the cheap fix is: your hands, in
    the document.
    """
    from . import guards
    notes = []
    for sec, subs, bullets in _sections_of(doc):
        if guards.is_abstract(sec) or guards.is_references(sec) \
                or guards.is_acknowledgements(sec):
            continue
        rate = section_rate(sec, len(subs), budget, shares, rates)
        row = plan_row(sec, len(subs), bullets or
                       guards.MIN_BULLETS_PER_SUBSECTION * max(len(subs), 1), rate)
        off = [(h, v) for h, v in (variance or {}).items() if h in subs]
        for x in (fig_faults or {}).get(sec, []):
            row += ("\n\nFIGURE NOT AS PLANNED — a figure is evidence you produced, and "
                    "the plan names which section carries it. This comment blocks the "
                    f"mint until you resolve it.\n  · {x}")
        if off:
            row += ("\n\nDOES NOT MATCH THIS PLAN — the tool asked twice and the model "
                    "would not write the approved count. Cut to the approved number, or "
                    "accept what is there and it becomes the plan. This comment blocks the "
                    "mint until you resolve it.\n"
                    + "\n".join(f"  · {h}: {w} bullet(s), plan says {a}"
                                 for h, (w, a) in off))
        notes.append((sec, row))
    return notes


def reconcile_plan(path) -> int:
    """Rewrite each section's word plan to match the outline as APPROVED. The mint's job.

    Same contract as the skeleton's, one rung on: WORDS PER BULLET is carried and never
    recomputed, and everything else is counted off the document the author gated. What is
    counted differs — the author edits BULLETS here, and a subsection they add inherits the
    section's rate, so it arrives as two more bullets at the price the section already pays.
    """
    from docx import Document
    from haarpi import redline as hrl
    from .redline import heading_comments
    from .skeleton import read_plan

    doc = Document(str(path))
    counts = {sec: (subs, bullets) for sec, subs, bullets in _sections_of(doc)}
    cmap = hrl.comments_by_id(path)
    updates: dict[str, str] = {}
    for a in heading_comments(path):
        head = a["heading"]
        if head not in counts:
            continue
        subs, bullets = counts[head]
        for cid in a["ids"]:
            rec = cmap.get(str(cid))
            if not rec:
                continue
            rate, _ = read_plan(rec.get("text", ""))
            if rate is None:
                continue                 # unreadable: leave the author's words alone
            updates[str(cid)] = plan_row(head, subs, bullets, rate)
    return hrl.set_comment_text(path, updates)


def _budget_block(cfg: ProjectConfig, project_dir: Path, venue: str = "",
                  skeleton: str = "", rates: dict[str, int] | None = None,
                  figures: dict[str, list] | None = None) -> str:
    """How many subsections this venue affords, and how they divide across sections.

    A venue's word limit reached the prompt as ambient fact and nothing turned it into the
    number the writer actually plans against — so a 5,000-word CFP got a 19-subsection
    outline, and the draft that obeyed it came in at 6,975. The limit has to arrive as an
    affordance ("you may write this many subsections, this long") or it is not a constraint.
    """
    from . import guards
    from .context import load_bib_keys, load_figure_manifest, load_author_figures
    v = cfg.venue(venue) if venue else None
    if not v or not v.word_limit:
        return ""
    target = guards.word_target(v.word_min, v.word_limit)
    budget = guards.prose_budget(target)
    shares = cfg.section_shares or guards.DEFAULT_SECTION_SHARES
    label = {"intro": "Introduction", "litrev": "Background", "methods": "Methods",
             "results": "Results", "other": "Discussion", "conclusion": "Conclusion"}
    # State the affordance, do not leave it to be derived. The model was given each
    # section's WORDS and the cost of one bullet and had to work out the rest; it read
    # "a subsection thinner than 100 words cannot carry an argument", divided Background's
    # 600 by four subsections, got 150, and shipped four. The arithmetic it skipped was that
    # four subsections is eight bullets, so 75 words a paragraph.
    allow = guards.leaf_allowance(budget, shares)
    per = "\n".join(
        f"  - {label.get(k, k)}: {round(budget * v_):d} words, affords "
        f"{allow.get(k, 1)} subsection(s)"
        for k, v_ in shares.items())
    stated = (f"{v.word_min}–{v.word_limit} words; aim at {target}"
              if v.word_min else f"{v.word_limit} words")
    return (
        "Length budget:\n"
        f"- The venue asks for {stated}. That is {budget} words of BODY PROSE. Section "
        f"headings, figure captions, [@citekey] tags, the reference list and the abstract "
        f"are NOT counted.\n"
        f"- Each section carries a share of those {budget} words, by what the section is "
        f"FOR:\n{per}\n"
        f"- The abstract is {guards.abstract_words(v.abstract_limit)} words, separately.\n"
        f"- One outline bullet becomes ONE PARAGRAPH of about "
        f"{guards.WORDS_PER_PARAGRAPH} words in the manuscript, and every subsection gets "
        f"at least {guards.MIN_BULLETS_PER_SUBSECTION}. So a subsection costs about "
        f"{guards.subsection_words()} words — that is the number to divide a section's "
        f"share by, NOT {guards.WORDS_PER_PARAGRAPH}. A subsection is a heading plus an "
        f"argument; one paragraph under a heading is a heading tax.\n"
        + (_per_subsection_plan(skeleton, budget, shares, rates, figures)
           if skeleton.strip() else
           "- Plan a structure that FITS this. Prefer merging related material into one "
           "substantial subsection over splitting it across several thin ones.\n")
    )


def section_rate(sec: str, subs: int, budget: int, shares: dict | None,
                 rates: dict[str, int] | None = None) -> int:
    """What one bullet is worth in this section: the APPROVED rate wherever there is one.

    The skeleton pins it and its release carries it. Recomputing it here from the share
    would undo the author's structural edit — Background's four approved subsections would
    be re-rated at 600/8 = 75 rather than the 150 they were gated at.

    The fallback derives it the way a fresh skeleton would, floored at one paragraph, so an
    outline built without a plan still behaves.
    """
    from . import guards
    if rates and sec in rates:
        return rates[sec]
    words = guards.section_words(sec, budget, shares)
    return max(guards.WORDS_PER_PARAGRAPH,
               words // (guards.MIN_BULLETS_PER_SUBSECTION * max(subs, 1)))


def planned_bullets(skeleton: str, budget: int,
                    shares: dict | None,
                    rates: dict[str, int] | None = None
                    ) -> list[tuple[str, str, int, int]]:
    """What every writable heading in the APPROVED skeleton is owed.

    Returns (label, heading, words, bullets) in document order — ``label`` for the prompt,
    ``heading`` for matching against what came back. One function, because the number the
    model is ASKED for and the number it is CHECKED against being computed in two places is
    exactly how the css2026 outline came back with five subsections at half their bullets
    and nothing objected.
    """
    from . import guards
    heads = guards.parse_outline(skeleton)
    current, kids, order = None, {}, []
    for h in heads:
        if h.level == 2:
            current = h.text
            if guards.is_abstract(current) or guards.is_references(current) \
                    or guards.is_acknowledgements(current):
                current = None
                continue
            kids[current] = []
            order.append(current)
        elif h.level >= 3 and current is not None:
            kids[current].append(h.text)

    # Bullets come from the APPROVED STRUCTURE, not from the share. Deriving them here as
    # bullets_for(share / subsections) gave each of Background's four gated subsections ONE
    # bullet where the author had approved two — the outline would have asked the model for
    # half the section it was told was fixed, and the check it is measured against, being
    # this same function, would have agreed.
    out: list[tuple[str, str, int, int]] = []
    for sec in order:
        subs = kids[sec]
        rate = section_rate(sec, len(subs), budget, shares, rates)
        n = guards.MIN_BULLETS_PER_SUBSECTION
        if not subs:
            out.append((sec, sec, n * rate, n))
            continue
        for sub in subs:
            out.append((f"{sec} / {sub}", sub, n * rate, n))
    return out


def bullet_shortfall(outline_md: str, skeleton_md: str, budget: int,
                     shares: dict | None,
                     rates: dict[str, int] | None = None
                     ) -> list[tuple[str, int, list[str]]]:
    """Headings the outline under-plans: (heading, bullets still owed, bullets it has).

    An empty heading is the extreme case of this, not a different problem — so the same
    re-ask covers both, and a subsection that came back with one bullet where the plan
    afforded two is no longer allowed to reach the drafter, where it becomes a 360-word
    paragraph.
    """
    have = _bullets_by_heading(outline_md)
    from . import guards
    out: list[tuple[str, int, list[str]]] = []
    for _, heading, _, want in planned_bullets(skeleton_md, budget, shares, rates):
        got = [b for b in have.get(guards._norm_heading(heading), []) if b.strip()]
        # BOTH directions. Testing only "<" meant the prompt's "write exactly this many"
        # was half enforced: four subsections came back over, the plan comment adopted the
        # model's number, and the draft would have banded against it. lock_to_skeleton cuts
        # the surplus, so a positive count here is now a shortfall the assembler cannot
        # create and a negative one is a bug in the assembler, not in the model.
        if len(got) != want:
            out.append((heading, want - len(got), got))
    return out


def figures_by_section(project_dir: Path, spine: list[str]) -> dict[str, list]:
    """Which section each available figure belongs to, decided before a beat is written.

    A figure is not decoration added once the prose exists — it is evidence, and the beat
    that discusses it has to be written knowing it must carry it. Left to be placed
    afterwards, two things went wrong on css2026: the model never placed the Methods
    illustration at all, because nothing had told it Methods owed one, and a recovery-
    landscape plot rode a beat that was later removed.

    Assignment is computable, which is why it can be stated up front: an author's figure
    names the section it belongs to (``author_figure_sections``), and rayleigh's figures
    carry the results. WHICH SUBSECTION is not computable — that depends on which finding a
    figure shows — so the plan names the section and the model places it within.
    """
    from .context import (load_figure_manifest, load_author_figures,
                          author_figure_sections)
    from . import guards
    hints = author_figure_sections(project_dir)
    out: dict[str, list] = {}
    for f in load_figure_manifest(project_dir) + load_author_figures(project_dir):
        if f.origin == "author":
            hint = hints.get(f.path, "")
            kind = guards.budget_kind(hint) if hint else "methods"
        else:
            kind = "results"
        target = next((s for s in spine if guards.budget_kind(s) == kind), "")
        if target:
            out.setdefault(target, []).append(f)
    return out


def _per_subsection_plan(skeleton: str, budget: int, shares: dict | None,
                         rates: dict[str, int] | None = None,
                         figures: dict[str, list] | None = None) -> str:
    """The exact words and bullets each APPROVED subsection gets.

    Phase two is handed a structure the author has already gated, so every one of these
    numbers is computable — and leaving the model to derive them across seventeen
    subsections is how a bullet count comes out wrong and `bullet_budget` then fails it.

    The merge advice this replaces belongs to the SKELETON stage, where the structure is
    still up for revision. Here it told the model to do the one thing skeleton_conformance
    rejects.
    """
    rows = [f"  - {label}: {words} words, {n} bullet(s)"
            for label, _, words, n in planned_bullets(skeleton, budget, shares, rates)]
    if not rows:
        return ""
    for sec, figs in (figures or {}).items():
        rows.append(f"  - {sec} must also place {len(figs)} figure(s), each appended to the "
                    f"bullet that discusses it, as [[Figure: <its caption> (`<path>`)]]:")
        # PATHS only. The captions are already in key_figures, where the model reads them;
        # repeating them here put 1,044 characters of the same text in the prompt twice and
        # was 40% of the overshoot that made Ollama discard the top of it.
        rows += [f"      {f.path}" for f in figs]
    return ("- The structure below is APPROVED and FIXED. Write exactly this many bullets "
            "under each heading — do not add, remove, merge or rename a heading:\n"
            + "\n".join(rows) + "\n")


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
    # This venue's outline has its own folder: paper/css2026/outline/.
    paper_dir = deliverable_dir(paper_dir, "outline", venue)
    paper_dir.mkdir(parents=True, exist_ok=True)
    user_rev = find_user_revision(paper_dir, cfg.short_title, chain_includes=scope,
                                  chain_excludes=others)
    # The .docx is the deliverable now — the .md is deleted after it renders. Counting
    # markdown here would report every finished outline as absent and rebuild it, spending
    # a GPU run to overwrite work the author may already have marked up.
    existing = find_latest(paper_dir, cfg.short_title, "docx", last_initials="ra",
                           chain_includes=scope, chain_excludes=others)

    if not existing:
        # Phase two writes bullets ONTO the author-approved skeleton. Without one there is
        # no approved structure to write to, and generating a structure here silently
        # bypasses the redline that phase one exists to get.
        from haarpi.naming import find_latest_release
        sk_home = deliverable_dir(project_dir / "paper", "skeleton", venue)
        # The DOCX. The skeleton's release is one artifact: the document the author
        # gated, carrying its word plan on the section headings as comments. A markdown
        # rendering cannot carry a comment, so the plan would not survive the conversion.
        skeleton_path = find_latest_release(
            sk_home / "output", cfg.short_title, "docx",
            chain_includes=([venue] if venue else []) + ["skeleton"])
        if skeleton_path is None:
            where = f" for {venue}" if venue else ""
            log(f"[error] no approved skeleton{where} — run 'raconteur skeleton"
                + (f" --venue {venue}'" if venue else "'")
                + " and gate it first")
            raise SystemExit(1)
        log(f"[raconteur] building on skeleton: {skeleton_path.name}")
        from docx import Document as _Docx
        from haarpi.redline import release_markdown
        from .skeleton import plan_from_release
        rates, problems = plan_from_release(skeleton_path)
        for pr in problems:
            log(f"[error] approved skeleton: {pr}")
        if problems:
            # The plan and the structure disagree, so neither can be trusted to say what
            # this paper is owed. Guessing here spends a draft run to find out.
            raise SystemExit(1)
        log("[raconteur] approved word plan: "
            + ", ".join(f"{k} {v}/bullet" for k, v in rates.items()))
        _outline_fresh(project_dir, cfg, brain, paper_dir, venue,
                       skeleton=release_markdown(_Docx(str(skeleton_path))), rates=rates)
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

_RECOUNT_PROMPT = """\
Rewrite outline subsections to the beat count the author approved.

Title: {title}

Structural analysis:
{analysis}

Each subsection below states how many bullets it must have and shows what you wrote.

{work}

Rules:
- Output EXACTLY the stated number of bullets for each subsection. Not one more, not one
  fewer.
- Nothing may be lost. Where you wrote too many, fold the surplus into the bullets that
  remain; where too few, develop what the section owes rather than padding.
- A bullet is ONE PARAGRAPH of the finished paper — keep each a single argument, not two
  joined by "and"
- Keep every "[[Figure: … (path)]]" exactly as written, attached to a bullet that discusses
  it. A figure may not be dropped, invented, or reworded.
- Output only, for each subsection, its "### " heading followed by its bullets. No preamble.
"""


def _with_rewrites(base_md: str, rewrite_md: str) -> str:
    """``base_md`` with each heading's beats REPLACED by the rewrite's, where it has any.

    The rewrite used to be concatenated onto the outline and re-locked. ``_bullets_by_heading``
    collects every bullet under a heading, so the original and its rewrite both survived and
    the beats accumulated: a subsection asked twice came back with three copies of itself and
    each figure placed three times. The truncation that used to follow hid it — capping to
    the planned count made a doubled subsection look merely correct. Take the cap away
    because deleting is the author's act, and the doubling is what is left.

    Replacing also makes the loop idempotent: running it on an outline that already matches
    changes nothing, which concatenation could never manage.
    """
    from . import guards
    over = {k: v for k, v in _bullets_by_heading(rewrite_md).items() if v}
    if not over:
        return base_md
    lines, current, replaced = [], None, set()
    for raw in base_md.splitlines():
        st = raw.lstrip()
        if st.startswith("#"):
            lvl = len(st) - len(st.lstrip("#"))
            text = st[lvl:].strip()
            current = guards._norm_heading(text) if text and lvl >= 2 else None
            lines.append(raw)
            if current in over and current not in replaced:
                lines += [""] + over[current]
                replaced.add(current)
            continue
        if current in over:
            continue                    # its beats came from the rewrite
        lines.append(raw)
    return "\n".join(lines) + "\n"


def _recount(brain: Brain, variance: list[tuple[str, list[str]]],
             want: dict[str, int], title: str, analysis: str,
             figs: dict[str, list[str]] | None = None) -> str:
    """Ask for the approved count, losing nothing.

    Replaces a merge pass that only ran after the tool had already cut the surplus. The tool
    no longer cuts, so this is the whole remedy: the model rewrites its own excess, or the
    stage refuses to write an outline that deviates.
    """
    work = []
    for heading, got in variance:
        n = want.get(heading, 0)
        if n == 0:
            # A heading the plan gives no beats of its own — a section whose content lives
            # in its subsections. Its beats are not surplus to be folded away; they are in
            # the wrong place, and the instruction has to say so or the model will simply
            # delete them.
            work.append(f"## {heading}\nThis heading OWNS NO BULLETS — its content lives "
                        f"in its subsections. Move each of these into the subsection of "
                        f"{heading} where it belongs, folding it into a bullet already "
                        f"there so that subsection's own count does not change:\n"
                        + "\n".join(got))
            continue
        work.append(f"### {heading}\nMUST HAVE {n} bullet(s); "
                    f"you wrote {len(got)}:\n" + "\n".join(got))
    for sec, probs in (figs or {}).items():
        work.append(f"## {sec}\nFIGURE FAULTS — fix by appending the figure to the bullet "
                    f"that discusses it, in this section:\n"
                    + "\n".join(f"  - {x}" for x in probs))
    log(f"[raconteur] re-asking {len(variance)} subsection(s) and "
        f"{len(figs or {})} section(s)")
    return brain.coordinator(
        _RECOUNT_PROMPT.format(title=title, analysis=analysis, work="\n\n".join(work)),
        system=_SYSTEM, num_ctx=16384)


def _outline_fresh(
    project_dir: Path, cfg: ProjectConfig, brain: Brain, paper_dir: Path,
    venue: str = "", skeleton: str = "", rates: dict[str, int] | None = None,
) -> None:
    litrev = load_litreview(project_dir, cfg.litrev_dir) if cfg.litrev_dir else ""
    code = load_methods(project_dir) if cfg.use_methods else ""
    results = load_results(project_dir, cfg.results_dir) if cfg.results_dir else ""
    figures = ((load_figure_manifest(project_dir, cfg.results_dir or "results")
                if cfg.results_dir else [])
               + load_author_figures(project_dir))
    narrative = load_onepager(project_dir, cfg.short_title)

    # Pass 1: structural analysis
    log("[raconteur] analysing paper structure…")
    analysis = _analyze_structure(brain, cfg.description, litrev, code, results,
                                  narrative, figures, project_dir)

    figures = figures_by_section(
        project_dir, [h.text for h in __import__("raconteur.guards", fromlist=["x"])
                      .parse_outline(skeleton) if h.level == 2])
    venue_section = _build_venue_section(cfg, project_dir, venue, skeleton, rates, figures)
    narrative_section = f"Narrative spine (author-approved):\n{narrative}\n" if narrative else ""
    litrev_section = f"Literature Review Context:\n{litrev}\n" if litrev else ""
    # The raw methods writeup and results digest are NOT re-sent here — the analysis above
    # already distilled them (key_equations/key_design, key_findings, key_figures). Sending
    # both overran num_ctx and, because the analysis sits at the top, it was the analysis
    # (and its figure paths) Ollama discarded. The draft plans from the distilled analysis;
    # the manuscript draft (paper.py) still writes from the raw content per section.

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
            skeleton_section=_skeleton_section(skeleton),
        ),
        system=_SYSTEM,
        num_ctx=16384,
    )

    # Passes 3–4 and 5–6: two critique→revise cycles
    outline = _critique_revise(brain, draft, analysis, n=1,
                               structural=_structural_critique(cfg, project_dir, draft,
                                                              venue, skeleton, rates))
    outline = _critique_revise(brain, outline, analysis, n=2,
                               structural=_structural_critique(cfg, project_dir, outline,
                                                              venue, skeleton, rates))

    divergence: dict[str, tuple[int, int]] = {}
    fig_faults: dict[str, list[str]] = {}
    if skeleton.strip():
        # The structure is not negotiated — it is the skeleton the author gated. Rebuild
        # from it and keep only the bullets; a heading the model renamed, dropped, invented
        # or duplicated simply has nowhere to land. See lock_to_skeleton.
        authors_block = ""          # furniture: derived from the manifest, never carried
        from . import guards
        gi = _outline_guard_inputs(cfg, project_dir, venue, skeleton, rates)
        budget, shares = gi["budget"], gi["shares"]
        plan = planned_bullets(skeleton, budget, shares, rates)
        want = {h: n for _, h, _, n in plan}
        # CONVERGE OR FAIL. The tool is not capable of deviance: it does not cut the model's
        # excess (deleting is the author's act) and it does not ship an outline that differs
        # from the plan (at the draft a section's band is bullets x rate, so six extra beats
        # became 1,100 words of authorised band — every section legal, the document 2,600
        # over, found only after a GPU run). It asks, and if the model will not comply it
        # writes nothing. A wasted outline run is cheaper than a wasted draft run.
        # Before anything MEASURES the figures, put them in the one form that can be
        # measured. Skipping this is what turned five correctly-placed figures into ten
        # references and a word plan 410 words past the venue ceiling — see normalise_figures.
        outline, ghosts = normalise_figures(outline, figures)
        outline, _, variance = lock_to_skeleton(outline, skeleton, cfg.title,
                                                authors_block, plan)
        fig_faults = figure_variance(outline, figures)
        _add_ghosts(fig_faults, ghosts)
        for attempt in (1, 2):
            if not variance and not fig_faults:
                break
            rewritten, ghosts = normalise_figures(
                _with_rewrites(outline,
                               _recount(brain, variance, want, cfg.title, analysis,
                                        fig_faults)),
                figures)
            outline, _, variance = lock_to_skeleton(rewritten, skeleton, cfg.title,
                                                    authors_block, plan)
            fig_faults = figure_variance(outline, figures)
            _add_ghosts(fig_faults, ghosts)
        # RECOVERABLE, not fatal. Discarding the run because two subsections of fifteen are
        # off by one spends a GPU hour to save thirty seconds of the author's time, and
        # leaves them nothing to look at. The outline is written; the plan comment on each
        # affected section says it does not match, and an unresolved tool comment blocks the
        # mint — so the deviance cannot reach the draft, and the fix is a deletion in Word.
        divergence = {h: (len(got), want.get(h, 0)) for h, got in variance}
        if fig_faults:
            log(f"[warn] {len(fig_faults)} section(s) do not carry the figures the plan "
                f"names. A figure is evidence you produced; the mint is blocked:")
            for sec, probs in fig_faults.items():
                for x in probs:
                    log(f"  · {sec}: {x}")
        if divergence:
            log(f"[warn] {len(divergence)} subsection(s) do not carry the approved number "
                f"of bullets after two rewrites. The outline IS written, and the mint is "
                f"blocked until you cut them or accept them:")
            for h, (got_n, want_n) in divergence.items():
                log(f"  · {h}: {got_n} bullet(s), plan says {want_n}")

    _log_structure(cfg, project_dir, outline, venue, skeleton, rates)

    _write(project_dir, cfg, paper_dir, outline, venue, rates, divergence, fig_faults)
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
    figures = ((load_figure_manifest(project_dir, cfg.results_dir or "results")
                if cfg.results_dir else [])
               + load_author_figures(project_dir))
    narrative = load_onepager(project_dir, cfg.short_title)

    log("[raconteur] analysing paper structure…")
    analysis = _analyze_structure(brain, cfg.description, litrev, code, results,
                                  narrative, figures, project_dir)

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
    # Carry the rates off the document being answered, or the re-render re-rates every
    # section from its share and the author's structural edits are undone in silence.
    _write(project_dir, cfg, paper_dir, updated, venue, rates_from(user_rev))
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

def rates_from(path) -> dict[str, int]:
    """The pinned rates carried on a document's section headings.

    How the plan survives a revision: answering a redline re-renders the outline, and the
    new document must carry the rates the old one did or the section quietly re-rates itself
    off the share. Unvalidated on purpose — the mint reconciles counts, so all this needs to
    recover is the one number that cannot be derived.
    """
    from haarpi import redline as hrl
    from .redline import heading_comments
    from .skeleton import read_plan
    cmap = hrl.comments_by_id(path)
    out: dict[str, int] = {}
    for a in heading_comments(path):
        for cid in a["ids"]:
            rec = cmap.get(str(cid))
            if not rec:
                continue
            rate, _ = read_plan(rec.get("text", ""))
            if rate is not None:
                out[a["heading"]] = rate
    return out


def _write(project_dir: Path, cfg: ProjectConfig, paper_dir: Path, text: str,
           venue: str = "", rates: dict[str, int] | None = None,
           divergence: dict[str, tuple[int, int]] | None = None,
           fig_faults: dict[str, list[str]] | None = None) -> None:
    # No byline. Authors, affiliations and the corresponding address are derived from the
    # project manifest by ``load_authors_block`` and regenerated at every stage that needs
    # them — paper.py inserts them into the manuscript at write time regardless. A copy
    # riding the outline is a second, older home for authorship, five paragraphs the author
    # cannot usefully review, and a named byline on the working files of a double-blind
    # submission. Removed from the skeleton for these reasons; the same ones hold here.
    if text.lstrip().startswith("# "):
        output = text.strip() + "\n"      # already assembled from the skeleton
    else:
        output = f"# {cfg.title}\n\n{text.strip()}\n"
    out_path = paper_dir / major_outline_name(cfg.short_title, "md", venue=venue)
    out_path.write_text(output, encoding="utf-8")

    from .refdoc import render as _render_docx
    docx = _render_docx(out_path, project_dir)
    if docx is None:
        # Without pandoc the .md is the only output there is; deleting it would leave the
        # author nothing to look at.
        log(f"[raconteur] wrote {out_path.relative_to(project_dir)} (no .docx — pandoc)")
    if docx:
        # The markdown was pandoc's input and nothing else read it. The deliverable is the
        # .docx: it is what the author marks up, what the gate reads, and the only one that
        # can carry the word plan, since a comment is not markdown.
        out_path.unlink(missing_ok=True)
        # The plan travels on the document, where the author gates it. Written after the
        # render because pandoc supplies the comments part and discards anything upstream.
        try:
            from docx import Document as _Docx
            from haarpi import redline as _rl
            gi = _outline_guard_inputs(cfg, project_dir, venue)
            notes = plan_notes(_Docx(str(docx)), rates or {}, gi["budget"], gi["shares"],
                               divergence, fig_faults)
            if notes:
                n = _rl.add_anchored_comments(docx, notes, author="raconteur",
                                              initials="ra", headings_only=True)
                log(f"[raconteur] attached {n} word-plan comment(s)")
        except Exception as e:              # noqa: BLE001 — a comment must not fail a write
            log(f"[warn] could not attach word-plan comments: {type(e).__name__}: {e}")
        log(f"[raconteur] wrote {docx.relative_to(project_dir)}")

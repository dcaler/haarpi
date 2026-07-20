"""Phase one of the outline: the paper's sections and subsections, and nothing else.

The outline used to be written in one pass — structure and content beats together — and the
structure was the part nobody could check until a draft had been written from it. A
19-subsection outline against a 5,000-word CFP produced a 6,975-word manuscript, and the
only signal was 4.5 GPU-hours later.

Phase one emits headings alone. That is enough to compute the entire word plan: each
section's share of the body budget, and therefore how many paragraphs each subsection can
afford. The author redlines THAT — cheap to fix, and fixed before a bullet is written.
Phase two (``outline``) adds the bullets, one per manuscript paragraph.

Two conventions the skeleton carries:

  * Headings carry NO numbers. The .docx style supplies them (Heading 1 -> "1",
    Heading 2 -> "1.1"), so a literal "2.1" in the text would render as "2.1 2.1". It also
    retires a whole failure mode: an outline numbered 1.1, 1.3 reads to a drafting model as
    a missing 1.2, and it will helpfully invent one — which is exactly how the css2026 draft
    acquired a section nobody asked for.
  * IBMRDC — Introduction, Background, Methods, Results, Discussion, Conclusion — unless the
    venue says otherwise. An Abstract precedes it; Acknowledgements and References are
    permanent furniture, never planned here and never deleted downstream.
"""

from __future__ import annotations

from pathlib import Path

from . import guards
from .brain import Brain
from .config import ProjectConfig
from .log import log

# The default spine. A venue may override it (`VenueConfig.section_structure`) — Nature's
# format is not IBMRDC, and a venue that mandates its own order is stating a spec, not a
# preference.
IBMRDC = ("Introduction", "Background", "Methods", "Results", "Discussion", "Conclusion")

# Never planned, never deleted, never budgeted. The bibliography is rendered by pandoc at
# write time and the CRediT statement is built from the author list, so neither is the
# outline's business — but both must exist in the manuscript.
FURNITURE = ("Acknowledgements", "References")

_SYSTEM = (
    "You plan the structure of academic papers. You return headings only — never prose, "
    "never bullets, never commentary."
)

_SKELETON_PROMPT = """\
Plan the SECTION AND SUBSECTION STRUCTURE of an academic paper. Headings only.

Title: {title}
Topic: {topic}
Focus: {focus}
{venue_section}
Structural analysis:
{analysis}
{narrative_section}
{budget_section}
Rules:
- Use EXACTLY these top-level sections, in this order, each as a `## ` heading:
{spine}
- Under each, add `### ` subsections named from this paper's actual content — the \
background_pillars, method_steps and results_structure in the analysis above. A section \
whose material is one continuous argument takes NO subsections; an Introduction of ~2 \
paragraphs never needs them.
- Give each section only as many subsections as its word share affords. The budget above \
states each section's words and what one bullet costs — a subsection thinner than about \
{min_words} words cannot carry an argument, so merge rather than split.
- Do NOT number any heading. The document style numbers them: `## Methods` renders as \
"3 Methods" and `### The Model` as "3.1 The Model". A number you write yourself renders \
twice.
- Do NOT write bullets, beats, prose, or any body text. Headings only — that is the whole \
output. The content plan is a separate pass.
- Do NOT add Abstract, Acknowledgements or References: they are added automatically.
- Output only the headings, one per line, starting with the first `## `.
"""


def spine_for(cfg: ProjectConfig, venue: str = "") -> tuple[str, ...]:
    """The top-level sections this paper uses.

    IBMRDC unless the venue states its own structure. A venue's mandated order is a SPEC
    read off its call for papers, not a preference — see ``VenueConfig.section_structure``.
    """
    v = cfg.venue(venue) if venue else None
    stated = (getattr(v, "section_structure", "") or "").strip() if v else ""
    if not stated:
        return IBMRDC
    parts = [p.strip() for p in stated.replace("\n", ",").split(",") if p.strip()]
    return tuple(parts) or IBMRDC


def assemble(sections: list[tuple[int, str]], title: str) -> str:
    """The skeleton document: title, abstract, the planned sections, then the furniture.

    The Abstract and the furniture are added HERE rather than asked of the model: they are
    fixed, and a model that can add them can also forget them, rename them, or number them.
    """
    lines = [f"# {title}", "", "## Abstract", ""]
    for level, text in sections:
        lines += ["#" * level + " " + text, ""]
    for f in FURNITURE:
        lines += [f"## {f}", ""]
    return "\n".join(lines).rstrip() + "\n"


def parse_headings(raw: str, spine: tuple[str, ...]) -> list[tuple[int, str]]:
    """(level, text) for every heading the model returned, numbers stripped.

    A model told not to number will number anyway often enough to matter, and a stray "2.1"
    in the text renders as "2.1 2.1" once the style adds its own. Stripping is cheaper than
    a retry and cannot be got wrong.
    """
    import re
    out: list[tuple[int, str]] = []
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line.startswith("#"):
            continue
        level = len(line) - len(line.lstrip("#"))
        text = re.sub(r"^\d+(\.\d+)*\.?\s*", "", line[level:].strip()).strip()
        if not text or level < 2:
            continue
        if guards.is_abstract(text) or guards.is_references(text) \
                or guards.is_acknowledgements(text):
            continue                      # added by assemble(), never by the model
        out.append((min(level, 4), text))
    return out


def findings(sections: list[tuple[int, str]], spine: tuple[str, ...],
             budget: int, shares: dict | None = None) -> list[guards.Finding]:
    """What is mechanically wrong with a skeleton. PHASE: skeleton.

    The whole point of phase one: every one of these is computable from headings alone,
    before a bullet or a sentence exists.
    """
    out: list[guards.Finding] = []
    tops = [t for lvl, t in sections if lvl == 2]

    missing = [s for s in spine if s.lower() not in [t.lower() for t in tops]]
    for s in missing:
        out.append(guards.Finding(
            "missing-section", s,
            f'The structure calls for a "{s}" section and the skeleton has none. Add it.'))
    extra = [t for t in tops if t.lower() not in [s.lower() for s in spine]]
    for t in extra:
        out.append(guards.Finding(
            "invented-section", t,
            f'"{t}" is not part of this paper\'s structure. Remove it, or fold its '
            f"material into the section that does cover it."))

    if budget > 0:
        counts: dict[str, int] = {}
        current = None
        for lvl, text in sections:
            if lvl == 2:
                current = text
                counts.setdefault(current, 0)
            elif current is not None:
                counts[current] += 1
        for top, n in counts.items():
            words = guards.section_words(top, budget, shares)
            if not words or n == 0:
                continue
            each = words // n
            if each < guards.PARAGRAPH_BAND[0]:
                out.append(guards.Finding(
                    "subsections-too-thin", top,
                    f'"{top}" carries {words} words across {n} subsections — {each} each, '
                    f"below the {guards.PARAGRAPH_BAND[0]} words a paragraph needs. Merge "
                    f"to at most {max(1, words // guards.PARAGRAPH_BAND[0])}."))
    return out


def plan_table(sections: list[tuple[int, str]], budget: int,
               shares: dict | None = None) -> str:
    """The word plan the skeleton implies — what the author is really approving."""
    rows, current, subs = [], None, []
    ordered: list[tuple[str, list[str]]] = []
    for lvl, text in sections:
        if lvl == 2:
            current = text
            subs = []
            ordered.append((current, subs))
        elif current is not None:
            subs.append(text)
    for top, kids in ordered:
        words = guards.section_words(top, budget, shares)
        n = len(kids) or 1
        rows.append(f"  {top:<28}{words:>6} words  {len(kids):>2} sub  "
                    f"{words // n:>4} each  {guards.bullets_for(words // n):>2} bullets")
    return "\n".join(rows)


# ── the verb ─────────────────────────────────────────────────────────────────

def _write(project_dir: Path, cfg: ProjectConfig, work_dir: Path, text: str,
           venue: str = "") -> Path:
    from .context import load_authors_block
    from .naming import major_skeleton_name
    from .render import to_docx
    v = cfg.venue(venue) if venue else None
    who = load_authors_block(project_dir, anonymized=bool(v and v.anonymized))
    if who:
        lines = text.split("\n")
        lines.insert(1, f"\n{who}")
        text = "\n".join(lines)
    out = work_dir / major_skeleton_name(cfg.short_title, "md", venue=venue)
    out.write_text(text, encoding="utf-8")
    log(f"[raconteur] wrote {out.relative_to(project_dir)}")
    docx = to_docx(out)
    if docx:
        log(f"[raconteur] wrote {docx.relative_to(project_dir)}")
    return out


def run(project_dir: Path, venue: str = "") -> None:
    """Phase one: plan the paper's sections and subsections against the word budget."""
    from .brain import Brain
    from .config import GlobalConfig
    from .context import (check_prerequisites, load_litreview, load_methods,
                          load_onepager, load_results)
    from .naming import deliverable_dir, find_latest, find_user_revision
    from .outline import _analyze_structure, _build_venue_section, _budget_block

    if not ProjectConfig.exists(project_dir):
        log("[error] no paper/raconteur.yaml found — run 'raconteur init' first")
        raise SystemExit(1)
    cfg = ProjectConfig.load(project_dir)
    gcfg = GlobalConfig.load()
    check_prerequisites(project_dir, cfg)

    narrative = load_onepager(project_dir, cfg.short_title)
    if not narrative:
        log("[error] no one-pager found — run 'raconteur onepager' first")
        raise SystemExit(1)

    from . import slate
    venue = slate.resolve(cfg, venue)
    if venue:
        log(f"[raconteur] planning structure for {cfg.venues[venue].name} ({venue})")

    work = deliverable_dir(project_dir / "paper", "skeleton", venue)
    work.mkdir(parents=True, exist_ok=True)
    scope = ([venue] if venue else []) + ["skeleton"]
    others = [v for v in cfg.venues if v != venue]
    if find_latest(work, cfg.short_title, "md", last_initials="ra",
                   chain_includes=scope, chain_excludes=others):
        if find_user_revision(work, cfg.short_title, chain_includes=scope,
                              chain_excludes=others):
            log("[raconteur] a redlined skeleton is waiting — answer it with "
                "`raconteur skeleton --revise` (not yet implemented)")
        else:
            log("[raconteur] skeleton exists — annotate the docx with your initials, "
                "then run `haarpi next`")
        log("[error] nothing to do: this run made no changes (exit 3)")
        raise SystemExit(3)

    brain = Brain(gcfg, coordinator=cfg.brain.coordinator_model)
    litrev = load_litreview(project_dir, cfg.litrev_dir) if cfg.litrev_dir else ""
    code = load_methods(project_dir) if cfg.use_methods else ""
    results = load_results(project_dir, cfg.results_dir) if cfg.results_dir else ""

    log("[raconteur] analysing paper structure…")
    analysis = _analyze_structure(brain, cfg.description, litrev, code, results,
                                  narrative, None, project_dir)

    spine = spine_for(cfg, venue)
    budget = guards.prose_budget(
        guards.word_target(cfg.venue(venue).word_min, cfg.venue(venue).word_limit)
    ) if venue and cfg.venue(venue) and cfg.venue(venue).word_limit else 0

    log("[raconteur] planning sections…")
    raw = brain.coordinator(
        _SKELETON_PROMPT.format(
            title=cfg.title, topic=cfg.topic, focus=cfg.focus,
            venue_section=_build_venue_section(cfg, project_dir, venue),
            analysis=analysis,
            narrative_section=f"Narrative spine (author-approved):\n{narrative}\n",
            budget_section=_budget_block(cfg, project_dir, venue),
            spine="\n".join(f"  - {s}" for s in spine),
            min_words=guards.PARAGRAPH_BAND[0],
        ),
        system=_SYSTEM, num_ctx=16384)

    sections = parse_headings(raw, spine)
    for f in findings(sections, spine, budget, cfg.section_shares or None):
        log(f"[warn] {f.kind} — {f.where}: {f.imperative}")
    if budget:
        log("[raconteur] word plan:")
        for line in plan_table(sections, budget, cfg.section_shares or None).splitlines():
            log(line)
    _write(project_dir, cfg, work, assemble(sections, cfg.title), venue)

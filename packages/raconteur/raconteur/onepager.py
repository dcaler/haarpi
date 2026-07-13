from __future__ import annotations
import re
from typing import NamedTuple
from .log import log
from pathlib import Path
from .brain import Brain
from .config import ProjectConfig, GlobalConfig
from .context import (
    load_litreview, load_methods, load_results, load_bib_summary,
    load_bib_keys, load_style_profile, load_figure_manifest, check_prerequisites,
)
from .naming import major_onepager_name, find_latest, find_user_revision
from .render import to_docx

# The one-pager is the first deliverable: the most concise path through the
# paper's narrative — high notes only, at most two figures. A human edits it,
# and 'outline' uses the approved narrative to design the full paper.
#
# Beats are drafted one at a time, each seeing only the evidence that bears on
# it (cf. paper.py:_context_for_section). The beats are fixed rather than parsed
# from an outline, so the routing is a static table. A whole-document critique
# then closes the through-line: a 1-3 sentence beat is too short to stand alone
# the way a paper section does, so coherence is repaired at the end rather than
# assumed.

_SYSTEM = (
    "You are an expert academic writing assistant. You distil a research project "
    "into the single most concise path through its narrative — the story a reader "
    "must follow, and nothing more."
)

# Priority claims are the reflex to suppress in the beats that position the work.
# A bare "avoid overclaiming" gets ignored, so the banned phrasings and their
# replacements are both named.
_NO_PRIORITY_CLAIMS = (
    '- Make no priority claims. Never write "the first to", "no one has", "novel", '
    '"unprecedented", or "opens an entirely new field". Frame the space as '
    'underexamined instead: "this area is underexamined", "has received little '
    'attention", "remains largely untested".'
)


class _Beat(NamedTuple):
    name: str
    intent: str            # what this beat must convey
    sources: tuple[str, ...]   # bulky evidence routed to it
    drop: tuple[str, ...]      # analysis keys withheld from it
    rules: str = ""            # extra constraints


# Approach is given key_design and denied key_findings: it describes how the
# experiments were built, never what they returned.
_BEATS: list[_Beat] = [
    _Beat(
        "Motivation",
        "why this problem matters — the stakes, and what it costs to leave the "
        "problem unaddressed",
        ("description", "litrev"),
        ("key_findings", "key_design", "key_equations"),
    ),
    _Beat(
        "Gap",
        "where this work sits in the literature: what the existing literature has "
        "and has not examined. This is the beat that situates the research product "
        "among what has come before",
        ("description", "litrev", "results"),
        ("key_design", "key_equations"),
        _NO_PRIORITY_CLAIMS,
    ),
    _Beat(
        "Approach",
        "the core idea and method, named specifically, including how the experiments "
        "are designed",
        ("description", "code"),
        ("key_findings",),
        "- Describe the design of the work only. Report no outcomes, values, or "
        "findings — those belong to the Key result(s) beat.",
    ),
    _Beat(
        "Key result(s)",
        "the one or two findings that carry the paper, with the concrete values that "
        "support them",
        ("results",),
        ("key_design", "key_equations"),
    ),
    _Beat(
        "Implication",
        "what this work changes or enables",
        ("description", "litrev"),
        ("key_design", "key_equations"),
        "- Return explicitly to the gaps named in the Gap beat above and say how "
        "this work narrows them. Do not raise a new gap here.\n"
        + _NO_PRIORITY_CLAIMS,
    ),
]

# Figures come from rayleigh's results dir, so results are the only beat that can
# carry one — and it inherits the whole two-figure budget.
_FIGURE_BEAT = "Key result(s)"

_FIGURE_RULE = (
    "- You may embed AT MOST TWO figures, and only those that carry the argument. "
    "Use exactly this markdown form on its own line: ![short caption](figure/path). "
    "Choose paths only from the figure list above; do not invent paths. Omit figures "
    "entirely if none is essential."
)
_NO_FIGURE_RULE = "- Do not embed a figure in this beat."

_BEAT_PROMPT = """\
Write the **{beat}** beat of a one-pager: the most concise path through this paper's \
narrative.

This beat must convey: {intent}

Title: {title}
Topic: {topic}
Focus: {focus}
{venue_section}{style_section}Structural analysis:
{analysis}

{evidence_section}{recut_section}{preceding_section}{figure_section}
Rules:
- Write 1-3 sentences. This is a beat, not a section — high notes only.
- Derive every claim from the content above. No generic academic filler: if the \
content does not support a claim, do not make it.
- Do not repeat what the preceding beats already said. Carry the through-line \
forward from them.
- Write prose only. Do not output the beat label, a heading, or a bullet.
{figure_rule}{extra_rules}
- Output only the beat text — no preamble, no closing remarks.
"""

_RECUT_BLOCK = """\
This is a RE-CUT: the author rejected the previous narrative. The previous \
{beat} beat read:
{prior}

The author's annotations on it:
{notes}

Honour every annotation. Re-derive the beat from the evidence above — do not \
patch the old wording. Keep only what the author explicitly marked as good.

"""

_RECUT_GENERAL = """\
The author also left these overall annotations on the previous one-pager \
(they apply to every beat):
{notes}

"""

_CRITIQUE_PROMPT = """\
Critique this one-pager. It must be the most concise path through the paper's \
narrative — high notes only, a single unbroken through-line.

Check for:
- Beats that repeat each other, or restate the same claim in different words.
- Breaks in the through-line: a beat that does not follow from the one before it.
- The Implication beat failing to return to the gaps the Gap beat named.
- Priority claims — "the first to", "no one has", "novel", "unprecedented". The \
work must be positioned as entering an underexamined area, not as unprecedented.
- Generic academic filler: any sentence that could appear in any paper in this field.
- Claims the structural analysis does not support.
- Total length over ~500 words, or more than two embedded figures.

One-pager:
{onepager}

List the specific problems as a numbered list. If there are none, say so.
Output only the list."""

_REVISE_PROMPT = """\
Revise this one-pager to address every point in the critique.

One-pager:
{onepager}

Critique:
{critique}

Rules:
- Keep the beat structure: each beat is a short bolded label followed by 1-3 sentences.
- Cut every sentence that is not essential to the through-line. Do not exceed ~500 words.
- Keep concrete values. Do not add new claims, and do not add figures — keep at most two.
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


def _evidence_for_beat(
    sources: tuple[str, ...], description: str, litrev: str, code: str, results: str
) -> str:
    """The evidence bundle for one beat — only what bears on it."""
    parts = []
    if "description" in sources and description:
        parts.append(
            "Research description (the author's own framing of the project — the "
            f"source of record for why this work matters):\n{description}"
        )
    if "litrev" in sources and litrev:
        parts.append(f"Literature review:\n{litrev}")
    if "code" in sources and code:
        parts.append(f"Methods (raster writeup):\n{code}")
    if "results" in sources and results:
        parts.append(f"Results content (rayleigh):\n{results}")
    return ("\n\n".join(parts) + "\n\n") if parts else ""


def _preceding_block(beats: list[tuple[str, str]]) -> str:
    if not beats:
        return ""
    written = "\n".join(f"**{name}** — {text}" for name, text in beats)
    return (
        "Beats already written (continue from these; do not repeat them):\n"
        f"{written}\n\n"
    )


_LABEL_DASHES = "—-–:"


def _strip_label(name: str, text: str) -> str:
    """Drop a beat label the model emitted despite being told not to."""
    t = text.strip()
    for prefix in (f"- **{name}**", f"**{name}**", f"- {name}", name):
        if t.lower().startswith(prefix.lower()):
            t = t[len(prefix):].lstrip().lstrip(_LABEL_DASHES).lstrip()
            break
    return t


_FIG_LINE = re.compile(r"[ \t]*(!\[[^\]]*\]\([^)]+\))[ \t]*$", re.M)


def _assemble(beats: list[tuple[str, str]]) -> str:
    """Bold-label paragraphs, each figure left standing as its own block."""
    out = []
    for name, text in beats:
        body = _FIG_LINE.sub(r"\n\n\1\n", text.strip()).strip()
        out.append(f"**{name}** — {body}")
    return "\n\n".join(out)


def _write(project_dir: Path, cfg: ProjectConfig, paper_dir: Path, text: str) -> None:
    output = f"# {cfg.title} — one-pager\n\n{text.strip()}\n"
    out_path = paper_dir / major_onepager_name(cfg.short_title, "md")
    out_path.write_text(output, encoding="utf-8")
    log(f"[raconteur] wrote {out_path.relative_to(project_dir)}")

    bib_path = (project_dir / cfg.litrev_dir / "output" / "refs.bib") if cfg.litrev_dir else None
    docx = to_docx(out_path, bib_path=bib_path, resource_path=project_dir)
    if docx:
        log(f"[raconteur] wrote {docx.relative_to(project_dir)}")


def _beat_of(text: str) -> str | None:
    t = text.lstrip("*-–—• ").lower()
    for beat in _BEATS:
        if t.startswith(beat.name.lower()):
            return beat.name
    return None


def _annotation_brief(user_rev: Path) -> tuple[dict[str, str], dict[str, str], str]:
    """The re-cut briefing from the reviewer's docx.

    Returns (prior, notes, general): each beat's previous text with the
    reviewer's tracked changes accepted, the comments anchored to it, and the
    annotations that anchor to no recognisable beat (title, figures) — those
    apply to the whole narrative.
    """
    from docx import Document
    from . import redline

    prior: dict[str, str] = {}
    md = redline.accepted_markdown(Document(str(user_rev)))
    for para in md.split("\n\n"):
        beat = _beat_of(para.strip())
        if beat and beat not in prior:
            prior[beat] = para.strip()

    notes: dict[str, str] = {}
    general: list[str] = []
    cmap = redline.comments_by_id(user_rev)
    for anchor in redline.comment_anchors(user_rev):
        comments = [cmap[c]["text"] for c in anchor["ids"] if c in cmap]
        if not comments:
            continue
        block = "\n".join(f"- {c}" for c in comments)
        beat = _beat_of(anchor["text"])
        if beat is None:
            general.append(block)
        else:
            notes[beat] = f"{notes[beat]}\n{block}" if beat in notes else block
    for h in redline.heading_comments(user_rev):
        comments = [cmap[c]["text"] for c in h["ids"] if c in cmap]
        if comments:
            general.append("\n".join(f"- {c}" for c in comments))
    return prior, notes, "\n".join(general)


# ── fresh one-pager ───────────────────────────────────────────────────────────

def _onepager_fresh(
    project_dir: Path, cfg: ProjectConfig, brain: Brain, paper_dir: Path,
    brief: tuple[dict[str, str], dict[str, str], str] | None = None,
) -> None:
    from .outline import _analyze_structure, _build_venue_section, analysis_view

    litrev = load_litreview(project_dir, cfg.litrev_dir) if cfg.litrev_dir else ""
    code = load_methods(project_dir) if cfg.use_methods else ""
    results = load_results(project_dir, cfg.results_dir) if cfg.results_dir else ""
    figures = load_figure_manifest(project_dir, cfg.results_dir or "results")
    style_profile = load_style_profile(project_dir)

    log("[raconteur] analysing paper structure…")
    analysis = _analyze_structure(brain, cfg.description, litrev, code, results)

    venue_section = _build_venue_section(cfg, project_dir)
    style_section = _style_block(style_profile)
    figure_list = _figure_section(figures)

    prior, beat_notes, general = brief if brief else ({}, {}, "")
    general_block = _RECUT_GENERAL.format(notes=general) if general else ""

    beats: list[tuple[str, str]] = []
    for beat in _BEATS:
        can_embed = bool(figure_list) and beat.name == _FIGURE_BEAT
        recut_section = ""
        if brief:
            recut_section = _RECUT_BLOCK.format(
                beat=beat.name,
                prior=prior.get(beat.name, "(this beat did not exist)"),
                notes=beat_notes.get(beat.name, "(none on this beat)"),
            ) + general_block
        log(f"[raconteur] drafting beat '{beat.name}'…")
        text = brain.coordinator(
            _BEAT_PROMPT.format(
                beat=beat.name,
                intent=beat.intent,
                title=cfg.title,
                topic=cfg.topic,
                focus=cfg.focus,
                venue_section=venue_section,
                style_section=style_section,
                analysis=analysis_view(analysis, beat.drop),
                evidence_section=_evidence_for_beat(
                    beat.sources, cfg.description, litrev, code, results
                ),
                recut_section=recut_section,
                preceding_section=_preceding_block(beats),
                figure_section=figure_list if can_embed else "",
                figure_rule=_FIGURE_RULE if can_embed else _NO_FIGURE_RULE,
                extra_rules=f"\n{beat.rules}" if beat.rules else "",
            ),
            system=_SYSTEM,
            num_ctx=16384,
        )
        beats.append((beat.name, _strip_label(beat.name, text)))

    draft = _assemble(beats)

    log("[raconteur] critiquing one-pager…")
    critique = brain.coordinator(
        _CRITIQUE_PROMPT.format(onepager=draft),
        system=_SYSTEM,
        num_ctx=8192,
    )
    log(f"[raconteur] critique findings:\n{critique.strip()}")

    log("[raconteur] tightening one-pager…")
    tightened = brain.coordinator(
        _REVISE_PROMPT.format(onepager=draft, critique=critique),
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
    """Answer each anchored comment with an in-place tracked change (paper parity).

    Edits a copy of the reviewer's .docx — comments stay anchored and get
    dispositions, the reviewer's own tracked changes survive, and every
    un-flagged paragraph is byte-for-byte untouched. The accepted-text .md
    sibling is what 'outline' binds.
    """
    from .paper import _bib_block
    from .redline_revise import redline_revise

    litrev = load_litreview(project_dir, cfg.litrev_dir) if cfg.litrev_dir else ""
    code = load_methods(project_dir) if cfg.use_methods else ""
    results = load_results(project_dir, cfg.results_dir) if cfg.results_dir else ""
    bib_summary = load_bib_summary(project_dir, cfg.litrev_dir) if cfg.litrev_dir else ""
    bib_keys = load_bib_keys(project_dir, cfg.litrev_dir) if cfg.litrev_dir else set()

    def beat_context(heading: str, para_text: str) -> str:
        # The one-pager's structure lives in the paragraph's leading bold
        # label, not in headings — route each beat its own evidence bundle.
        t = para_text.lstrip("*-–—• ").lower()
        for beat in _BEATS:
            if t.startswith(beat.name.lower()):
                return _evidence_for_beat(
                    beat.sources, cfg.description, litrev, code, results
                )
        return ""

    redline_revise(project_dir, cfg, brain, paper_dir, user_rev,
                   litrev, code, results, _bib_block(bib_summary), bib_keys,
                   context_fn=beat_context, md_sibling=True)


# ── entry point ───────────────────────────────────────────────────────────────

def run(project_dir: Path, resynth: bool = False) -> None:
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
        if resynth:
            # Narrative-level rejection: the annotations are a brief for a fresh
            # cut, not line edits. Major version — new datestamp, chain reset.
            log("[raconteur] --resynth: re-cutting the narrative from the annotations")
            _onepager_fresh(project_dir, cfg, brain, paper_dir,
                            brief=_annotation_brief(user_rev))
        else:
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

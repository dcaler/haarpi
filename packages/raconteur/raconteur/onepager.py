from __future__ import annotations
import re
import shutil
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
- Keep the beat structure: each beat is a "## <beat name>" heading followed by 1-3 \
sentences of plain prose. No bold text.
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


def _preceding_block(beats: list[tuple[str, str]], frozen: set[str] = frozenset()) -> str:
    if not beats:
        return ""
    written = "\n".join(
        f"**{name}**{' (written by the author BY HAND — fixed, build around it)' if name in frozen else ''}"
        f" — {text}"
        for name, text in beats)
    return (
        "Beats already written (continue from these; do not repeat them):\n"
        f"{written}\n\n"
    )


_LABEL_DASHES = "—-–:"


def _strip_label(name: str, text: str) -> str:
    """Drop a beat label the model emitted despite being told not to."""
    t = text.strip()
    for prefix in (f"- **{name}**", f"**{name}**", f"## {name}", f"- {name}", name):
        if t.lower().startswith(prefix.lower()):
            t = t[len(prefix):].lstrip().lstrip(_LABEL_DASHES).lstrip()
            break
    return t


_FIG_LINE = re.compile(r"[ \t]*(!\[[^\]]*\]\([^)]+\))[ \t]*$", re.M)


def _assemble(beats: list[tuple[str, str]]) -> str:
    """Each beat under its own section heading, figures standing as their own blocks.

    Headings, not inline bold labels: a bold lead-in run would donate its
    formatting to any tracked replacement of the paragraph, and headings are
    what the redline reader routes by."""
    out = []
    for name, text in beats:
        body = _FIG_LINE.sub(r"\n\n\1\n", text.strip()).strip()
        out.append(f"## {name}\n\n{body}")
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


_REPLIES_PROMPT = """\
A one-pager was RE-CUT from scratch in response to a reviewer's annotations. \
For each reviewer comment below, write a 1-2 sentence reply saying specifically \
how the re-cut addresses it — or, if it does not, why.

Reviewer comments (id: text):
{comments}

The re-cut one-pager:
{onepager}

Return ONLY a JSON object mapping each comment id to its reply string."""

_FALLBACK_REPLY = ("Re-cut: the narrative was re-derived from the evidence with this "
                   "annotation in the brief — see the tracked replacement in this beat.")


def _draft_replies(brain: Brain, text: str, user_rev: Path) -> dict[str, str]:
    """One reply per reviewer comment; fail soft to a mechanical reply."""
    import json
    from . import redline
    from haarpi.redline import TOOL_AUTHORS

    tool_lower = {a.lower() for a in TOOL_AUTHORS}
    cmap = {cid: c for cid, c in redline.comments_by_id(user_rev).items()
            if (c.get("author") or "").lower() not in tool_lower}
    if not cmap:
        return {}
    listing = "\n".join(f"- {cid}: {c['text']}" for cid, c in cmap.items())
    raw = brain.coordinator(
        _REPLIES_PROMPT.format(comments=listing, onepager=text),
        system=_SYSTEM,
        num_ctx=8192,
    )
    try:
        m = re.search(r"\{.*\}", raw, re.S)
        parsed = json.loads(m.group(0)) if m else {}
    except Exception:
        parsed = {}
    return {cid: str(parsed.get(cid) or parsed.get(str(cid)) or _FALLBACK_REPLY)
            for cid in cmap}


def _strip_spurious_bold(doc) -> int:
    """Un-bold paragraphs whose every text run is bold. Returns paragraphs cleaned.

    Uniform run-level bold across a whole paragraph is the label era's scar: a
    bold lead-in label once donated its rPr to the full tracked replacement.
    The one-pager's body is plain prose — headings get their weight from their
    style, not their runs — so a wholly-bold paragraph is an artifact, while
    partial bold (real emphasis) is kept. Paragraphs carrying the reviewer's
    live tracked changes are left entirely alone — frozen means frozen, even
    for formatting; the scar heals there once the human accepts their edits."""
    from docx.oxml.ns import qn
    from haarpi import redline as hrl

    def _bolded(r):
        rpr = r.find(qn("w:rPr"))
        if rpr is None:
            return None
        b = rpr.find(qn("w:b"))
        return b if b is not None and b.get(qn("w:val")) not in ("0", "false", "none") else None

    cleaned = 0
    for p in doc.paragraphs:
        if hrl.has_foreign_markup(p._p):
            continue
        text_runs = [r for r in p._p.iter(qn("w:r"))
                     if any((t.text or "") for t in r.iter(qn("w:t")))
                     or any((t.text or "") for t in r.iter(qn("w:delText")))]
        if not text_runs or not all(_bolded(r) is not None for r in text_runs):
            continue
        for r in text_runs:
            rpr = r.find(qn("w:rPr"))
            for tag in ("w:b", "w:bCs"):
                el = rpr.find(qn(tag))
                if el is not None:
                    rpr.remove(el)
        cleaned += 1
    return cleaned


def _write_recut(project_dir: Path, cfg: ProjectConfig, paper_dir: Path,
                 brain: Brain, text: str, beats: list[tuple[str, str]],
                 user_rev: Path, frozen: dict[str, str] | None = None,
                 proposals: dict[str, str] | None = None) -> None:
    """Deliver the re-cut as a redline on the reviewer's own document.

    A re-cut replaces the narrative, but it must not replace the CONVERSATION,
    and it must not touch the reviewer's hand. Acceptance is a human act: the
    reviewer's tracked changes are never accepted here, and any beat carrying
    their live markup is FROZEN — left verbatim, markup and all. The tool's
    re-cut of a frozen beat, if the reviewer commented on it, arrives as an
    anchored comment (a proposal), never as an edit. Unfrozen beats are
    replaced as tracked changes; every reviewer comment gets a threaded reply.
    Major version — new datestamp, chain reset to onepager_ra — but the
    threads and the reviewer's own markup ride along into the new cycle.
    """
    from docx import Document
    from docx.oxml.ns import qn
    from . import redline as rl
    from haarpi import redline as hrl

    frozen = frozen or {}
    proposals = proposals or {}

    out = paper_dir / major_onepager_name(cfg.short_title, "docx")
    shutil.copy2(user_rev, out)

    doc = Document(str(out))
    if n := _strip_spurious_bold(doc):
        log(f"[raconteur] un-bolded {n} label-era paragraph(s)")
    ids = rl.ids_for(doc)

    final = _beats_from_md(text)             # beat -> body prose, no label/heading
    fallback = dict(beats)
    replaced, done = 0, set()
    frozen_hit: set[str] = set()
    frozen_anchor: dict[str, str] = {}       # beat -> visible text of its paragraph
    frozen_cids: set[str] = set()            # comment ids anchored in frozen beats
    for rec in rl.body_paragraphs(doc):
        # accepted text, not .text — a label the reviewer tracked in is
        # invisible to python-docx's .text
        accepted = rl._accepted_para_text(rec["para"]._p).strip()
        beat = _beat_of_para(rec["heading"], accepted)
        legacy = _label_led(accepted) is not None   # inline label → migrate it
        if beat is None:
            continue
        # Belt and braces: whatever the caller computed, a paragraph carrying
        # the reviewer's live markup is frozen. Acceptance is a human act.
        if beat in frozen or hrl.has_foreign_markup(rec["para"]._p):
            frozen_hit.add(beat)
            frozen_anchor.setdefault(
                beat, "".join(t.text or "" for t in rec["para"]._p.iter(qn("w:t"))))
            frozen_cids.update(
                s.get(qn("w:id"))
                for s in rec["para"]._p.findall(qn("w:commentRangeStart")))
            continue
        if beat in done:
            continue
        new = final.get(beat) or fallback.get(beat)
        if new and rl.tracked_replace_sentencewise(
                rec["para"]._p, new.replace("**", ""), rl.AUTHOR, ids):
            if legacy:
                # migrate: the inline label leaves with the diff, and the beat
                # name arrives above the paragraph as a real section heading
                hrl.tracked_heading_before(rec["para"]._p, beat, rl.AUTHOR, ids)
            replaced += 1
            done.add(beat)
    doc.save(str(out))
    log(f"[raconteur] wrote {out.relative_to(project_dir)} "
        f"(re-cut: {replaced} beat(s) replaced as tracked changes; "
        f"{len(frozen_hit)} author-edited beat(s) frozen verbatim)")

    log("[raconteur] drafting replies to the reviewer's comments…")
    replies = _draft_replies(brain, text, user_rev)
    for cid in frozen_cids & set(replies):
        replies[cid] = ("This beat carries your tracked edits, so it was left "
                        "untouched — see the re-cut proposal comment on this "
                        "paragraph. Acceptance is yours.")
    n = hrl.add_replies(out, replies, author=rl.AUTHOR)
    log(f"[raconteur] {n} threaded repl{'y' if n == 1 else 'ies'} written")

    notes = [(frozen_anchor[b],
              f"Re-cut proposal for the {b} beat — NOT applied, this paragraph "
              f"carries your edits and acceptance is yours:\n\n{p}")
             for b, p in proposals.items() if b in frozen_anchor]
    if notes:
        n = hrl.add_anchored_comments(out, notes, author=rl.AUTHOR)
        log(f"[raconteur] {n} re-cut proposal(s) delivered as comments on "
            f"frozen beat(s)")

    md_path = paper_dir / major_onepager_name(cfg.short_title, "md")
    md_path.write_text(rl.accepted_markdown(Document(str(out))).strip() + "\n",
                       encoding="utf-8")
    log(f"[raconteur] wrote {md_path.relative_to(project_dir)} "
        f"(accepted view of the delivered redline)")


def _beat_of(text: str) -> str | None:
    """The beat a heading or a legacy label-led paragraph belongs to."""
    t = text.lstrip("#*-–—• ").lower()
    for beat in _BEATS:
        if t.startswith(beat.name.lower()):
            return beat.name
    return None


def _label_led(text: str) -> str | None:
    """The beat of a paragraph carrying an EXPLICIT inline label ('Gap — …').

    Stricter than _beat_of: the beat name must be followed by a separator, so
    body prose that merely opens with a beat word is never mistaken for a
    label. An explicit label outranks heading scoping — a frozen legacy
    paragraph sitting under some other beat's migrated heading still belongs
    to ITS beat."""
    t = text.lstrip("#*-–—• ").strip()
    for beat in _BEATS:
        if t.lower().startswith(beat.name.lower()):
            rest = t[len(beat.name):].lstrip("*").lstrip()
            if rest[:1] in _LABEL_DASHES:
                return beat.name
    return None


def _beat_of_para(heading: str, text: str) -> str | None:
    """The beat a document paragraph belongs to: inline label first, then heading."""
    return _label_led(text) or _beat_of(heading or "")


def _beats_from_md(md: str) -> dict[str, str]:
    """Beat-name -> body prose (heading/label stripped), from beat-structured markdown.

    Understands both shapes: '## Gap' section headings with the body in the
    blocks that follow (current), and legacy '**Gap** — …' label-led paragraphs.
    Mixed documents are real: a re-cut migrates replaced beats to headings while
    the author's frozen beats keep their legacy label paragraphs. So heading-mode
    still adopts a label-led paragraph that sits OUTSIDE any beat section, for a
    beat no heading has claimed — but body prose inside a headed section is never
    mistaken for a label."""
    blocks = [b.strip() for b in md.split("\n\n") if b.strip()]
    out: dict[str, str] = {}
    if any(b.startswith("#") and _beat_of(b) for b in blocks):
        current: str | None = None
        for b in blocks:
            if b.startswith("#"):
                beat = _beat_of(b)
                current = beat if beat and beat not in out else None
                continue
            lab = _label_led(b)          # a legacy label paragraph among headed
            if lab and lab not in out:   # beats — a frozen beat, kept verbatim;
                out[lab] = _strip_label(lab, b)   # its label outranks the
                current = None                     # enclosing heading's scope
                continue
            if current:
                out[current] = f"{out[current]}\n\n{b}" if current in out else b
        return out
    for b in blocks:
        beat = _beat_of(b)
        if beat and beat not in out:
            out[beat] = _strip_label(beat, b)
    return out


def _frozen_beats(user_rev: Path) -> dict[str, str]:
    """Beats the reviewer's hand is in: any live human tracked change freezes them.

    Returns beat -> the beat's accepted body text (the fixed point the rest of
    the narrative is regenerated around). Human text is the most expensive text
    in the system; the re-cut never rewrites it and never accepts it — it
    builds around it, and argues only in comments."""
    from docx import Document
    from . import redline as rl
    from haarpi import redline as hrl

    out: dict[str, list[str]] = {}
    markup: set[str] = set()
    for rec in rl.body_paragraphs(Document(str(user_rev))):
        # accepted text, not .text — a label the reviewer tracked in is
        # invisible to python-docx's .text
        accepted = rl._accepted_para_text(rec["para"]._p).strip()
        beat = _beat_of_para(rec["heading"], accepted)
        if beat is None:
            continue
        if _label_led(accepted):
            accepted = _strip_label(beat, accepted)
        out.setdefault(beat, []).append(accepted)
        if hrl.has_foreign_markup(rec["para"]._p):
            markup.add(beat)
    return {b: "\n\n".join(t for t in out[b] if t) for b in markup}


def _adopt_title(project_dir: Path, cfg: ProjectConfig, user_rev: Path) -> None:
    """If the reviewer retitled the document, the config follows.

    The title is config-owned (every _write emits `# {cfg.title}`), so a tracked
    change to the heading can only persist by updating cfg.title itself.
    """
    from docx import Document
    from . import redline
    md = redline.accepted_markdown(Document(str(user_rev)))
    for line in md.splitlines():
        if line.startswith("#"):
            if _beat_of(line):
                continue   # a beat heading is never the title
            title = re.sub(r"\s*[—–-]+\s*one-pager\s*$", "", line.lstrip("# ").strip())
            if title and title != cfg.title:
                log(f"[raconteur] adopting reviewer's title: {title!r}")
                cfg.title = title
                cfg.save(project_dir)
            return


def _annotation_brief(user_rev: Path) -> tuple[dict[str, str], dict[str, str], str]:
    """The re-cut briefing from the reviewer's docx.

    Returns (prior, notes, general): each beat's previous text with the
    reviewer's tracked changes accepted, the comments anchored to it, and the
    annotations that anchor to no recognisable beat (title, figures) — those
    apply to the whole narrative.
    """
    from docx import Document
    from . import redline

    prior = _beats_from_md(redline.accepted_markdown(Document(str(user_rev))))

    notes: dict[str, str] = {}
    general: list[str] = []
    cmap = redline.comments_by_id(user_rev)
    for anchor in redline.comment_anchors(user_rev):
        comments = [cmap[c]["text"] for c in anchor["ids"] if c in cmap]
        if not comments:
            continue
        block = "\n".join(f"- {c}" for c in comments)
        beat = _beat_of_para(anchor.get("heading") or "", anchor["text"])
        if beat is None:
            general.append(block)
        else:
            notes[beat] = f"{notes[beat]}\n{block}" if beat in notes else block
    for h in redline.heading_comments(user_rev):
        comments = [cmap[c]["text"] for c in h["ids"] if c in cmap]
        if not comments:
            continue
        block = "\n".join(f"- {c}" for c in comments)
        beat = _beat_of(h["heading"])
        if beat is None:
            general.append(block)
        else:
            notes[beat] = f"{notes[beat]}\n{block}" if beat in notes else block
    return prior, notes, "\n".join(general)


# ── fresh one-pager ───────────────────────────────────────────────────────────

def _onepager_fresh(
    project_dir: Path, cfg: ProjectConfig, brain: Brain, paper_dir: Path,
    brief: tuple[dict[str, str], dict[str, str], str] | None = None,
    user_rev: Path | None = None,
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

    # The reviewer's hand freezes a beat: it is a fixed point the rest of the
    # narrative is regenerated around, never a candidate for rewriting.
    frozen = _frozen_beats(user_rev) if user_rev is not None else {}
    frozen_names = set(frozen)

    beats: list[tuple[str, str]] = []
    proposals: dict[str, str] = {}
    for beat in _BEATS:
        can_embed = bool(figure_list) and beat.name == _FIGURE_BEAT
        recut_section = ""
        if brief:
            recut_section = _RECUT_BLOCK.format(
                beat=beat.name,
                prior=prior.get(beat.name, "(this beat did not exist)"),
                notes=beat_notes.get(beat.name, "(none on this beat)"),
            ) + general_block
        prompt = _BEAT_PROMPT.format(
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
            preceding_section=_preceding_block(beats, frozen_names),
            figure_section=figure_list if can_embed else "",
            figure_rule=_FIGURE_RULE if can_embed else _NO_FIGURE_RULE,
            extra_rules=f"\n{beat.rules}" if beat.rules else "",
        )
        if beat.name in frozen:
            if beat.name in beat_notes:
                log(f"[raconteur] beat '{beat.name}' carries the author's edits — "
                    f"frozen; drafting a proposal for their comments…")
                ptext = brain.coordinator(prompt, system=_SYSTEM, num_ctx=16384)
                proposals[beat.name] = _strip_label(beat.name, ptext)
            else:
                log(f"[raconteur] beat '{beat.name}' carries the author's edits — "
                    f"frozen, kept verbatim")
            beats.append((beat.name, frozen[beat.name]))
            continue
        log(f"[raconteur] drafting beat '{beat.name}'…")
        text = brain.coordinator(prompt, system=_SYSTEM, num_ctx=16384)
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
    tighten_prompt = _REVISE_PROMPT.format(onepager=draft, critique=critique)
    if frozen_names:
        tighten_prompt += ("\n\nThese beats were written by the author BY HAND — "
                           "reproduce them VERBATIM, whatever the critique says: "
                           + ", ".join(sorted(frozen_names)) + ".")
    tightened = brain.coordinator(tighten_prompt, system=_SYSTEM, num_ctx=8192)
    if user_rev is not None:
        _write_recut(project_dir, cfg, paper_dir, brain, tightened, beats, user_rev,
                     frozen=frozen, proposals=proposals)
    else:
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
        # Beats live under their own section headings; legacy one-pagers carry
        # the beat name as an inline lead-in label instead. Either way, route
        # each beat its own evidence bundle.
        name = _beat_of_para(heading, para_text)
        for beat in _BEATS:
            if beat.name == name:
                return _evidence_for_beat(
                    beat.sources, cfg.description, litrev, code, results
                )
        return ""

    out, _ = redline_revise(project_dir, cfg, brain, paper_dir, user_rev,
                            litrev, code, results, _bib_block(bib_summary), bib_keys,
                            context_fn=beat_context, md_sibling=True)

    # Label-era documents carry wholly-bold beat paragraphs, and any tracked
    # edit written into one inherited that bold — normalise the delivered copy.
    from docx import Document
    doc = Document(str(out))
    if n := _strip_spurious_bold(doc):
        doc.save(str(out))
        log(f"[raconteur] un-bolded {n} label-era paragraph(s)")


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
            # cut, not line edits. Major version — new datestamp, chain reset —
            # but delivered as a redline on the reviewer's file: their edits
            # persist, their comment threads ride along and get replies.
            log("[raconteur] --resynth: re-cutting the narrative from the annotations")
            _adopt_title(project_dir, cfg, user_rev)
            _onepager_fresh(project_dir, cfg, brain, paper_dir,
                            brief=_annotation_brief(user_rev), user_rev=user_rev)
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

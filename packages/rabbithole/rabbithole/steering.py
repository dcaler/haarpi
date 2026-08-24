"""Litreview config-steering helpers — write a new numbered litrev_<N>.yaml that aims the
next `gather`/`report` at what the reviewer asked for.

Planning itself no longer lives here. `haarpi next` (haarpi.planner) is the sole planner:
it decomposes the annotations, derives the chain, and queues it. These helpers are the one
piece that stayed in rabbitHole because they write litreview's OWN config format — haarpi
soft-imports them (rabbithole.steering) exactly as it soft-imports raconteur for the paper
ladder, so a stack without rabbitHole degrades quietly.

The focus line is the only channel between an annotated docx and a later `report`, which
re-plans from the corpus and never reads the docx.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import config


def _norm_focus(s: str) -> str:
    """Comparison form for a focus clause: case- and punctuation-insensitive word sequence."""
    return " ".join(re.sub(r"[^\w\s]", " ", (s or "").lower()).split())


def _append_focus(cfg, *additions: str) -> None:
    """Append each addition to the focus line, skipping anything already said.

    The focus is CUMULATIVE across cycles and is fed verbatim to every later gather, so a
    duplicate is not cosmetic — it re-weights the retrieval toward whatever got said twice,
    and the line grows without bound. It duplicated readily because one caller passes the same
    query twice: a `section` task's query reaches `_write_gap_config` both as
    "Expand coverage of: <q>" (via gather_topics) and bare (via extra_focus).

    A clause is skipped when its normalised words already appear in the focus — which catches
    the bare/prefixed pair, since "expand coverage of: X" contains "X".
    """
    for addition in additions:
        if not addition or not addition.strip():
            continue
        seen = _norm_focus(cfg.focus)
        norm = _norm_focus(addition)
        if norm and norm in seen:
            continue
        cfg.focus = f"{cfg.focus}; {addition}" if cfg.focus else addition


def _sub_term(text: str, wrong: str, right: str) -> tuple[str, int]:
    """Case-insensitive whole-term substitution, tolerant of the separators a name picks up.

    A model name travels as "Dosi-Stiglitz-Keynes", "Dosi–Stiglitz–Keynes" and
    "Dosi Stiglitz Keynes" in the same project, so matching the literal string finds one of the
    three. Every run of hyphen/en-dash/whitespace between the term's words is treated as one
    separator, and the match is bounded so a term never fires inside a longer word.
    """
    if not text or not wrong:
        return text or "", 0
    parts = [re.escape(w) for w in re.split(r"[\s‐-―-]+", wrong.strip()) if w]
    if not parts:
        return text, 0
    pat = re.compile(r"(?<!\w)" + r"[\s‐-―-]+".join(parts) + r"(?!\w)", re.I)
    return pat.subn(right, text)


def apply_correction(directory: str, wrong: str, right: str) -> dict[str, int]:
    """Replace a term everywhere the litreview stage will read it again.

    A reviewer who corrects a name is not asking for a paragraph to be rewritten — they are
    saying the project has the fact wrong. Fixing only the anchored paragraph is why one
    project carried a wrong model name through four cycles: the term also sat in the config's
    topic/focus/research_prompt, which is what gather searches with and what a re-draft plans
    from, so every cycle re-injected it. This is deliberately a deterministic substitution, not
    a model call: a naming fix has one right answer and a reviser that finds five of six
    occurrences is worse than useless.

    Returns a per-target count of substitutions made.
    """
    counts: dict[str, int] = {}
    prev = config.load_project(directory)
    total = 0
    for field in ("topic", "focus", "research_prompt", "domain_anchor", "exclude_topics"):
        new, n = _sub_term(getattr(prev, field, "") or "", wrong, right)
        if n:
            setattr(prev, field, new)
            total += n
    if total:
        fp = config.next_project_file(directory)
        config.save_project_to(prev, fp)
        counts[fp.name] = total

    # The current draft too, or the reviewer gets back the document they just corrected with the
    # error still in it everywhere they did not put a comment. This is the half a span-local
    # reviser gets wrong: one project had the term in six places and one comment on it.
    for md in _draft_reviews(directory):
        new, n = _sub_term(md.read_text(encoding="utf-8"), wrong, right)
        if not n:
            continue
        md.write_text(new, encoding="utf-8")
        counts[md.name] = counts.get(md.name, 0) + n
        docx = md.with_suffix(".docx")
        try:
            from .render import pandoc_convert
            pandoc_convert(md, docx)
        except Exception:  # noqa: BLE001 — the markdown is corrected either way
            pass
    return counts


def _draft_reviews(directory: str) -> list[Path]:
    """The tool's own current litreview drafts (``*_litreview_ra.md``) — never a human's markup.

    A file whose trailing suffix is not ``ra`` was last touched by the reviewer, and rewriting
    it would edit their copy underneath them.
    """
    try:
        out = config.project_paths(directory).output
    except Exception:  # noqa: BLE001
        return []
    return [p for p in sorted(out.glob("*_litreview_ra.md"))] if out.is_dir() else []


def _write_gap_config(directory: str, plan: dict, extra_focus: str = "") -> Path:
    """Write a new numbered litrev config whose focus steers gather at the gaps.

    `extra_focus` carries a section the reviewer asked for. The focus line is the only
    channel between an annotated docx and a later `report`, which re-plans from the corpus
    and never reads the docx.
    """
    prev = config.load_project(directory)
    topics = ", ".join(t for t in plan.get("gather_topics", []) if t)
    addition = plan.get("focus_addition") or (f"Expand coverage of: {topics}" if topics else "")
    _append_focus(prev, addition, extra_focus)
    fp = config.next_project_file(directory)
    return config.save_project_to(prev, fp)


def _write_section_config(directory: str, extra_focus: str) -> Path:
    """Write a new numbered litrev config that asks `report` for a section, nothing else.

    The corpus already holds the evidence — no gather is needed — but a redline cannot add a
    section, so the review has to be re-planned. This is the config that tells the planner why.
    """
    prev = config.load_project(directory)
    _append_focus(prev, extra_focus)
    fp = config.next_project_file(directory)
    return config.save_project_to(prev, fp)


def _write_redirect_config(directory: str, plan: dict, extra_focus: str = "") -> Path:
    """Write a new iterated litrev config that re-aims the project at the reviewer's
    redirected research question.

    Unlike gap_fill (which only appends a focus line to the same brief), a redirection
    REWRITES the brief. research_prompt is the source of truth gather extracts topic/
    focus from, so we overwrite all three — topic, focus, and research_prompt — with the
    reframe. The new file is a fresh iteration (litrev_<N+1>.yaml) that gather uses on the
    next run; inspect or edit it whenever. Project binding (name, trundlr id, models, source
    policy) is inherited from the previous config untouched.

    `extra_focus` (a section the reviewer asked for) is appended AFTER the reframe, so a
    section request survives a change of direction rather than being overwritten by it.
    """
    prev = config.load_project(directory)
    new_topic = (plan.get("new_topic") or "").strip()
    new_focus = (plan.get("new_focus") or "").strip()
    new_prompt = (plan.get("new_research_prompt") or "").strip()
    if new_topic:
        prev.topic = new_topic
    if new_focus:
        prev.focus = new_focus
    if new_prompt:
        prev.research_prompt = new_prompt
    _append_focus(prev, extra_focus)
    fp = config.next_project_file(directory)
    return config.save_project_to(prev, fp)

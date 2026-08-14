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

from pathlib import Path

from . import config


def _append_focus(cfg, *additions: str) -> None:
    for addition in additions:
        if addition:
            cfg.focus = f"{cfg.focus}; {addition}" if cfg.focus else addition


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

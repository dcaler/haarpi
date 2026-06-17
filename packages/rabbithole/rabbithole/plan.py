"""rabbitHole parseNplan — read reviewer annotations, decide what work is needed,
and queue it into trundlr as a dependency chain.

The coordinator classifies the annotations into one of three tiers:

  cosmetic     reword/restructure only          -> revise -> comment
  gap_fill     "more on X", X absent from corpus -> gather -> collect -> revise -> comment
  redirection  new direction / wrong scope       -> init -> gather -> collect -> revise -> comment

parseNplan never runs gather/revise itself. Commanded steps (gather, revise) are
queued with a shell command and assigned to the trundlr runner resource, which
executes them once their dependency is done. Human steps (init, collect, comment)
carry no command and wait in the queue until you mark them done. The `init` step
(redirection only) is what mints a new datestamp — a new major revision cycle.
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from pathlib import Path

from . import config, docxio, notify
from .brain import Brain
from .models import Candidate
from .revise import _load_corpus

# Pipeline per tier (order matters; each step depends on the previous one).
_PIPELINE = {
    "cosmetic":    ["revise", "comment"],
    "gap_fill":    ["gather", "collect", "revise", "comment"],
    "redirection": ["init", "gather", "collect", "revise", "comment"],
}

# Per-step metadata. `human` steps carry no command (you do them); the rest are
# run by the trundlr runner. `verb` is the rabbitHole subcommand for runner steps.
# `hours` is a scheduling estimate (trundlr durations are in hours) so reflow can
# lay the chain out on the timeline.
_STEP = {
    "init":    {"human": True,  "verb": None,    "hours": 1.0,
                "desc": "Re-run `rabbitHole init` to set the new research direction (new datestamp)."},
    "gather":  {"human": False, "verb": "gather", "hours": 1.0,
                "desc": "Discover & curate new sources into the Zotero collection."},
    "collect": {"human": True,  "verb": None,    "hours": 0.5,
                "desc": "Download the new PDFs and add them to the Zotero collection."},
    "revise":  {"human": False, "verb": "revise", "hours": 1.5,
                "desc": "Re-draft the review from the expanded corpus + your annotations."},
    "comment": {"human": True,  "verb": None,    "hours": 0.5,
                "desc": "Review the new draft and annotate it."},
}

PLAN_SYS = (
    "You are a research-project planner for a literature-review assistant. "
    "You read a reviewer's annotations on a draft literature review and decide "
    "what work is needed next. You respond with a single JSON object and nothing else."
)

_PLAN_PROMPT = """\
Review topic: {topic}
Focus: {focus}

The current corpus already covers these sources:
{coverage}

A reviewer annotated the latest draft. Their annotations:
{revision_context}

Decide which ONE tier of work the annotations require:

- "cosmetic": only rewording, restructuring, or clarification. No new sources needed.
- "gap_fill": the reviewer wants more on one or more topics NOT already well
  covered by the corpus above. New sources must be gathered.
- "redirection": the reviewer signals the review is aimed wrong or needs a
  fundamentally new direction or scope. This needs a fresh research brief.

Rules:
- Choose "gap_fill" ONLY if the requested topic is genuinely absent from the
  corpus above. If it is already covered, prefer "cosmetic".
- Choose "redirection" only for a genuine change of direction, not just "add more".

Respond with a single JSON object:
{{
  "tier": "cosmetic" | "gap_fill" | "redirection",
  "assessment": "1-3 sentences explaining the decision",
  "gather_topics": ["specific search topics to fill the gaps"],
  "focus_addition": "one line to steer the next search toward the gaps, or empty"
}}
gather_topics and focus_addition are only needed for gap_fill or redirection."""


# ── coverage + plan ────────────────────────────────────────────────────────────

def _coverage_summary(corpus: list[Candidate], cap: int = 60) -> str:
    if not corpus:
        return "(corpus is empty)"
    lines = []
    for c in corpus[:cap]:
        title = (c.title or "untitled").strip()
        if len(title) > 100:
            title = title[:100] + "…"
        lines.append(f"- {c.author_year()}: {title}")
    if len(corpus) > cap:
        lines.append(f"- … and {len(corpus) - cap} more")
    return "\n".join(lines)


def _extract_json_object(text: str) -> dict:
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object in model output")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError("no complete JSON object in model output")


def _make_plan(brain: Brain, cfg, coverage: str, revision_context: str) -> dict:
    prompt = _PLAN_PROMPT.format(
        topic=cfg.topic or "(unspecified)",
        focus=cfg.focus or "(none)",
        coverage=coverage,
        revision_context=revision_context,
    )
    raw = brain.coordinator(prompt, PLAN_SYS, num_ctx=16384)
    try:
        plan = _extract_json_object(raw)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"[error] could not parse plan from model output: {e}", file=sys.stderr)
        print(f"--- raw output ---\n{raw[:1000]}", file=sys.stderr)
        raise SystemExit(1)
    tier = plan.get("tier")
    if tier not in _PIPELINE:
        print(f"[warn] model returned unknown tier {tier!r}; defaulting to 'cosmetic'.",
              file=sys.stderr)
        plan["tier"] = "cosmetic"
    plan.setdefault("assessment", "")
    plan.setdefault("gather_topics", [])
    plan.setdefault("focus_addition", "")
    return plan


# ── config steering for gap_fill ───────────────────────────────────────────────

def _write_gap_config(directory: str, plan: dict) -> Path:
    """Write a new numbered litrev config whose focus steers gather at the gaps."""
    prev = config.load_project(directory)
    topics = ", ".join(t for t in plan.get("gather_topics", []) if t)
    addition = plan.get("focus_addition") or (f"Expand coverage of: {topics}" if topics else "")
    if addition:
        prev.focus = f"{prev.focus}; {addition}" if prev.focus else addition
    fp = config.next_project_file(directory)
    return config.save_project_to(prev, fp)


# ── rendering ──────────────────────────────────────────────────────────────────

def _print_plan(cfg, docx: Path, plan: dict, steps: list[str]) -> None:
    print(f"rabbitHole parseNplan — {cfg.project_name}")
    print(f"  Annotated file: {docx.name}")
    print()
    print(f"  Tier: {plan['tier']}")
    if plan.get("assessment"):
        print(f"  Assessment: {plan['assessment']}")
    if plan["tier"] != "cosmetic" and plan.get("gather_topics"):
        print("  Gather topics:")
        for t in plan["gather_topics"]:
            print(f"    • {t}")
    print()
    print("  Planned pipeline:")
    for i, step in enumerate(steps, 1):
        meta = _STEP[step]
        who = "you" if meta["human"] else "runner"
        dep = f"  (after step {i - 1})" if i > 1 else ""
        print(f"    {i}. {step:<8} [{who}] {meta['desc']}{dep}")


def _build_command(step: str) -> str | None:
    """Command for a runner step. The trundlr runner cd's into the project's
    folder before running, so a bare `rabbitHole <verb>` is sufficient."""
    verb = _STEP[step]["verb"]
    return f"rabbitHole {verb}" if verb else None


def _next_index(titles: list[str], step: str) -> int:
    """Next per-step number for a `lit review <step> <N>` title in this project."""
    pat = re.compile(rf"^lit review {re.escape(step)} (\d+)$", re.I)
    nums = [int(m.group(1)) for t in titles for m in [pat.match(t.strip())] if m]
    return max(nums, default=0) + 1


def _estimate_hours(tasks: list[dict], step: str) -> float:
    """Median actual duration (hours) of past completed tasks of this step,
    pooled across all projects.

    trundlr records the realised hours in the `duration` field once a task is
    done, so the schedule estimate self-tunes from history. Falls back to the
    static per-step default when there is no completed task to learn from."""
    pat = re.compile(rf"^lit review {re.escape(step)} (\d+)$", re.I)
    durs = [float(t["duration"]) for t in tasks
            if t.get("status") == "done"
            and pat.match((t.get("title") or "").strip())
            and isinstance(t.get("duration"), (int, float)) and t["duration"] > 0]
    return round(statistics.median(durs), 3) if durs else _STEP[step]["hours"]


# ── trundlr submission ─────────────────────────────────────────────────────────

def _submit_chain(gc, cfg, directory: str, steps: list[str], plan: dict) -> int:
    from .trundlr import TrundlrClient, TrundlrError
    try:
        tc = TrundlrClient(gc)
        proj = tc.project_by_name(cfg.project_name)
        if proj is None:
            proj = tc.create_project(
                cfg.project_name, folder=str(Path(directory).resolve()),
                description="rabbitHole literature review")
            print(f"  [trundlr] created project '{cfg.project_name}' (id {proj['id']})")
        project_id = proj["id"]

        # Cache the project id back to the *current* latest config (which may be
        # the gap-steering file just written), without clobbering its other fields.
        latest = config.load_project(directory)
        if latest.trundlr_project_id != project_id:
            latest.trundlr_project_id = project_id
            config.save_project(latest, directory)

        # Project tasks drive the per-step `lit review <step> <N>` numbering;
        # duration estimates pool completed tasks across all projects.
        titles = [t.get("title", "") for t in tc.tasks_for_project(project_id)]
        history = tc.all_tasks()

        prev_id = None
        for step in steps:
            meta = _STEP[step]
            command = _build_command(step)
            if meta["human"]:
                resource_id = gc.trundlr_human_resource_id  # Cale; runner ignores it
            else:
                resource_id = gc.trundlr_runner_resource_id
                if resource_id is None:
                    raise TrundlrError(
                        "commanded task needs a runner resource — set "
                        "[trundlr] runner_resource_id in config.toml")
            idx = _next_index(titles, step)
            title = f"lit review {step} {idx}"
            hours = _estimate_hours(history, step)
            task = tc.create_task(
                title=title,
                project_id=project_id,
                command=command,
                depends_on_id=prev_id,
                description=meta["desc"],
                resource_id=resource_id,
                duration=hours,
            )
            titles.append(title)  # keep numbering correct if a step repeats in-chain
            prev_id = task["id"]
            tag = "Cale" if meta["human"] else "runner"
            print(f"  [trundlr] queued #{task['id']} '{title}' [{tag}] ~{hours}h"
                  + (f" depends-on #{task['depends_on_id']}" if task.get("depends_on_id") else ""))
        return 0
    except TrundlrError as e:
        print(f"[warn] trundlr submission failed: {e}", file=sys.stderr)
        _print_manual(steps)
        return 1


def _print_manual(steps: list[str]) -> None:
    print("\n  Could not queue tasks — run these steps manually, in order:")
    for i, step in enumerate(steps, 1):
        meta = _STEP[step]
        verb = meta["verb"]
        cmd = f"rabbitHole {verb}" if verb else f"(manual) {meta['desc']}"
        print(f"    {i}. {cmd}")


# ── orchestration ──────────────────────────────────────────────────────────────

def run(directory: str = ".", brain_override: str | None = None,
        docx_path: str | None = None, dry_run: bool = False,
        use_trundlr: bool = True) -> int:
    docxio.require_docx()

    cfg = config.load_project(directory)
    gc = config.load_global()
    paths = config.project_paths(directory)

    # 1. Find the annotated docx
    docx = Path(docx_path) if docx_path else docxio.find_annotated_docx(paths)
    if not docx or not docx.exists():
        print("[error] No annotated .docx found in output/. "
              "Annotate a draft (e.g. *_DCR.docx) or pass --file.", file=sys.stderr)
        return 1

    # 2. Extract annotations
    revision_context = docxio.build_revision_context(docx)
    if not revision_context:
        print("[warn] No tracked changes or comments found in the docx. Nothing to plan.")
        return 0

    # 3. Corpus coverage
    corpus = _load_corpus(paths)
    coverage = _coverage_summary(corpus)

    # 4. Coordinator plan
    print("  Reading annotations and planning (coordinator)…", flush=True)
    brain = Brain(cfg.brain, gc, backend_override=brain_override)
    plan = _make_plan(brain, cfg, coverage, revision_context)
    tier = plan["tier"]
    steps = _PIPELINE[tier]

    print()
    _print_plan(cfg, docx, plan, steps)

    if dry_run:
        print("\n  [dry-run] No config written and no tasks queued.")
        return 0

    # 5. gap_fill steers the next gather via a new numbered config
    if tier == "gap_fill":
        fp = _write_gap_config(directory, plan)
        print(f"\n  Wrote gather-steering config: {fp.name}")

    # 6. Queue the chain in trundlr (or print manual steps)
    print()
    if use_trundlr and gc.have_trundlr:
        rc = _submit_chain(gc, cfg, directory, steps, plan)
    else:
        if use_trundlr and not gc.have_trundlr:
            print("  [trundlr] not configured ([trundlr] url in config.toml).")
        _print_manual(steps)
        rc = 0

    # 7. Notify
    topics = ", ".join(plan.get("gather_topics", [])) or "(none)"
    notify.send_email(
        f"rabbitHole parseNplan: {cfg.project_name} ({tier})",
        (f"Planned next steps for '{cfg.project_name}' from {docx.name}.\n\n"
         f"Tier: {tier}\n"
         f"Assessment: {plan.get('assessment', '')}\n"
         f"Gather topics: {topics}\n"
         f"Pipeline: {' -> '.join(steps)}\n"),
        gc,
    )
    return rc

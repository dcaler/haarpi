"""rabbitHole parseNplan — read reviewer annotations, decide what work is needed,
and queue it into trundlr as a dependency chain.

The coordinator classifies the annotations into one of three tiers:

  cosmetic     reword/restructure only          -> revise -> comment
  gap_fill     "more on X", X absent from corpus -> gather -> collect -> revise -> comment
  redirection  new direction / wrong scope       -> gather -> collect -> revise -> comment

Orthogonally, if the reviewer pasted references into the draft ("add these citations" /
work from a related project), an `ingest` step is prepended to the chain (and a `collect`
step guaranteed before `revise`): ingest pulls the Zotero-matched references into the
corpus so revise can cite them, and lists the rest for you to add at the collect step.

parseNplan never runs gather/revise itself. Commanded steps (gather, revise) are
queued with a shell command and assigned to the trundlr runner resource, which
executes them once their dependency is done. Human steps (collect, comment) carry
no command and wait in the queue until you mark them done.

On a redirection, parseNplan does the reframe itself rather than bouncing it back:
the coordinator that recognised the new direction also rewrites the project brief
(topic, focus, research_prompt) and writes it to a new iterated litrev_<N>.yaml, so
the fresh gather is aimed at the reviewer's new research question automatically. No
manual re-init step — redirection runs the same hands-off chain as gap_fill; the new
litrev_<N>.yaml is there to inspect or edit whenever you like.
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from pathlib import Path

from . import config, docxio, notify, runlog
from .brain import Brain
from .models import Candidate
from .revise import _load_corpus

# Pipeline per tier (order matters; each step depends on the previous one).
#
# Every chain used to end in `revise`, which is the in-place redline: it rewrites the
# paragraph a comment anchors to. A redline cannot ADD A SECTION — so a comment like "I want
# a section on X" was classified correctly as gap_fill, gathered the right sources, and then
# handed them to a step structurally incapable of using them. `report` appears in no chain at
# all. `_chain_for` swaps in `report` when the redline reported a comment it could not satisfy
# because it asked for a new section (the audit's `CORPUS: section` verdict).
_PIPELINE = {
    "cosmetic":    ["revise", "comment"],
    "gap_fill":    ["gather", "collect", "revise", "comment"],
    "redirection": ["gather", "collect", "revise", "comment"],
}

# Per-step metadata. `human` steps carry no command (you do them); the rest are
# run by the trundlr runner. `verb` is the rabbitHole subcommand for runner steps.
# `hours` is a scheduling estimate (trundlr durations are in hours) so reflow can
# lay the chain out on the timeline.
_STEP = {
    # `hours` defaults are cold-start fallbacks only — once a step has completed
    # tasks in trundlr, _estimate_hours learns from history instead. These values
    # track the realised medians as of 2026-06 (gather/revise grew heavier with the
    # multi-query + two-pass-synthesis upgrades; the human steps run quick).
    "ingest":  {"human": False, "verb": "ingest", "hours": 0.5,
                "desc": "Pull the reviewer-supplied references into the corpus (Zotero "
                        "matches) and list the rest for the collect step."},
    "gather":  {"human": False, "verb": "gather", "hours": 1.3,
                "desc": "Discover & curate new sources into the Zotero collection."},
    "collect": {"human": True,  "verb": None,    "hours": 0.25,
                "desc": "Download the new PDFs and add them to the Zotero collection."},
    "revise":  {"human": False, "verb": "revise", "hours": 4.0,
                "desc": "Re-draft the review from the expanded corpus + your annotations."},
    "report":  {"human": False, "verb": "report", "hours": 3.0,
                "desc": "Re-plan the review's sections and re-synthesise from the corpus. "
                        "Starts a new revision cycle: it does NOT read the redline, so the "
                        "reviewer's intent must reach it through the project config."},
    "comment": {"human": True,  "verb": None,    "hours": 0.15,
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

Sources already in the corpus — use this to TARGET gathering, NOT to conclude a topic
needs no more work. Presence is not sufficiency: a topic can have many sources and
still be too shallow for what the reviewer wants.
{coverage}

A reviewer annotated the latest draft. Their annotations:
{revision_context}

Pick the ONE tier matching the MOST substantive work that ANY annotation asks for. A
single request for more research outranks a pile of wording notes — choose the heavier
tier, because a lighter pipeline cannot perform the heavier work.

- "cosmetic": EVERY annotation can be satisfied by editing what is already written —
  rewording, restructuring, clarifying, defining a term, adding an example the corpus
  already supports, or cutting irrelevant material. No new sources needed.
- "gap_fill": at least one annotation asks for MORE substance on some topic — "more on
  X", "a lot more on X", "go deeper", "expand", "what about Y", or a new connection that
  needs evidence to support it. This holds EVEN IF the topic is already in the corpus:
  "a lot more on Schelling" when Schelling is already cited still means gather more and
  develop it further. Gather new sources.
- "redirection": at least one annotation signals the review is aimed wrong or needs a
  fundamentally different scope or research question. Needs a fresh research brief.

Rules:
- Do NOT downgrade to "cosmetic" just because the requested topic already has sources.
  "More on X" is gap_fill whether or not X is present — the reviewer wants greater depth
  or breadth than the current draft delivers.
- Choose "redirection" only for a genuine change of direction, not for "add more".

For gap_fill or redirection, set gather_topics to specific, searchable topics that would
deepen exactly what the reviewer asked for — lean on the corpus list above to avoid
re-finding what is already there and to aim at the thin spots.

For redirection ONLY, also rewrite the project brief so the next cycle is aimed at the
reviewer's NEW research question, not the old one. Set new_topic (the reframed question in
one line), new_focus (the new emphasis/scope in one line), and new_research_prompt (a
self-contained 3-6 sentence brief written in the reviewer's new direction, as if briefing
the search from scratch — state the new unit of analysis, the question, and what counts as
relevant). The reviewer's annotation already gives you the new direction; carry it through
faithfully. These fields become a new iterated project config that re-aims gather.

Respond with a single JSON object:
{{
  "tier": "cosmetic" | "gap_fill" | "redirection",
  "added_references": true | false,
  "assessment": "1-3 sentences explaining the decision; name the annotation that drove it",
  "gather_topics": ["specific search topics to deepen the requested areas"],
  "focus_addition": "one line to steer the next search toward those areas, or empty",
  "new_topic": "redirection only: the reframed research question in one line, else empty",
  "new_focus": "redirection only: the new emphasis/scope in one line, else empty",
  "new_research_prompt": "redirection only: a self-contained 3-6 sentence research brief in the new direction, else empty"
}}
Independently of the tier, set added_references to true if the reviewer pasted
bibliographic references or new citations into the draft — inserted reference text, a
reference list, or a comment such as "add these citations" / "incorporate this work from
a related project". This routes an `ingest` step ahead of the chain to bring those
specific sources into the corpus (it is orthogonal to cosmetic/gap_fill/redirection).

gather_topics and focus_addition are needed for gap_fill or redirection; the new_* fields
are needed only for redirection; added_references may be set for any tier."""


def _chain_for(tier: str, plan: dict, needs_report: bool = False) -> list[str]:
    """Pipeline steps for this plan.

    When the reviewer pasted references, prepend an `ingest` step and guarantee a `collect`
    step precedes the re-draft (so the human can add any not-in-Zotero references first).

    When any comment asked for a NEW SECTION, the re-draft must be `report`, not `revise`:
    a redline edits the paragraph a comment anchors to and cannot add a section. `report`
    re-plans the review's sections from the corpus — which is exactly the work being asked
    for, and is orthogonal to the tier (a section may be wanted with or without new sources).
    """
    steps = list(_PIPELINE[tier])
    if needs_report and "revise" in steps:
        steps[steps.index("revise")] = "report"
    if plan.get("added_references"):
        steps = ["ingest"] + steps
        redraft = "report" if needs_report else "revise"
        if "collect" not in steps and redraft in steps:
            steps.insert(steps.index(redraft), "collect")
    return steps


def section_focus(comments: list[str], limit: int = 240) -> str:
    """Turn "add a section on X" comments into a focus line the section planner will read.

    `report` never reads the annotated docx — it re-plans from the corpus. The reviewer's
    intent therefore has to reach it through the project config, which is the one channel
    both steps share. Their own words, not a paraphrase: nothing here is an LLM call, so
    nothing here can drift from what they asked for.
    """
    asks = [" ".join(c.split())[:limit] for c in comments if c and c.strip()]
    if not asks:
        return ""
    return ("Develop a dedicated section addressing each of the following, or say in the "
            "narrative why the evidence does not support one: " + "; ".join(asks))


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
    plan.setdefault("new_topic", "")
    plan.setdefault("new_focus", "")
    plan.setdefault("new_research_prompt", "")
    plan.setdefault("added_references", False)
    return plan


# ── config steering for gap_fill ───────────────────────────────────────────────

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
    coordinator's reframe. The new file is a fresh iteration (litrev_<N+1>.yaml) that gather
    uses on the next run; inspect or edit it whenever. Project binding (name, trundlr id,
    models, source policy) is inherited from the previous config untouched.

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


# ── rendering ──────────────────────────────────────────────────────────────────

def _print_plan(cfg, docx: Path, plan: dict, steps: list[str]) -> None:
    print(f"rabbitHole parseNplan — {cfg.project_name}")
    print(f"  Annotated file: {docx.name}")
    print()
    print(f"  Tier: {plan['tier']}")
    if plan.get("assessment"):
        print(f"  Assessment: {plan['assessment']}")
    if plan["tier"] == "redirection" and plan.get("new_topic"):
        print(f"  Redirected topic: {plan['new_topic']}")
        if plan.get("new_research_prompt"):
            print(f"  New brief: {plan['new_research_prompt']}")
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
    folder before running, so a bare `rabbitHole <verb>` is sufficient.

    The chain's `revise` carries --no-queue: it re-drafts from the expanded corpus but
    must NOT re-plan and re-queue another chain (the comments it sees are the same ones
    that produced this chain, so it would loop). Queuing is a decision made once, here.

    `report` needs no such guard: it never reads annotations, so it cannot re-plan a chain
    from them. That is the same property that makes it the right re-draft step for a comment
    asking for a new section, and the reason the ask must reach it through the project focus."""
    verb = _STEP[step]["verb"]
    if not verb:
        return None
    if step == "revise":
        return "rabbitHole revise --no-queue"
    return f"rabbitHole {verb}"


def _next_cycle(titles: list[str]) -> int:
    """Next revision-cycle number for this project, shared by every step in the chain.

    All steps queued by one parseNplan run carry the SAME number — `lit review gather 2`,
    `collect 2`, `revise 2`, `comment 2` — so a cycle reads as one unit instead of each
    step keeping an independent counter. The number is one past the highest seen on any
    `lit review <step> <N>` title already in the project."""
    pat = re.compile(r"^lit review \w+ (\d+)$", re.I)
    nums = [int(m.group(1)) for t in titles for m in [pat.match(t.strip())] if m]
    return max(nums, default=0) + 1


# How many of the most-recent completed runs to average over. A window (rather
# than all history) keeps the estimate tracking the current regime: gather and
# revise grew several-fold heavier with recent upgrades, so an all-history median
# lags reality (e.g. gather: all-history 0.43h vs recent ~1.3h).
_ESTIMATE_WINDOW = 5


def _task_recency(t: dict):
    """Sort key putting the most-recently-finished task last (end_date, then id)."""
    end = t.get("end_date") or ""
    try:
        from datetime import datetime
        ts = datetime.fromisoformat(end.replace("Z", "+00:00")).timestamp() if end else 0.0
    except ValueError:
        ts = 0.0
    return (ts, t.get("id") or 0)


def _estimate_hours(tasks: list[dict], step: str) -> float:
    """Median actual duration (hours) of the most recent completed tasks of this
    step, pooled across all projects.

    trundlr records the realised hours in the `duration` field once a task is
    done, so the schedule estimate self-tunes from history. Only the last
    _ESTIMATE_WINDOW runs count, so the estimate tracks shifts in runtime rather
    than being anchored to old, lighter runs. Falls back to the static per-step
    default when there is no completed task to learn from."""
    pat = re.compile(rf"^lit review {re.escape(step)} (\d+)$", re.I)
    done = [t for t in tasks
            if t.get("status") == "done"
            and pat.match((t.get("title") or "").strip())
            and isinstance(t.get("duration"), (int, float)) and t["duration"] > 0]
    if not done:
        return _STEP[step]["hours"]
    recent = sorted(done, key=_task_recency)[-_ESTIMATE_WINDOW:]
    return round(statistics.median(float(t["duration"]) for t in recent), 3)


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

        # One shared cycle number for the whole chain (gather/collect/revise/comment
        # all carry it); duration estimates pool completed tasks across all projects.
        titles = [t.get("title", "") for t in tc.tasks_for_project(project_id)]
        history = tc.all_tasks()
        cycle = _next_cycle(titles)

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
            title = f"lit review {step} {cycle}"
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
    runlog.start()

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
    print(f"  {runlog.stamp()}Reading annotations and planning (coordinator)…", flush=True)
    brain = Brain(cfg.brain, gc, backend_override=brain_override)
    plan = _make_plan(brain, cfg, coverage, revision_context)
    tier = plan["tier"]
    steps = _chain_for(tier, plan)

    print()
    _print_plan(cfg, docx, plan, steps)

    if dry_run:
        print("\n  [dry-run] No config written and no tasks queued.")
        return 0

    # 5. gap_fill steers the next gather via a new numbered config; redirection
    #    rewrites the whole brief into a new iterated config for you to approve.
    if tier == "gap_fill":
        fp = _write_gap_config(directory, plan)
        print(f"\n  Wrote gather-steering config: {fp.name}")
    elif tier == "redirection":
        fp = _write_redirect_config(directory, plan)
        print(f"\n  Drafted redirected research brief: {fp.name} "
              f"(gather will use it; inspect or edit it whenever)")

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

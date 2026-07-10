"""The planner harness — parseNplan generalized to every human gate.

`haarpi next` runs as a commanded trundlr task at the end of every chain the
planner queues, firing when the human marks their gate task done. Flow:

  1. find the markup the human just finished (newest in-flight file whose
     chain ends in their initials), unless told --file/--stage;
  2. MECHANICAL gate check (haarpi.redline.gate_check — no LLM): clean markup
     -> mint a RELEASE, archive the spent chain, advance downstream stages;
  3. otherwise classify the unresolved asks into a tier (local brain), map the
     tier to a step chain, and queue it in trundlr — ending, as always, with
     the next `haarpi next`. The loop feeds itself.

Oversight without a terminal: the plan is emailed (haarpi.notify), the chain
sits in trundlr's UI during the resource wait (cancellation window), and the
ledger records every decision. One annotation set is never planned twice
(annotation-set hash loop guard).

The tier definitions and their prompts are per-stage judgment; the litreview
prompt inherits rabbitHole parseNplan's tuned rules (presence is not
sufficiency; never downgrade to cosmetic because the topic already has
sources). The paper/experiments prompts are initial and expected to earn
their tuning in use.
"""

from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass
from pathlib import Path

from . import config as hconfig
from . import naming, notify, project, redline, trundlr


# ── step registries ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Step:
    command: str | None      # None = human step (no command; waits in the queue)
    hours: float             # cold-start estimate; history overrides
    desc: str
    resource: str = "runner"  # "human" | "runner" | "gpu" | "cpu"

    @property
    def human(self) -> bool:
        return self.command is None


# Commands are queued in umbrella form (`haarpi <tool> <verb>`) — on the shared
# runner box the old standalone stack owns the bare names (oddjob coexistence).
STAGE_STEPS: dict[str, dict[str, Step]] = {
    "litreview": {
        "ingest":  Step("haarpi rabbithole ingest", 0.5,
                        "Pull reviewer-supplied references into the corpus."),
        "gather":  Step("haarpi rabbithole gather", 1.3,
                        "Discover & curate new sources into the Zotero collection."),
        "collect": Step(None, 0.25,
                        "Download the new PDFs and add them to the Zotero collection."),
        "revise":  Step("haarpi rabbithole revise --no-queue", 4.0,
                        "Re-draft the review from the expanded corpus + annotations."),
        "report":  Step("haarpi rabbithole report", 3.0,
                        "Re-plan the review's sections and re-synthesise from the corpus."),
        "comment": Step(None, 0.15, "Review the new draft and annotate it."),
    },
    "paper": {
        "revise":  Step("haarpi raconteur draft", 2.0,
                        "Answer each comment in place with tracked changes."),
        "outline": Step("haarpi raconteur outline", 1.0,
                        "Re-design the paper's structure from the approved one-pager."),
        "draft":   Step("haarpi raconteur draft", 3.0,
                        "Write the full paper from the outline and upstream releases."),
        "comment": Step(None, 0.25, "Review the new draft and annotate it."),
    },
    "experiments": {
        "process": Step("haarpi rayleigh process", 1.0,
                        "Re-reduce data to the preregistered outputs and write-up."),
        "comment": Step(None, 0.25, "Review the results write-up and annotate it."),
    },
}

# tier -> ordered step chain, per stage
STAGE_TIERS: dict[str, dict[str, list[str]]] = {
    "litreview": {
        "cosmetic":    ["revise", "comment"],
        "gap_fill":    ["gather", "collect", "revise", "comment"],
        "redirection": ["gather", "collect", "revise", "comment"],
    },
    "paper": {
        "cosmetic":   ["revise", "comment"],
        "structural": ["outline", "draft", "comment"],
    },
    "experiments": {
        "cosmetic": ["process", "comment"],
    },
}

_SYS = ("You are a research-pipeline planner. You read a reviewer's unresolved "
        "annotations on a draft and decide what work is needed next. Respond "
        "with a single JSON object and nothing else.")

# The litreview rules are rabbitHole parseNplan's, earned from real
# misclassifications. The others are initial.
STAGE_PROMPTS: dict[str, str] = {
    "litreview": """\
A reviewer left unresolved annotations on a literature-review draft:
{annotations}

Pick the ONE tier matching the MOST substantive work ANY annotation asks for — a single
request for more research outranks a pile of wording notes, because a lighter pipeline
cannot perform the heavier work.

- "cosmetic": EVERY annotation can be satisfied by editing what is already written —
  rewording, restructuring, clarifying, or cutting. No new sources needed.
- "gap_fill": at least one annotation asks for MORE substance on some topic ("more on X",
  "go deeper", "what about Y"). This holds EVEN IF the topic is already in the corpus —
  presence is not sufficiency. Do NOT downgrade to cosmetic because X already has sources.
- "redirection": the review is aimed wrong or needs a fundamentally different scope.
  Only for a genuine change of direction, not "add more".

Respond: {{"tier": "...", "assessment": "<one sentence>", "gather_topics": ["..."]}}
(gather_topics only for gap_fill/redirection: specific, searchable topics.)""",
    "paper": """\
A reviewer left unresolved annotations on a paper draft:
{annotations}

Pick the ONE tier matching the MOST substantive work ANY annotation asks for:

- "cosmetic": every annotation is satisfiable by rewriting the flagged passages in place —
  wording, clarity, tone, transitions, small factual fixes from existing material.
- "structural": at least one annotation demands reorganization — sections added, removed,
  merged, or reordered; the argument restructured. The outline must change.

Respond: {{"tier": "...", "assessment": "<one sentence>"}}""",
    "experiments": """\
A reviewer left unresolved annotations on an experiment results write-up:
{annotations}

- "cosmetic": every annotation is about presentation — figure choices, table layout,
  narration of the preregistered findings. The data already answers them.

Anything demanding new cells, new seeds, or a new experiment is beyond this planner's
current chains — classify it "cosmetic" ONLY if presentation alone satisfies it;
otherwise use tier "escalate" and say what is needed.

Respond: {{"tier": "...", "assessment": "<one sentence>"}}""",
}


# ── config + estimates ───────────────────────────────────────────────────────

def pipeline_config() -> dict:
    return hconfig.merged_config("haarpi", {})


def _resource_id(tr_cfg: dict, kind: str) -> int | None:
    key = {"human": "human_resource", "runner": "runner_resource",
           "gpu": "gpu_resource", "cpu": "cpu_resource"}[kind]
    v = int(tr_cfg.get(key) or 0)
    if kind == "runner" and not v:            # runner falls back to the gpu box
        v = int(tr_cfg.get("gpu_resource") or 0)
    return v or None


_ESTIMATE_WINDOW = 5


def estimate_hours(tasks: list[dict], stage: str, step: str, fallback: float) -> float:
    """Median realised duration of the last few completed `<stage> <step> N`
    tasks (pooled across projects) — the schedule self-tunes from history."""
    pat = re.compile(rf"^{re.escape(stage)} {re.escape(step)} (\d+)$", re.I)
    done = [t for t in tasks
            if t.get("status") == "done" and pat.match((t.get("title") or "").strip())
            and isinstance(t.get("duration"), (int, float)) and t["duration"] > 0]
    if not done:
        return fallback
    done.sort(key=lambda t: (t.get("end_date") or "", t.get("id") or 0))
    recent = done[-_ESTIMATE_WINDOW:]
    return round(statistics.median(float(t["duration"]) for t in recent), 3)


def next_cycle(titles: list[str], stage: str) -> int:
    """One shared number for every step a planning run queues, so a cycle reads
    as one unit; one past the highest `<stage> <step> N` already present."""
    pat = re.compile(rf"^{re.escape(stage)} \w+ (\d+)$", re.I)
    nums = [int(m.group(1)) for t in titles for m in [pat.match((t or "").strip())] if m]
    return max(nums, default=0) + 1


# ── queueing ─────────────────────────────────────────────────────────────────

def queue_chain(client: trundlr.TrundlrClient, project_id: int, stage: str,
                steps: list[str], tr_cfg: dict, description: str = "") -> dict:
    """Queue the steps as a dependency chain, always appending the next planner
    invocation as a runner task — the loop feeds itself."""
    registry = STAGE_STEPS[stage]
    history = client.all_tasks()
    titles = [t.get("title", "") for t in history
              if t.get("project_id") in (project_id, None)]
    cycle = next_cycle([t.get("title", "") for t in client.tasks_for_project(project_id)],
                       stage)

    plan_steps = [(name, registry[name]) for name in steps]
    plan_steps.append(("next", Step("haarpi next", 0.1,
                                    "Read the finished markup; mint a release or queue rework.")))
    prev_id = None
    queued = []
    for name, step in plan_steps:
        rid = _resource_id(tr_cfg, "human" if step.human else step.resource)
        task = client.create_task(
            f"{stage} {name} {cycle}", project_id,
            command=step.command,
            depends_on_id=prev_id,
            description=(description if name == plan_steps[0][0] else "") or step.desc,
            resource_id=rid,
            duration=estimate_hours(history, stage, name, step.hours),
        )
        prev_id = task["id"]
        queued.append({"title": f"{stage} {name} {cycle}", "id": task["id"],
                       "command": step.command})
    return {"cycle": cycle, "tasks": queued}


# ── classification ───────────────────────────────────────────────────────────

def _parse_json_obj(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise ValueError(f"planner reply had no JSON object: {raw[:200]}")
    return json.loads(m.group(0))


def classify(stage: str, check: dict, cfg: dict) -> dict:
    """Local-brain tier classification of the unresolved asks."""
    from .brain import Brain
    o = cfg.get("ollama", {})
    b = Brain(o.get("url", "http://localhost:11434"),
              o.get("coordinator", "qwen3.6:27b-16k"),
              o.get("worker", "llama3.1:8b"), tool="haarpi")
    lines = [f'- ({c["author"]}) {c["text"]}' for c in check["unresolved"]]
    if check["reviewer_changes"]:
        lines.append(f"- ({check['reviewer_changes']} direct tracked-change edits by the reviewer)")
    prompt = STAGE_PROMPTS[stage].format(annotations="\n".join(lines))
    plan = _parse_json_obj(b.coordinator(prompt, _SYS, think=False))
    tiers = STAGE_TIERS[stage]
    if plan.get("tier") not in tiers:
        plan["escalate"] = plan.get("tier")
        # unknown/escalate tier -> the heaviest chain this stage has
        plan["tier"] = list(tiers)[-1]
    return plan


# ── the verb ─────────────────────────────────────────────────────────────────

def find_finished_markup(root: Path, m: project.Manifest) -> tuple[str, Path] | None:
    """Newest in-flight file whose chain ends in the human's initials — the
    markup whose gate task was just marked done."""
    best: tuple[float, str, Path] | None = None
    for stage in m.stages:
        d = m.output_dir(root, stage)
        if not d.is_dir():
            continue
        for p in d.glob("*.docx"):
            parsed = naming.parse(p, m.short_title)
            if not parsed:
                continue
            _, chain, _ = parsed
            if chain and chain[-1].lower() == m.initials.lower():
                t = p.stat().st_mtime
                if best is None or t > best[0]:
                    best = (t, stage, p)
    return (best[1], best[2]) if best else None


def _archive_chain(root: Path, m: project.Manifest, stage: str, release: Path) -> int:
    """Move the spent chain files aside so output/ holds releases + live work only."""
    d = m.output_dir(root, stage)
    dest = m.stage_dir(root, stage) / "archive" / release.stem
    n = 0
    for p in list(d.glob("*.docx")) + list(d.glob("*.md")):
        parsed = naming.parse(p, m.short_title)
        if parsed and not naming.is_release(parsed[1]):
            dest.mkdir(parents=True, exist_ok=True)
            p.rename(dest / p.name)
            n += 1
    return n


def _advance(root: Path, m: project.Manifest, client, tr_cfg: dict) -> list[str]:
    """After a mint: open any downstream stage that just became unlocked."""
    opened = []
    done = {e.get("stage") for e in project.list_plans(root) if e.get("type") == "opened"}
    for stage, spec in m.stages.items():
        if stage in done or not spec.get("inputs"):
            continue
        if project.latest_release(root, m, stage) or project.in_flight(root, m, stage):
            continue
        if not project.unlocked(root, m, stage):
            continue
        tool = spec["tool"]
        if spec.get("attended"):
            verb = {"raster": "plan", "rayleigh": "init"}.get(tool, "init")
            client.create_task(
                f"{stage} design session", m.trundlr_project_id,
                description=f"Interactive design session — run: haarpi {tool} {verb}",
                resource_id=_resource_id(tr_cfg, "human"), duration=2.0)
        else:
            queue_chain(client, m.trundlr_project_id, stage,
                        ["draft", "comment"] if stage == "paper" else ["comment"],
                        tr_cfg, description="Stage opened: inputs released.")
        project.record_plan(root, {"type": "opened", "stage": stage})
        opened.append(stage)
    return opened


def run_next(root: Path, stage: str | None = None, file: Path | None = None,
             dry_run: bool = False) -> int:
    m = project.load_manifest(root)
    cfg = pipeline_config()
    tr_cfg = cfg.get("trundlr", {})

    if file is not None:
        found = (stage or "litreview", Path(file))
    else:
        found = find_finished_markup(root, m)
        if found is None:
            print("haarpi next: no finished markup found (no in-flight file ends "
                  f"in _{m.initials}). Nothing to do.")
            return 0
        if stage and found[0] != stage:
            print(f"haarpi next: newest finished markup is in '{found[0]}', not "
                  f"'{stage}' — pass --file to override.")
            return 2
    stage, markup = found

    check = redline.gate_check(markup)
    ahash = project.annotation_hash(check["unresolved"], check["reviewer_changes"])
    if project.already_planned(root, ahash):
        print(f"haarpi next: this annotation set was already planned (hash {ahash}) — "
              "loop guard, refusing to plan it twice.")
        return 0

    infix = m.stages[stage].get("infix") or ""
    if check["clean"]:
        rel_name = naming.release_name(m.short_title, "docx", infix=infix)
        dst = m.output_dir(root, stage) / rel_name
        if dry_run:
            print(f"[dry-run] clean markup -> would mint {dst.name}")
            return 0
        result = redline.mint_release(markup, dst)
        archived = _archive_chain(root, m, stage, dst)
        project.record_plan(root, {
            "type": "gate", "stage": stage, "annotation_hash": ahash,
            "markup": markup.name, "release": dst.name, "archived": archived})
        opened = []
        if m.trundlr_project_id:
            try:
                client = trundlr.TrundlrClient(tr_cfg.get("url", ""))
                opened = _advance(root, m, client, tr_cfg)
            except trundlr.TrundlrError as e:
                print(f"  [trundlr] advance skipped: {e}")
        msg = (f"{stage}: gate PASSED — released {dst.name}"
               + (f"; opened {', '.join(opened)}" if opened else ""))
        print(f"haarpi next: {msg}")
        _email(cfg, f"haarpi: {m.name} — {stage} gate passed", msg)
        return 0

    # unresolved asks -> classify + queue rework
    plan = classify(stage, check, cfg)
    tier = plan["tier"]
    steps = STAGE_TIERS[stage][tier]
    summary = [f"{stage}: {len(check['unresolved'])} unresolved ask(s) -> tier {tier}",
               f"  assessment: {plan.get('assessment', '')}",
               f"  chain: {' -> '.join(steps)} -> next"]
    if plan.get("escalate"):
        summary.append(f"  NOTE: classifier wanted '{plan['escalate']}' — beyond this "
                       "stage's chains; queued the heaviest available instead. Review!")
    if dry_run:
        print("\n".join(["[dry-run]"] + summary))
        return 0
    entry = {"type": "plan", "stage": stage, "annotation_hash": ahash,
             "markup": markup.name, "tier": tier, "steps": steps,
             "assessment": plan.get("assessment", "")}
    if not m.trundlr_project_id:
        project.record_plan(root, entry)
        print("\n".join(summary + ["  [trundlr] no project id — run the chain manually:"]
                        + [f"    {STAGE_STEPS[stage][s].command or '(you) ' + s}" for s in steps]))
        return 0
    try:
        client = trundlr.TrundlrClient(tr_cfg.get("url", ""))
        queued = queue_chain(client, m.trundlr_project_id, stage, steps, tr_cfg,
                             description=plan.get("assessment", ""))
        entry["cycle"] = queued["cycle"]
        entry["tasks"] = [t["title"] for t in queued["tasks"]]
        project.record_plan(root, entry)
        summary.append(f"  queued as cycle {queued['cycle']} "
                       f"({len(queued['tasks'])} tasks, ends in `haarpi next`)")
    except trundlr.TrundlrError as e:
        project.record_plan(root, entry)
        summary.append(f"  [trundlr] queueing failed ({e}) — run the chain manually:")
        summary += [f"    {STAGE_STEPS[stage][s].command or '(you) ' + s}" for s in steps]
    print("\n".join(summary))
    _email(cfg, f"haarpi: {m.name} — {stage} plan ({tier})", "\n".join(summary))
    return 0


def _email(cfg: dict, subject: str, body: str) -> None:
    nt = cfg.get("notify", {})
    to = nt.get("to") or cfg.get("contact_email", "")
    if to:
        notify.send_email(subject, body, to=to, mail_prog=nt.get("mail_prog", ""))


# ── init + status ────────────────────────────────────────────────────────────

def _ask(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    got = input(f"  {label}{suffix}: ").strip()
    return got or default


def run_init(root: Path, name: str | None = None, short_title: str | None = None,
             brief: str | None = None, initials: str | None = None,
             no_trundlr: bool = False) -> int:
    """One interview -> haarpi.yaml + the stage skeleton + the trundlr project +
    the lit-review opening chain. Identity is answered once, here; the stage
    tools' own inits go straight to their substance."""
    if (root / project.MANIFEST).exists():
        print(f"haarpi init: {project.MANIFEST} already exists here.")
        return 2
    default_name = re.sub(r"^\d{6}_", "", root.name)
    m = project.Manifest(
        name=name or _ask("project name", default_name),
        short_title=short_title or _ask("short title (filename stem)",
                                        (name or default_name).lower()),
        brief=brief if brief is not None else _ask("research brief (long-form)"),
        initials=initials or _ask("your initials (revision chain)", "DCR"),
    )
    cfg = pipeline_config()
    tr_cfg = cfg.get("trundlr", {})
    lines = []
    if not no_trundlr and tr_cfg.get("url"):
        try:
            client = trundlr.TrundlrClient(tr_cfg["url"])
            pid, created = trundlr.resolve_project_id(
                tr_cfg["url"], m.name, folder=str(root.resolve()),
                description="HAARPi research pipeline")
            m.trundlr_project_id = pid
            lines.append(f"trundlr project '{m.name}' (id {pid}"
                         + (", created)" if created else ")"))
            queued = queue_chain(client, pid, "litreview",
                                 ["gather", "collect", "report", "comment"], tr_cfg,
                                 description=m.brief[:300])
            lines.append(f"queued litreview cycle {queued['cycle']} "
                         f"({len(queued['tasks'])} tasks, ends in `haarpi next`)")
        except trundlr.TrundlrError as e:
            lines.append(f"[trundlr] skipped ({e}) — register + queue later with "
                         "`haarpi queue`")
    else:
        lines.append("[trundlr] not configured/disabled — queue later with `haarpi queue`")
    project.save_manifest(m, root)
    project.scaffold(root, m)
    project.record_plan(root, {"type": "opened", "stage": "litreview"})
    print(f"haarpi init: {m.name} ({m.short_title}) — stages: "
          + ", ".join(m.stages) + "\n  " + "\n  ".join(lines))
    print("  Your to-do list is the trundlr queue; after each markup, mark the "
          "task done — the pipeline plans itself from there.")
    return 0


def run_status(root: Path) -> int:
    m = project.load_manifest(root)
    opened = {e.get("stage") for e in project.list_plans(root) if e.get("type") == "opened"}
    print(f"{m.name} ({m.short_title}) — trundlr project {m.trundlr_project_id or '—'}")
    for stage, spec in m.stages.items():
        rel = project.latest_release(root, m, stage)
        flight = project.in_flight(root, m, stage)
        stale = project.stale_inputs(root, m, stage)
        if rel and not flight:
            state = f"released   {rel.name}"
        elif flight:
            turn = "your turn" if (naming.parse(flight, m.short_title) or ("", ["ra"], ""))[1][-1].lower() != "ra" else "tool's turn"
            state = f"in flight  {flight.name}  ({turn})"
            if rel:
                state += f"  [last release {rel.name}]"
        elif stage in opened:
            state = "open       (no documents yet)"
        elif project.unlocked(root, m, stage):
            state = "unlocked   (not opened)"
        else:
            missing = [s for s in spec.get("inputs", [])
                       if project.latest_release(root, m, s) is None]
            state = f"waiting    (needs release from: {', '.join(missing)})"
        line = f"  {stage:<12} {state}"
        if stale:
            line += f"  STALE inputs: {', '.join(stale)}"
        print(line)
    return 0

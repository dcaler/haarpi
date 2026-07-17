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
        "revise":   Step("haarpi raconteur draft", 2.0,
                         "Answer each comment in place with tracked changes."),
        "onepager": Step("haarpi raconteur onepager", 1.0,
                         "Answer the one-pager annotations with tracked changes."),
        "recut":    Step("haarpi raconteur onepager --resynth", 1.0,
                         "Re-cut the narrative from scratch; the annotations are the brief."),
        "venue":    Step("haarpi raconteur venue", 1.0,
                         "Analyse candidate venues from the narrative."),
        "outline":  Step("haarpi raconteur outline", 1.0,
                         "Re-design the paper's structure from the approved one-pager."),
        "draft":    Step("haarpi raconteur draft", 3.0,
                         "Write the full paper from the outline and upstream releases."),
        "comment":  Step(None, 0.25, "Review the new draft and annotate it."),
    },
    "experiments": {
        "process": Step("haarpi rayleigh process", 1.0,
                        "Re-reduce data to the preregistered outputs and write-up."),
        "comment": Step(None, 0.25, "Review the results write-up and annotate it."),
        "review_session": Step(None, 1.0,
                               "Deep review needed (new cells/seeds/experiments): run "
                               "`haarpi rayleigh review` — the attended session designs "
                               "and queues the follow-on chain itself."),
    },
}

# tier -> ordered step chain, per stage. A "stage:step" element queues into
# ANOTHER stage's registry — cross-stage escalation ("this claim needs
# literature support" on the paper queues a litreview chain; the paper's own
# refresh then arrives via staleness propagation when that gate passes).
STAGE_TIERS: dict[str, dict[str, list[str]]] = {
    "litreview": {
        "cosmetic":    ["revise", "comment"],
        "gap_fill":    ["gather", "collect", "revise", "comment"],
        "redirection": ["gather", "collect", "revise", "comment"],
    },
    "paper": {
        "cosmetic":   ["revise", "comment"],
        "structural": ["outline", "draft", "comment"],
        # The narrative is re-cut and handed straight back for approval: the
        # one-pager is a human gate, so the outline must not be rebuilt from a
        # through-line the author has not signed off on.
        "narrative":  ["recut", "comment"],
        "upstream_literature": ["litreview:gather", "litreview:collect",
                                "litreview:report", "litreview:comment"],
    },
    "experiments": {
        "cosmetic": ["process", "comment"],
        "extend":   ["review_session"],
    },
}

# what a stage re-runs when an INPUT releases anew while it sits idle with output
STAGE_REFRESH: dict[str, list[str]] = {
    "paper":       ["revise", "comment"],
    "experiments": ["process", "comment"],
}

# ── the paper stage's internal ladder ─────────────────────────────────────────
# The paper stage produces a succession of deliverables (onepager → venue →
# outline → draft), each human-gated. `haarpi next` tells them apart by the
# deliverable word in the markup's chain. A clean markup on one rung mints THAT
# deliverable's release and queues the next rung; only the bare manuscript (no
# deliverable word) mints the stage release and advances downstream stages.

_PAPER_DELIVERABLE_WORDS = ("onepager", "outline", "venue")

_DELIVERABLE_LABEL = {
    "":         "full manuscript draft",
    "onepager": "one-pager (the narrative through-line)",
    "venue":    "venue analysis",
    "outline":  "outline",
}

# The next rung after a deliverable's gate passes.
#
#   onepager → venue → outline → draft
#
# The venue analysis is the FORK. Before it there is one narrative and one of everything;
# after it there is an outline and a manuscript PER VENUE the author selected. The one-pager
# used to jump straight to the outline, which quietly assumed a paper is written for nobody
# in particular — but an outline has a length, a column count and an audience, and those
# come from somewhere.
PAPER_LADDER: dict[str, list[str]] = {
    "onepager": ["venue", "comment"],
    "venue":    ["outline", "comment"],     # queued once per SELECTED venue
    "outline":  ["draft", "comment"],
}

# tier -> chain, per deliverable; STAGE_TIERS["paper"] covers the manuscript.
# Cosmetic asks are answered in place with tracked changes; anything heavier on
# a one-pager IS a narrative complaint — a structure objection to a five-beat
# narrative means the through-line is wrong — so structural and narrative both
# re-cut it from scratch with the annotations as the brief.
PAPER_DELIVERABLE_TIERS: dict[str, dict[str, list[str]]] = {
    "onepager": {
        "cosmetic":   ["onepager", "comment"],
        "structural": ["recut", "comment"],
        "narrative":  ["recut", "comment"],
        "upstream_literature": ["litreview:gather", "litreview:collect",
                                "litreview:report", "litreview:comment"],
    },
    "outline": {
        "cosmetic":   ["outline", "comment"],
        "structural": ["outline", "comment"],
        "narrative":  ["recut", "comment"],
        "upstream_literature": ["litreview:gather", "litreview:collect",
                                "litreview:report", "litreview:comment"],
    },
}
PAPER_DELIVERABLE_TIERS["venue"] = PAPER_DELIVERABLE_TIERS["outline"]


def _selected_venues(root: Path) -> list[str]:
    """The venues the AUTHOR chose, read from raconteur's config.

    Selecting a venue is the author's act, made on the slate in the venue analysis — the
    tool proposes candidates and never promotes one. So the ladder does not fork until the
    author has said where this paper is going.
    """
    try:
        from raconteur.config import ProjectConfig
    except ImportError:                       # raconteur not installed in this stack
        return []
    if not ProjectConfig.exists(root):
        return []
    try:
        return ProjectConfig.load(root).selected_venues()
    except Exception as e:                    # noqa: BLE001 — a broken yaml must not wedge the gate
        print(f"  [note] could not read the venue slate ({e})")
        return []


def _selected_venue_configs(root: Path) -> dict:
    """The selected venues as raconteur VenueConfig records (slug -> record).

    Richer than _selected_venues (which returns bare slugs): the template task and
    its email brief need each venue's name, CFP url, detected template link, and
    double-blind flag. Empty when raconteur or its config is absent."""
    try:
        from raconteur.config import ProjectConfig
    except ImportError:                       # raconteur not installed in this stack
        return {}
    if not ProjectConfig.exists(root):
        return {}
    try:
        cfg = ProjectConfig.load(root)
    except Exception as e:                    # noqa: BLE001 — a broken yaml must not wedge the fork
        print(f"  [note] could not read the venue slate ({e})")
        return {}
    return {s: cfg.venues[s] for s in cfg.selected_venues() if s in cfg.venues}


def _queue_template_task(root: Path, m, client, tr_cfg: dict, slug: str, vcfg,
                         cycle: int) -> str:
    """Scaffold a drop-slot and queue the human task that fills it.

    Locating a venue's submission template is the one step the machine cannot do
    reliably (see raconteur.slate.template_brief), so it is a human task — but a
    well-scaffolded one: a labelled folder already waits, and the brief pre-fills
    everything the CFP yielded. Runs in PARALLEL with the outline/draft chain (off
    the critical path); the future packaging rung is what will depend on it."""
    from raconteur import slate
    tdir = m.stage_dir(root, "paper") / "templates" / slug
    tdir.mkdir(parents=True, exist_ok=True)
    target_rel = tdir.relative_to(root).as_posix()
    brief = slate.template_brief(vcfg, target_rel)
    readme = tdir / "README.md"
    if not readme.exists():                   # never clobber a human's notes
        readme.write_text(f"# Submission template — {vcfg.name or slug}\n\n{brief}\n",
                          encoding="utf-8")
    client.create_task(
        f"paper {slug} template {cycle}", m.trundlr_project_id, description=brief,
        resource_id=_resource_id(tr_cfg, "human"), duration=0.5)
    return brief


def _queue_next_rung(root: Path, m, client, tr_cfg: dict, deliverable: str,
                     venue: str, dst: Path) -> str:
    """Queue what comes after a deliverable's gate — once per venue where that applies.

    The venue analysis is the FORK in the ladder. Before it, there is one narrative and one
    of everything. After it, there is one outline and one manuscript PER SELECTED VENUE, and
    those chains are independent: they share the one-pager, not the paper.
    """
    steps = PAPER_LADDER[deliverable]
    if deliverable != "venue":
        queued = queue_chain(client, m.trundlr_project_id, "paper", steps, tr_cfg,
                             description=f"{deliverable} gate passed: {dst.name}.",
                             venue=venue)
        return (f"; queued cycle {queued['cycle']} "
                f"({' -> '.join(steps)} -> next)"
                + (f" for {venue}" if venue else ""))

    records = _selected_venue_configs(root)
    if not records:
        return ("; NO VENUE SELECTED — nothing queued. Set a venue's status to "
                "'selected' on the slate in the venue analysis, then re-run `haarpi next`. "
                "An outline is written FOR somewhere, and only you can say where.")
    notes, briefs = [], []
    for slug, vcfg in records.items():
        queued = queue_chain(client, m.trundlr_project_id, "paper", steps, tr_cfg,
                             description=f"venue gate passed ({dst.name}): "
                                         f"write the {slug} paper.",
                             venue=slug)
        notes.append(f"{slug} (cycle {queued['cycle']})")
        try:
            briefs.append(_queue_template_task(root, m, client, tr_cfg, slug, vcfg,
                                               queued["cycle"]))
        except Exception as e:                # noqa: BLE001 — a template task must not wedge the fork
            print(f"  [note] could not queue the {slug} template task ({e})")
    note = f"; queued an outline chain for each selected venue: {', '.join(notes)}"
    if briefs:
        note += "\n\n  ── Submission templates to fetch (queued as human tasks) ──\n" \
                + "\n\n".join(briefs)
    return note


def _template_task_id(client, m, venue: str) -> int | None:
    """The venue's template-fetch task, so packaging waits until the template is in the
    slot. None when there is none (the template was placed by hand, no task) — packaging
    then simply runs, and `raconteur package` degrades if the slot is still empty."""
    pat = re.compile(rf"^paper {re.escape(venue)} template \d+$", re.I)
    ids = [t.get("id") for t in client.tasks_for_project(m.trundlr_project_id)
           if pat.match((t.get("title") or "").strip())]
    return ids[-1] if ids else None


def _queue_packaging(root: Path, m, client, tr_cfg: dict, venue: str, release: Path) -> str:
    """After a venue's manuscript is approved, assemble + compile its submission and hand
    the author the PDF to finish.

    Terminal rung: the author edits the .tex and submits, so no planner call follows. The
    package RUNNER waits on the template task (the artefact must be in the slot); the human
    review that follows is where the author reads the PDF and fills the venue-specific
    blocks. `raconteur package` no-ops gracefully when a venue has no template."""
    titles = [t.get("title", "") for t in client.tasks_for_project(m.trundlr_project_id)]
    cycle = next_cycle(titles, "paper", venue)
    pkg = client.create_task(
        f"paper {venue} package {cycle}", m.trundlr_project_id,
        command=_venued("haarpi raconteur package", venue),
        description=f"Assemble + compile the {venue} submission from {release.name}.",
        resource_id=_resource_id(tr_cfg, "runner"),
        duration=estimate_hours(client.all_tasks(), "paper", "package", 0.3),
        depends_on_id=_template_task_id(client, m, venue))
    client.create_task(
        f"paper {venue} submission {cycle}", m.trundlr_project_id,
        description=(f"Read paper/submission/{venue}/submission.pdf, finish submission.tex "
                     "(author, affiliations, abstract, keywords), and submit."),
        resource_id=_resource_id(tr_cfg, "human"), duration=1.0,
        depends_on_id=pkg["id"])
    return f"; queued packaging for {venue} (cycle {cycle}: package -> submission)"


def _deliverable_of(markup: Path, short_title: str) -> str:
    """The paper-stage deliverable a markup belongs to; '' = the manuscript."""
    parsed = naming.parse(markup, short_title)
    if not parsed:
        return ""
    chain = [c.lower() for c in parsed[1]]
    for w in _PAPER_DELIVERABLE_WORDS:
        if w in chain:
            return w
    return ""

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
A reviewer left unresolved annotations on a paper-stage deliverable — specifically
the {deliverable}:
{annotations}

Pick the ONE tier matching the MOST substantive work ANY annotation asks for. The
tiers below run from lightest to heaviest; a single heavier request outranks a pile
of lighter ones, because a lighter pipeline cannot perform the heavier work.

- "cosmetic": every annotation is satisfiable by rewriting the flagged passages in place —
  wording, clarity, tone, transitions, small factual fixes from existing material.
- "structural": at least one annotation demands reorganization — sections added, removed,
  merged, or reordered; the argument restructured. The outline must change, but the story
  the paper tells is still the right one.
- "narrative": at least one annotation rejects the through-line itself — the motivation,
  the framing, what the paper claims to contribute, or which results carry it. Not "move
  this section" but "this is not the argument". The one-pager must be re-cut and re-approved
  before any outline or draft is rebuilt on top of it. Annotations ON a one-pager are
  usually this tier or cosmetic; they are never "structural".
- "upstream_literature": at least one annotation needs NEW SOURCES — a claim requiring
  citation support the corpus lacks, "cite more recent work on X", a missing related-work
  thread. The paper cannot satisfy it; the literature review must gather first.

Respond: {{"tier": "...", "assessment": "<one sentence>", "gather_topics": ["..."]}}
(gather_topics only for upstream_literature: specific, searchable topics.)""",
    "experiments": """\
A reviewer left unresolved annotations on an experiment results write-up:
{annotations}

- "cosmetic": every annotation is about presentation — figure choices, table layout,
  narration of the preregistered findings. The data already answers them.
- "extend": at least one annotation demands NEW DATA — more cells, more seeds, a wider
  sweep, or a new experiment. Presentation cannot satisfy it.

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


# The June->July title rename ("lit review write 4" -> "litreview report 1") orphaned
# every new-style step from its realised history: the estimator matched nothing, fell
# back to the cold-start constant, and budgeted a ~26-hour synthesis at 3 (task 591,
# 2026-07-16) — throwing every downstream start time in the queue.
_STEP_SYNONYMS: dict[tuple[str, str], tuple[str, str]] = {
    ("litreview", "write"): ("litreview", "report"),
}
_TITLE_RE = re.compile(r"^(.*?)\s+(\w+)\s+\d+$")


def _canonical(title: str) -> tuple[str, str] | None:
    """Reduce a task title to its (stage, step) identity across naming eras.

    Whitespace in the stage name collapses ("lit review" == "litreview"), a venue
    infix folds into its stage ("paper ismir outline 3" is paper/outline work), and
    renamed verbs map forward through _STEP_SYNONYMS. None for titles that don't
    look like `<stage> <step> <cycle>` at all."""
    m = _TITLE_RE.match((title or "").strip())
    if not m:
        return None
    stage = re.sub(r"\s+", "", m.group(1).lower())
    step = m.group(2).lower()
    stage = next((s for s in STAGE_STEPS if stage == s or stage.startswith(s)), stage)
    return _STEP_SYNONYMS.get((stage, step), (stage, step))


def estimate_hours(tasks: list[dict], stage: str, step: str, fallback: float) -> float:
    """Budget a step from the realised durations of its recent completed history,
    pooled across projects and across title eras (see _canonical).

    The number is a BUDGET, not a forecast. The loss is asymmetric: a task that
    finishes under budget releases its dependents immediately (deps fire on
    completion, not on schedule), but one that overruns drags every downstream
    start time with it. So take the high end of the recent window — the
    second-highest realised duration (~p80, immune to a single freak outlier) —
    rather than the median, which undershoots exactly when dispersion is worst."""
    want = (stage.lower(), step.lower())
    done = [t for t in tasks
            if t.get("status") == "done"
            and _canonical(t.get("title") or "") == want
            and isinstance(t.get("duration"), (int, float)) and t["duration"] > 0]
    if not done:
        return fallback
    done.sort(key=lambda t: (t.get("end_date") or "", t.get("id") or 0))
    recent = sorted(float(t["duration"]) for t in done[-_ESTIMATE_WINDOW:])
    budget = recent[-2] if len(recent) >= 3 else recent[-1]
    return round(budget, 3)


def next_cycle(titles: list[str], stage: str, venue: str = "") -> int:
    """One shared number for every step a planning run queues, so a cycle reads
    as one unit; one past the highest `<stage> [venue] <step> N` already present.

    Cycles count PER VENUE: the JASSS paper's first outline is its cycle 1, however many
    rounds the ISMIR paper has already been through."""
    mid = rf"{re.escape(venue)} \w+" if venue else r"\w+"
    pat = re.compile(rf"^{re.escape(stage)} {mid} (\d+)$", re.I)
    nums = [int(m.group(1)) for t in titles for m in [pat.match((t or "").strip())] if m]
    return max(nums, default=0) + 1


# ── queueing ─────────────────────────────────────────────────────────────────

_VENUE_AWARE = re.compile(r"raconteur (outline|draft|paper|package)\b")


def _venued(command: str | None, venue: str) -> str | None:
    """Give a venue-aware verb its venue, explicitly.

    A queued command that names the venue it is writing for is a provenance feature: read
    back off the trundlr board a month later, `haarpi raconteur draft --venue jasss` says
    which paper it wrote, and a bare `draft` would not.
    """
    if not command or not venue or not _VENUE_AWARE.search(command):
        return command
    return f"{command} --venue {venue}"


def queue_chain(client: trundlr.TrundlrClient, project_id: int, stage: str,
                steps: list[str], tr_cfg: dict, description: str = "",
                approval: bool = False, venue: str = "") -> dict:
    """Queue the steps as a dependency chain, always appending the next planner
    invocation as a runner task — the loop feeds itself.

    A step may be "otherstage:step" (cross-stage escalation; it queues into
    that stage's registry under that stage's title). approval=True prepends a
    command-less human task that gates the whole chain (confirm_tiers).

    ``venue`` scopes a paper-stage chain to one venue: it names the chain ("paper ismir
    outline 1"), and every venue-aware command in it carries `--venue`. Two venues' chains
    are independent and run in parallel — they share the narrative, not the paper."""
    history = client.all_tasks()
    cycle = next_cycle([t.get("title", "") for t in client.tasks_for_project(project_id)],
                       stage, venue)

    plan_steps: list[tuple[str, str, Step]] = []
    if approval:
        plan_steps.append((stage, "approve",
                           Step(None, 0.1, "Approve this plan — marking done releases "
                                           "the chain (confirm_tiers).")))
    for name in steps:
        st, _, sname = name.rpartition(":")
        st = st or stage
        plan_steps.append((st, sname, STAGE_STEPS[st][sname]))
    plan_steps.append((stage, "next",
                       Step("haarpi next", 0.1,
                            "Read the finished markup; mint a release or queue rework.")))
    prev_id = None
    queued = []
    first = True
    for st, name, step in plan_steps:
        rid = _resource_id(tr_cfg, "human" if step.human else step.resource)
        # the venue belongs to the paper stage; an escalation into litreview is shared work
        v = venue if (venue and st == stage) else ""
        title = f"{st} {v} {name} {cycle}" if v else f"{st} {name} {cycle}"
        command = _venued(step.command, v)
        desc = step.desc
        if first and description:
            desc = f"{description} — {step.desc}"    # the plan + the instructions
        task = client.create_task(
            title, project_id,
            command=command,
            depends_on_id=prev_id,
            description=desc,
            resource_id=rid,
            duration=estimate_hours(history, st, name, step.hours),
        )
        prev_id = task["id"]
        first = False
        queued.append({"title": title, "id": task["id"], "command": command})
    return {"cycle": cycle, "tasks": queued, "venue": venue}


# ── classification ───────────────────────────────────────────────────────────

def _step_of(stage: str, name: str) -> Step:
    """Resolve a chain element, honouring the 'otherstage:step' form."""
    st, _, sname = name.rpartition(":")
    return STAGE_STEPS[st or stage][sname]


def _parse_json_obj(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise ValueError(f"planner reply had no JSON object: {raw[:200]}")
    return json.loads(m.group(0))


def classify(stage: str, check: dict, cfg: dict,
             tiers: dict[str, list[str]] | None = None,
             deliverable: str = "") -> dict:
    """Local-brain tier classification of the unresolved asks.

    `tiers` overrides the stage's registry (a paper deliverable's own tier
    table); `deliverable` names what was annotated, for the prompt."""
    from .brain import Brain
    o = cfg.get("ollama", {})
    b = Brain(o.get("url", "http://localhost:11434"),
              o.get("coordinator", "qwen3.6:27b-16k"),
              o.get("worker", "llama3.1:8b"), tool="haarpi")
    lines = [f'- ({c["author"]}) {c["text"]}' for c in check["unresolved"]]
    if check["reviewer_changes"]:
        lines.append(f"- ({check['reviewer_changes']} direct tracked-change edits by the reviewer)")
    prompt = STAGE_PROMPTS[stage].format(
        annotations="\n".join(lines),
        deliverable=_DELIVERABLE_LABEL.get(deliverable, deliverable or "draft"),
    )
    plan = _parse_json_obj(b.coordinator(prompt, _SYS, think=False))
    tiers = tiers or STAGE_TIERS[stage]
    if plan.get("tier") not in tiers:
        plan["escalate"] = plan.get("tier")
        # unknown/escalate tier -> the heaviest chain this stage has
        plan["tier"] = list(tiers)[-1]
    return plan


# ── the verb ─────────────────────────────────────────────────────────────────

def find_finished_markup(root: Path, m: project.Manifest) -> tuple[str, Path] | None:
    """Newest in-flight file whose chain ends in the human's initials — the
    markup whose gate task was just marked done.

    Scans the stage root alongside output/: raconteur's working chain lives at
    paper/ root (same convention latest_release's root-scan tier serves)."""
    best: tuple[float, str, Path] | None = None
    for stage in m.stages:
        for d in {m.output_dir(root, stage), m.stage_dir(root, stage)}:
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


def _current_bindings(root: Path, m: project.Manifest, stage: str) -> dict:
    """Snapshot of the input releases a stage's queued work will bind — the
    provenance record staleness detection compares against."""
    out = {}
    for s in m.stages[stage].get("inputs", []):
        rel = project.latest_release(root, m, s)
        if rel is not None:
            out[s] = rel.name
    return out


def _refresh_stale(root: Path, m: project.Manifest, client, tr_cfg: dict,
                   minted_stage: str, release: Path) -> list[str]:
    """Staleness propagation: a fresh release re-fires idle downstream stages
    that already produced output bound to the older one. Mid-flight stages are
    left alone — their next cycle re-binds naturally."""
    refreshed = []
    for d, spec in m.stages.items():
        if minted_stage not in spec.get("inputs", []) or d not in STAGE_REFRESH:
            continue
        if project.in_flight(root, m, d) or not project.latest_release(root, m, d):
            continue
        already = any(e.get("type") == "refresh" and e.get("stage") == d
                      and e.get("source") == release.name
                      for e in project.list_plans(root))
        if already:
            continue
        queued = queue_chain(client, m.trundlr_project_id, d, STAGE_REFRESH[d],
                             tr_cfg, description=f"Refresh: new {minted_stage} "
                                                 f"release {release.name}.")
        project.record_plan(root, {"type": "refresh", "stage": d,
                                   "source": release.name,
                                   "bindings": _current_bindings(root, m, d),
                                   "cycle": queued["cycle"]})
        refreshed.append(d)
    return refreshed


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
            # The paper stage opens at the top of its ladder — narrative first,
            # then venue analysis, then the human gate; outline and draft are
            # queued by their own gates as each rung passes.
            queue_chain(client, m.trundlr_project_id, stage,
                        ["onepager", "venue", "comment"] if stage == "paper" else ["comment"],
                        tr_cfg, description="Stage opened: inputs released.")
        project.record_plan(root, {"type": "opened", "stage": stage,
                                   "bindings": _current_bindings(root, m, stage)})
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
    ahash = project.annotation_hash(check["unresolved"], check["reviewer_changes"],
                                    markup.name)
    if project.already_planned(root, ahash):
        print(f"haarpi next: this annotation set was already planned (hash {ahash}) — "
              "loop guard, refusing to plan it twice.")
        return 0

    deliverable = _deliverable_of(markup, m.short_title) if stage == "paper" else ""
    venue = naming.venue_of(markup, m.short_title) if stage == "paper" else ""
    infix = "_".join(p for p in (venue, deliverable or
                                 (m.stages[stage].get("infix") or "")) if p)
    if check["clean"]:
        rel_name = naming.release_name(m.short_title, "docx", infix=infix)
        dst = m.output_dir(root, stage) / rel_name
        if dry_run:
            rung = f" ({deliverable} rung)" if deliverable else ""
            for_v = f" for {venue}" if venue else ""
            print(f"[dry-run] clean markup{rung}{for_v} -> would mint {dst.name}")
            if deliverable == "venue":
                print(f"[dry-run] would queue an outline chain per selected venue: "
                      f"{', '.join(_selected_venues(root)) or '(none selected on the slate)'}")
            elif not deliverable and stage == "paper" and venue:
                print(f"[dry-run] would queue packaging for {venue} (package -> submission)")
            return 0
        result = redline.mint_release(markup, dst)

        if deliverable:
            # A ladder rung, not the stage: mint this deliverable's release and
            # queue the next rung. The working chain stays put (no archive) and
            # downstream STAGES neither advance nor refresh — only the bare
            # manuscript's gate speaks for the paper stage.
            project.record_plan(root, {
                "type": "gate", "stage": stage, "deliverable": deliverable,
                "venue": venue, "annotation_hash": ahash, "markup": markup.name,
                "release": dst.name})
            queued_note = ""
            if m.trundlr_project_id:
                try:
                    client = trundlr.TrundlrClient(tr_cfg.get("url", ""))
                    queued_note = _queue_next_rung(
                        root, m, client, tr_cfg, deliverable, venue, dst)
                except trundlr.TrundlrError as e:
                    queued_note = f"; [trundlr] queueing failed ({e}) — queue the next rung manually"
            for_v = f" ({venue})" if venue else ""
            msg = (f"{stage}: {deliverable}{for_v} gate PASSED — "
                   f"released {dst.name}{queued_note}")
            print(f"haarpi next: {msg}")
            _email(cfg, f"haarpi: {m.name} — {deliverable} gate passed", msg)
            return 0

        archived = _archive_chain(root, m, stage, dst)
        project.record_plan(root, {
            "type": "gate", "stage": stage, "annotation_hash": ahash,
            "markup": markup.name, "release": dst.name, "archived": archived})
        opened, refreshed, packaged = [], [], ""
        if m.trundlr_project_id:
            try:
                client = trundlr.TrundlrClient(tr_cfg.get("url", ""))
                opened = _advance(root, m, client, tr_cfg)
                refreshed = _refresh_stale(root, m, client, tr_cfg, stage, dst)
                if stage == "paper" and venue:      # the approved manuscript -> package it
                    packaged = _queue_packaging(root, m, client, tr_cfg, venue, dst)
            except trundlr.TrundlrError as e:
                print(f"  [trundlr] advance skipped: {e}")
        msg = (f"{stage}: gate PASSED — released {dst.name}"
               + (f"; opened {', '.join(opened)}" if opened else "")
               + (f"; refresh queued for {', '.join(refreshed)}" if refreshed else "")
               + packaged)
        print(f"haarpi next: {msg}")
        _email(cfg, f"haarpi: {m.name} — {stage} gate passed", msg)
        return 0

    # unresolved asks -> classify + queue rework
    dtiers = PAPER_DELIVERABLE_TIERS.get(deliverable) if deliverable else None
    plan = classify(stage, check, cfg, tiers=dtiers, deliverable=deliverable)
    tier = plan["tier"]
    steps = (dtiers or STAGE_TIERS[stage])[tier]
    what = f"{stage} [{deliverable}]" if deliverable else stage
    if venue:
        what += f" ({venue})"
    summary = [f"{what}: {len(check['unresolved'])} unresolved ask(s) -> tier {tier}",
               f"  assessment: {plan.get('assessment', '')}",
               f"  chain: {' -> '.join(steps)} -> next"]
    if plan.get("escalate"):
        summary.append(f"  NOTE: classifier wanted '{plan['escalate']}' — beyond this "
                       "stage's chains; queued the heaviest available instead. Review!")
    if dry_run:
        print("\n".join(["[dry-run]"] + summary))
        return 0
    confirm = tier in (cfg.get("planner", {}).get("confirm_tiers") or ["redirection"])
    if confirm:
        summary.append("  confirm_tiers: an 'approve plan' task gates this chain")
    if plan.get("gather_topics"):
        summary.append(f"  gather topics: {', '.join(plan['gather_topics'])}")
    entry = {"type": "plan", "stage": stage, "deliverable": deliverable, "venue": venue,
             "annotation_hash": ahash,
             "markup": markup.name, "tier": tier, "steps": steps,
             "assessment": plan.get("assessment", ""),
             "bindings": _current_bindings(root, m, stage)}
    if not m.trundlr_project_id:
        project.record_plan(root, entry)
        print("\n".join(summary + ["  [trundlr] no project id — run the chain manually:"]
                        + [f"    {_step_of(stage, s).command or '(you) ' + s}" for s in steps]))
        return 0
    try:
        client = trundlr.TrundlrClient(tr_cfg.get("url", ""))
        desc = plan.get("assessment", "")
        if plan.get("gather_topics"):
            desc += " | gather topics: " + ", ".join(plan["gather_topics"])
        queued = queue_chain(client, m.trundlr_project_id, stage, steps, tr_cfg,
                             description=desc, approval=confirm, venue=venue)
        entry["cycle"] = queued["cycle"]
        entry["tasks"] = [t["title"] for t in queued["tasks"]]
        project.record_plan(root, entry)
        summary.append(f"  queued as cycle {queued['cycle']} "
                       f"({len(queued['tasks'])} tasks, ends in `haarpi next`)")
    except trundlr.TrundlrError as e:
        project.record_plan(root, entry)
        summary.append(f"  [trundlr] queueing failed ({e}) — run the chain manually:")
        summary += [f"    {_step_of(stage, s).command or '(you) ' + s}" for s in steps]
    print("\n".join(summary))
    _email(cfg, f"haarpi: {m.name} — {stage} plan ({tier})", "\n".join(summary))
    return 0


def _email(cfg: dict, subject: str, body: str) -> None:
    nt = cfg.get("notify", {})
    to = nt.get("to") or cfg.get("contact_email", "")
    if to:
        notify.send_email(subject, body, to=to, mail_prog=nt.get("mail_prog", ""))


def run_queue(root: Path) -> int:
    """Register the trundlr project (if the manifest lacks an id) and queue the
    lit-review opening chain if the stage has no tasks yet — for projects
    initialised with --no-trundlr, or after standing trundlr up later."""
    m = project.load_manifest(root)
    cfg = pipeline_config()
    tr_cfg = cfg.get("trundlr", {})
    if not tr_cfg.get("url"):
        print("haarpi queue: no [trundlr] url configured.")
        return 2
    try:
        client = trundlr.TrundlrClient(tr_cfg["url"])
        if not m.trundlr_project_id:
            pid, created = trundlr.resolve_project_id(
                tr_cfg["url"], m.name, folder=str(root.resolve()),
                description="HAARPi research pipeline",
                priority=m.trundlr_priority)
            m.trundlr_project_id = pid
            project.save_manifest(m, root)
            print(f"  trundlr project '{m.name}' (id {pid}"
                  + (f", created at priority {m.trundlr_priority})" if created else ")"))
        titles = [t.get("title", "") for t in
                  client.tasks_for_project(m.trundlr_project_id)]
        if any(t.startswith("litreview ") for t in titles):
            print(f"haarpi queue: litreview already has tasks "
                  f"({len(titles)} total) — nothing to queue.")
            return 0
        queued = queue_chain(client, m.trundlr_project_id, "litreview",
                             ["gather", "collect", "report", "comment"], tr_cfg,
                             description=m.brief[:300])
        print(f"haarpi queue: litreview cycle {queued['cycle']} queued "
              f"({len(queued['tasks'])} tasks, ends in `haarpi next`).")
        return 0
    except trundlr.TrundlrError as e:
        print(f"haarpi queue: trundlr unreachable — {e}")
        return 1


# ── init + status ────────────────────────────────────────────────────────────

def _ask(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    got = input(f"  {label}{suffix}: ").strip()
    return got or default


def _ask_priority(default: int = trundlr.PRIORITY_DEFAULT) -> int:
    """The project's standing in the queue, asked once. A new project used to be born
    at priority 1 — top band, ahead of everything already running — which is a claim
    the tool has no business making on the user's behalf."""
    got = _ask(f"trundlr priority ({trundlr.PRIORITY_MIN} urgent .. "
               f"{trundlr.PRIORITY_MAX} background)", str(default))
    return trundlr.clamp_priority(got)


def run_init(root: Path, name: str | None = None, short_title: str | None = None,
             brief: str | None = None, initials: str | None = None,
             priority: int | None = None, no_trundlr: bool = False) -> int:
    """One interview -> haarpi.yaml + the stage skeleton + the trundlr project +
    the lit-review opening chain. Identity is answered once, here; the stage
    tools' own inits go straight to their substance."""
    if (root / project.MANIFEST).exists():
        # Repair mode: fill anything the original init (or an older haarpi)
        # didn't materialise, and touch nothing that exists.
        m = project.load_manifest(root)
        project.scaffold(root, m)
        seeded = project.seed_tool_configs(root, m)
        if seeded:
            print(f"haarpi init: {project.MANIFEST} already exists — seeded "
                  "missing stage config(s): " + ", ".join(seeded))
            return 0
        print(f"haarpi init: {project.MANIFEST} already exists here; "
              "nothing missing to seed.")
        return 2
    default_name = re.sub(r"^\d{6}_", "", root.name)
    cfg = pipeline_config()
    tr_cfg = cfg.get("trundlr", {})
    asks_trundlr = not no_trundlr and bool(tr_cfg.get("url"))
    m = project.Manifest(
        name=name or _ask("project name", default_name),
        short_title=short_title or _ask("short title (filename stem)",
                                        (name or default_name).lower()),
        brief=brief if brief is not None else _ask("research brief (long-form)"),
        initials=initials or _ask("your initials (revision chain)", "DCR"),
        trundlr_priority=(trundlr.clamp_priority(priority) if priority is not None
                          else _ask_priority() if asks_trundlr
                          else trundlr.PRIORITY_DEFAULT),
    )
    lines = []
    if asks_trundlr:
        try:
            client = trundlr.TrundlrClient(tr_cfg["url"])
            pid, created = trundlr.resolve_project_id(
                tr_cfg["url"], m.name, folder=str(root.resolve()),
                description="HAARPi research pipeline",
                priority=m.trundlr_priority)
            m.trundlr_project_id = pid
            lines.append(f"trundlr project '{m.name}' (id {pid}"
                         + (f", created at priority {m.trundlr_priority})" if created
                            else ")"))
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
    seeded = project.seed_tool_configs(root, m)
    if seeded:
        lines.append("seeded stage config(s): " + ", ".join(seeded))
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

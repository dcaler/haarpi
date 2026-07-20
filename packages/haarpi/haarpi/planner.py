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
import sys
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
        "skeleton": Step("haarpi raconteur skeleton", 0.6,
                         "Phase one: plan the sections and subsections, and the words "
                         "each can afford."),
        "outline":  Step("haarpi raconteur outline", 1.0,
                         "Phase two: add the content beats to the approved skeleton."),
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

_PAPER_DELIVERABLE_WORDS = ("onepager", "skeleton", "outline", "venue")

_DELIVERABLE_LABEL = {
    "":         "full manuscript draft",
    "onepager": "one-pager (the narrative through-line)",
    "venue":    "venue analysis",
    "skeleton": "section skeleton (phase one — headings only)",
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
    "venue":    ["skeleton", "comment"],    # queued once per SELECTED venue
    # The outline is written in two passes with a redline between them. Phase one is
    # headings only, which is enough to compute the whole word plan — each section's share,
    # and therefore how many paragraphs each subsection can afford. Approving THAT is cheap;
    # discovering it after a draft has been written from it costs 4.5 GPU-hours.
    "skeleton": ["outline", "comment"],
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


def _print_authors(m: project.Manifest) -> None:
    people = project.authors(m)
    if not people:
        print("  (none recorded)")
        return
    corr = project.corresponding_authors(m)
    for i, a in enumerate(people, 1):
        tags = []
        if a.get("initials"):
            tags.append(f"[{a['initials']}]")
        if a.get("corresponding"):
            tags.append("✉ co-corresponding" if len(corr) > 1 else "✉ corresponding")
        print(f"  {i}. {a['name']}" + (f"  {' '.join(tags)}" if tags else ""))
        for aff in a.get("affiliations", []):
            print(f"       affiliation: {aff}")
        if a.get("orcid"):
            print(f"       orcid: {a['orcid']}")
        # An email is printed only where it will be published — for a non-corresponding
        # author it is recorded but not rendered, and showing it here implies otherwise.
        if a.get("email"):
            shown = "published" if a.get("corresponding") else "not published"
            print(f"       email: {a['email']} ({shown})")


def _ask_affiliations(current: list[str] | None = None) -> list[str]:
    """Affiliations, one per prompt until a blank line.

    Asked as a list rather than a field because a joint appointment is ordinary and an
    author mid-move between institutions needs both. Offering only one, then asking the
    author to jam two into a string, records something no renderer can number.
    """
    out: list[str] = []
    for i, existing in enumerate(list(current or []) + [""], start=1):
        got = _ask(f"affiliation {i} (blank to finish)", existing)
        if not got:
            break
        out.append(got)
    while True:
        got = _ask(f"affiliation {len(out) + 1} (blank to finish)")
        if not got:
            return out
        out.append(got)


def _author_wizard(root: Path, m: project.Manifest) -> int:
    """Edit the author list by conversation rather than by flag.

    Authorship changes at moments that are ABOUT the change — a collaborator joins, an
    affiliation moves, correspondence passes to someone else. At that moment the person
    editing does not know this tool's flag names, and a half-remembered flag silently
    records a half-right author. The wizard shows the list, asks, and shows it again.
    """
    print(f"authors of {m.short_title or m.name} (in authorship order):")
    _print_authors(m)
    while True:
        print()
        choice = _ask("[a]dd, [e]dit, [r]emove, [m]ove, [d]one", "d").lower()[:1]
        if choice in ("d", "q", ""):
            return 0
        people = project.authors(m)
        if choice == "a":
            name = _ask("name")
            if not name:
                print("  no name — nothing added.")
                continue
            entry = {"name": name,
                     "initials": _ask("initials (their chain suffix, e.g. JR)"),
                     "affiliations": _ask_affiliations(),
                     "orcid": _ask("ORCID")}
            entry["email"] = _ask("email")
            entry["corresponding"] = _ask("corresponding author? [y/N]", "n")\
                .lower().startswith("y")
            if entry["corresponding"] and not entry["email"]:
                # The flag's whole effect is to publish the address. Recording one without
                # the other produces a corresponding author a reader cannot correspond with.
                print("  note: corresponding authors are published with an email, and this "
                      "one has none.")
            people.append(project.normalize_author(entry))
        elif choice in ("e", "r", "m"):
            if not people:
                print("  no authors yet.")
                continue
            who = _ask("which (number or name)")
            target = _match_author(people, who)
            if target is None:
                print(f"  no author matching '{who}'.")
                continue
            if choice == "r":
                people = [a for a in people if a is not target]
            elif choice == "m":
                pos = _ask(f"new position 1..{len(people)}")
                if not pos.isdigit():
                    print("  not a position — unchanged.")
                    continue
                people = [a for a in people if a is not target]
                people.insert(max(0, min(len(people), int(pos) - 1)), target)
            else:
                for key, label in (("name", "name"), ("initials", "initials")):
                    got = _ask(label, target.get(key, ""))
                    if got:
                        target[key] = got
                    else:
                        target.pop(key, None)
                target["affiliations"] = _ask_affiliations(target.get("affiliations"))
                for key, label in (("orcid", "ORCID"), ("email", "email")):
                    got = _ask(label, target.get(key, ""))
                    if got:
                        target[key] = got
                    else:
                        target.pop(key, None)
                was = "y" if target.get("corresponding") else "n"
                if _ask("corresponding author? [y/N]", was).lower().startswith("y"):
                    target["corresponding"] = True
                else:
                    target.pop("corresponding", None)
        else:
            print("  didn't catch that.")
            continue
        m.authors = [project.normalize_author(a) for a in people]
        project.save_manifest(m, root)
        print(f"\nauthors of {m.short_title or m.name} (in authorship order):")
        _print_authors(m)


def _match_author(people: list[dict], who: str) -> dict | None:
    """By 1-based position, initials, or name — whichever the human typed."""
    who = (who or "").strip()
    if not who:
        return None
    if who.isdigit() and 1 <= int(who) <= len(people):
        return people[int(who) - 1]
    for a in people:
        if a.get("initials", "").lower() == who.lower():
            return a
    for a in people:
        if a["name"].lower() == who.lower() or who.lower() in a["name"].lower():
            return a
    return None


def run_authors(root: Path, action: str = "", name: str = "", initials: str = "",
                affiliation: str | list[str] = "", email: str = "", orcid: str = "",
                position: int | None = None, corresponding: bool | None = None,
                interactive: bool | None = None) -> int:
    """Read and edit the project's author list.

    Authorship changes mid-project — a collaborator joins after the one-pager circulates —
    and when it does it must change in ONE place and be picked up by every document
    generated afterwards. Typing a name into a draft makes it prose, and prose is lost on
    the next major revision; this writes it to the manifest, above every stage.

    The tool records what it is told and nothing more. It does not infer an affiliation
    from a name, order the list, or assign CRediT roles — those are the author's calls.
    """
    m = project.load_manifest(root)
    current = project.authors(m)

    if not action:
        # Bare `haarpi authors` is the wizard — but only where someone is there to answer.
        # A queued task inheriting a non-tty must print and exit, never block the runner
        # on input() nobody will type.
        if interactive if interactive is not None else sys.stdin.isatty():
            return _author_wizard(root, m)
        action = "list"

    if action == "list":
        if not current:
            print("haarpi authors: none recorded — run `haarpi authors` to add them.")
            return 0
        print(f"authors of {m.short_title or m.name} (in authorship order):")
        _print_authors(m)
        if m.initials and m.initials not in [a.get("initials") for a in current]:
            print(f"\nnote: this project's chain suffix is _{m.initials}, which is not "
                  f"any listed author's initials.")
        return 0

    if action == "add":
        if not name.strip():
            print("haarpi authors add: --name is required.", file=sys.stderr)
            return 2
        if any(a["name"].lower() == name.strip().lower() for a in current):
            print(f"haarpi authors: '{name}' is already listed — use `set` to edit them.",
                  file=sys.stderr)
            return 2
        entry = project.normalize_author(
            {"name": name, "initials": initials, "affiliations": affiliation,
             "email": email, "orcid": orcid, "corresponding": bool(corresponding)})
        # position is 1-based authorship order; absent means append.
        if position is None or position > len(current):
            current.append(entry)
        else:
            current.insert(max(0, position - 1), entry)
        m.authors = current
        project.save_manifest(m, root)
        print(f"haarpi authors: added {entry['name']} "
              f"({len(current)} author(s) on {m.short_title or m.name})")
        return 0

    if action in ("set", "remove"):
        match = [a for a in current if a["name"].lower() == name.strip().lower()
                 or (initials and a.get("initials", "").lower() == initials.lower())]
        if not match:
            print(f"haarpi authors: no author matching '{name or initials}'.",
                  file=sys.stderr)
            return 2
        target = match[0]
        if action == "remove":
            current = [a for a in current if a is not target]
            m.authors = current
            project.save_manifest(m, root)
            print(f"haarpi authors: removed {target['name']}")
            return 0
        for k, v in (("name", name), ("initials", initials),
                     ("email", email), ("orcid", orcid)):
            if v:
                target[k] = v.strip()
        if affiliation:
            # Replaces the whole list: `set --affiliation A --affiliation B` states what
            # the affiliations ARE, so a correction cannot silently leave a stale one behind.
            target["affiliations"] = project.author_affiliations(
                {"affiliations": affiliation})
        if corresponding is not None:
            # Explicit False must be able to REMOVE the flag; `if corresponding:` would
            # make --no-corresponding silently do nothing.
            if corresponding:
                target["corresponding"] = True
            else:
                target.pop("corresponding", None)
        m.authors = current
        project.save_manifest(m, root)
        print(f"haarpi authors: updated {target['name']}")
        return 0

    print(f"haarpi authors: unknown action '{action}'.", file=sys.stderr)
    return 2


# ── the verb ─────────────────────────────────────────────────────────────────

# Directories that hold spent or reference copies rather than live work. `old/` is where a
# discard goes (moved, never deleted), and a file there must never read as this turn's markup.
_NOT_LIVE = {"old", "templates", "figures"}


def _markup_dirs(root: Path, m: project.Manifest, stage: str) -> list[Path]:
    """Every directory under a stage that may hold live markup.

    raconteur gives each deliverable its own folder — paper/onepager/,
    paper/css2026/outline/, paper/css2026/manuscript/ — so a scan of the stage root and its
    output/ no longer sees the work. Walks instead, skipping the archive: the alternative
    is a planner that reports "nothing to do" for a stage full of finished markup, which is
    the exact silent success this function was fixed for once already.
    """
    base = m.stage_dir(root, stage)
    return project.live_dirs(base) if base.is_dir() else []


def find_finished_markup(root: Path, m: project.Manifest) -> tuple[str, Path] | None:
    """Newest in-flight file a HUMAN touched last — the markup whose gate task was just
    marked done.

    "A human is done" is: the chain ends in a token that is not the tool's, and the file is
    not a release. It is deliberately NOT "the chain ends in `m.initials`". That test asked
    whether ONE named person went last, so a co-author with the final pass
    (`…_ra_DCR_JR.docx`) left a fully annotated document that this function could not see —
    `haarpi next` printed "nothing to do", exited 0, and the ladder stalled with the work
    sitting in the directory. ``naming.find_user_revision`` already defined it this way; the
    two definitions are now one.

    Scans the stage root alongside output/: raconteur's working chain lives at
    paper/ root (same convention latest_release's root-scan tier serves)."""
    best: tuple[float, str, Path] | None = None
    for stage in m.stages:
        for d in _markup_dirs(root, m, stage):
            for p in d.glob("*.docx"):
                parsed = naming.parse(p, m.short_title)
                if not parsed:
                    continue
                _, chain, _ = parsed
                # A release's last token is a deliverable word ("…_litreview.docx") — not
                # the tool's, and emphatically not a reviewer's; without this it reads as
                # markup on itself.
                if chain and chain[-1].lower() != "ra" and not naming.is_release(chain):
                    t = p.stat().st_mtime
                    if best is None or t > best[0]:
                        best = (t, stage, p)
    return (best[1], best[2]) if best else None


def _release_dir(root: Path, m: project.Manifest, stage: str, markup: Path) -> Path:
    """Where a release lands: the markup's own deliverable folder, under output/.

    Falls back to the stage's output/ when the markup sits at the stage root (every stage
    but paper, which is the only one with per-deliverable folders)."""
    base = m.stage_dir(root, stage)
    if markup.parent == base or base not in markup.parents:
        return m.output_dir(root, stage)
    home = markup.parent
    if home.name == "output":
        home = home.parent
    return home / "output"


def _archive_chain(root: Path, m: project.Manifest, stage: str, release: Path) -> int:
    """Move the spent chain files aside so output/ holds releases + live work only."""
    d = release.parent
    dest = d.parent / "archive" / release.stem
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
            print("haarpi next: no finished markup found (no in-flight file ends in a "
                  "reviewer's initials). Nothing to do.")
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
        # Beside the markup it was minted from: raconteur gives each deliverable its own
        # folder, so paper/css2026/outline/output/ — not one shared paper/output/.
        dst = _release_dir(root, m, stage, markup) / rel_name
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


def _paper_pick(root: Path, m: project.Manifest, includes: list[str],
                want_release: bool) -> Path | None:
    """Newest paper-stage doc matching a rung, across output/ and the stage root.

    `includes` are the chain tokens the rung must carry (a venue slug, a deliverable word,
    or both); any OTHER deliverable word disqualifies it — so the bare per-venue manuscript
    (chain == [venue]) is told apart from that venue's outline (chain == [venue, outline])."""
    includes = [i.lower() for i in includes]
    exclude = {w for w in _PAPER_DELIVERABLE_WORDS if w not in includes}
    best: Path | None = None
    for d in {m.output_dir(root, "paper"), m.stage_dir(root, "paper")}:
        if not d.is_dir():
            continue
        for p in d.glob("*.docx"):
            parsed = naming.parse(p, m.short_title)
            if not parsed:
                continue
            chain = [c.lower() for c in parsed[1]]
            if any(i not in chain for i in includes) or any(w in chain for w in exclude):
                continue
            if naming.is_release(parsed[1]) != want_release:
                continue
            if best is None or p.stat().st_mtime > best.stat().st_mtime:
                best = p
    return best


def _paper_rung_state(root: Path, m: project.Manifest, includes: list[str]) -> str:
    """A ladder rung's state: a release wins over a spent working markup, which wins
    over nothing (the deliverable chain stays on disk after its gate, so 'released'
    must not read as 'in flight' just because the markup is still there)."""
    rel = _paper_pick(root, m, includes, want_release=True)
    if rel:
        return f"released   {rel.name}"
    fl = _paper_pick(root, m, includes, want_release=False)
    if fl:
        chain = (naming.parse(fl, m.short_title) or ("", ["ra"], ""))[1]
        turn = "your turn" if chain[-1].lower() != "ra" else "tool's turn"
        return f"in flight  {fl.name}  ({turn})"
    return "pending"


def _submission_state(root: Path, m: project.Manifest, venue: str) -> str:
    """The packaging rung: a compiled PDF, an assembled-but-uncompiled project, or
    nothing yet — annotated with whether the venue's template is in its slot."""
    paper_root = m.stage_dir(root, "paper")
    subdir = paper_root / "submission" / venue
    tdir = paper_root / "templates" / venue
    has_template = tdir.is_dir() and any(
        p.is_file() and p.name.lower() != "readme.md" for p in tdir.rglob("*"))
    tnote = "template ready" if has_template else "no template"
    if subdir.is_dir():
        pdf = next(iter(sorted(subdir.glob("*.pdf"))), None)
        if pdf:
            return f"packaged   {pdf.name}"
        docx = next(iter(sorted(subdir.glob("*_submission.docx"))), None)
        if docx:
            return f"packaged   {docx.name}"
        if any(subdir.iterdir()):
            return f"assembled  (no PDF; {tnote})"
    return f"pending    ({tnote})"


def _print_paper_status(root: Path, m: project.Manifest) -> None:
    """The paper stage is a ladder, not one deliverable — expand it. Shared rungs
    (onepager, venue) sit under `paper`; the ladder forks per selected venue below
    (outline → draft → submission), which is where it multiplexes."""
    stale = project.stale_inputs(root, m, "paper")
    print("  paper" + (f"        STALE inputs: {', '.join(stale)}" if stale else ""))
    for deliv in ("onepager", "venue"):
        print(f"    {deliv:<12} {_paper_rung_state(root, m, [deliv])}")
    for v in _selected_venues(root):
        print(f"    {v}")
        print(f"      {'outline':<12} {_paper_rung_state(root, m, [v, 'outline'])}")
        print(f"      {'draft':<12} {_paper_rung_state(root, m, [v])}")
        print(f"      {'submission':<12} {_submission_state(root, m, v)}")


def run_status(root: Path) -> int:
    m = project.load_manifest(root)
    opened = {e.get("stage") for e in project.list_plans(root) if e.get("type") == "opened"}
    print(f"{m.name} ({m.short_title}) — trundlr project {m.trundlr_project_id or '—'}")
    for stage, spec in m.stages.items():
        if stage == "paper":
            _print_paper_status(root, m)
            continue
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

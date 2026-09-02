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
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import config as hconfig
from . import naming, project, redline, trundlr


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
                        "Search, rank and curate candidate sources into the collect-list. "
                        "The HUMAN adds them to Zotero at `collect`; gather puts nothing "
                        "there itself."),
        "collect": Step(None, 0.25,
                        "Download the new PDFs and add them to the Zotero collection."),
        "audit":   Step("haarpi rabbithole audit", 0.5,
                        "Word-sense filter: quarantine lexical false-friends (shared word, no "
                        "conceptual transfer) from the finalised corpus (reversible)."),
        "build":   Step("haarpi rabbithole build", 1.0,
                        "Embed the audited Zotero collection into the working corpus "
                        "(candidates, citekeys, ChromaDB index, per-paper notes). Needed "
                        "before a `revise` re-draft, which reads a cached corpus and never "
                        "embeds; `report` calls the same builder inline and needs no step."),
        # 4.0h of redline, plus ~3.5h PER SECTION the markup asks for: drafting and peer review
        # both run with the coordinator's chain-of-thought on (they are judgement work, unlike
        # lint and the redline itself), so a section costs three thinking calls. A `Step` carries
        # one scalar and the planner cannot know how many sections a markup asks for, so this
        # cold start assumes one and reads low for a multi-section pass until history fills in.
        "revise":  Step("haarpi rabbithole revise --no-queue", 7.5,
                        "Answer every comment in kind: a tracked rewrite for a prose comment, a "
                        "drafted section spliced in at the comment that asked for it, and the "
                        "cycle's term corrections applied across the document."),
        # VESTIGIAL: nothing calls this. `haarpi next` stopped selecting it when `revise`
        # absorbed section-grafting, and the only remaining caller of graft.run() is cli.py.
        # The live code is graft.draft_sections/choose_position, which revise imports. Kept as
        # an entry point rather than deleted, but do not describe it as a route anything takes.
        "graft":   Step("haarpi rabbithole graft", 3.5,
                        "Draft ONLY the requested section and splice it into the reviewer's "
                        "own .docx as a tracked insertion — existing paragraphs untouched, "
                        "comment threads intact."),
        "report":  Step("haarpi rabbithole report", 3.0,
                        "Re-plan the review's sections and re-synthesise from the corpus."),
        "mindmap": Step("haarpi rabbithole mindmap", 0.5,
                        "Regenerate the contribution map beside the new draft — a per-draft "
                        "diagnostic of the reference budget and which themes are peripheral.",
                        resource="gpu"),
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
    # The DESIGN (preregistration) stage. Its rework is always an attended re-run of the
    # design session — rayleigh re-authors experiments.yaml + the prereg docx addressing the
    # annotations (mirrors experiments' `review_session`). No `comment` step: re-running the
    # session ends by re-rendering the prereg for the author to annotate again.
    "design": {
        "design_session": Step(None, 1.0,
                               "Re-open the design session: run `haarpi rayleigh init` to "
                               "address the annotations and re-render the prereg docx. The "
                               "EXECUTABLE experiments.yaml is not written here — "
                               "`rayleigh plan` authors it in the experiments stage, against "
                               "the code raster built."),
    },
    # The BUILD stage. raster's `handoff` renders the methods digest to a docx the gate mints;
    # rework re-opens the attended build session (raster re-plans/re-builds and re-emits the
    # digest). Same shape as design — one tier, an attended re-run.
    "build": {
        "build_session": Step(None, 1.0,
                              "Re-open the build: run `haarpi raster plan` / `raster build` to "
                              "address the annotations, then `raster handoff` to re-emit the "
                              "methods digest docx."),
    },
    # The DECK stage. razzle drafts a venue-specific .pptx the author reviews IN PLACE with
    # PowerPoint comments (no rename to initials — the .pptx is its own markup). Rework re-opens
    # the attended deck session (razzle re-authors the spec addressing the comments, re-renders).
    # Same one-tier shape as design/build.
    "deck": {
        # Unattended: the interview settled every fact a tool must not invent, and the re-rendered
        # .pptx meets the human again at the redline gate — so there is no decision inside the
        # authoring pass for anyone to sit through. It runs on the CPU runner rather than the
        # `claude` resource because that resource has no runner polling it; nothing would execute.
        "deck_session": Step("haarpi razzle deck --headless", 1.0,
                             "Re-author the deck spec to address the PowerPoint comments and "
                             "re-render the .pptx.", resource="cpu"),
    },
}

# tier -> ordered step chain, per stage. A "stage:step" element queues into
# ANOTHER stage's registry — cross-stage escalation ("this claim needs
# literature support" on the paper queues a litreview chain; the paper's own
# refresh then arrives via staleness propagation when that gate passes).
STAGE_TIERS: dict[str, dict[str, list[str]]] = {
    # Litreview always plans through `decompose`/`chain_from_tasks` (the per-comment path);
    # this table is the tier-summary shape, kept in step with the chain the decomposition
    # builds — new sources are audited then embedded (`build`) before a `revise` re-draft.
    "litreview": {
        "cosmetic":    ["revise", "comment"],
        "gap_fill":    ["gather", "collect", "audit", "build", "revise", "comment"],
        "redirection": ["gather", "collect", "audit", "build", "report", "comment"],
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
    # Any prereg annotation re-opens the attended design session (one tier — the design is
    # authored interactively, so every rework is "re-run the session").
    "design": {
        "revise": ["design_session"],
    },
    # Any methods annotation re-opens the attended build session (one tier — raster's build is
    # an attended process; every rework is "re-run it and re-emit the digest").
    "build": {
        "revise": ["build_session"],
    },
    # Any unresolved deck comment re-opens the attended deck session (one tier — the deck is
    # authored interactively; every rework is "re-run it and re-render").
    "deck": {
        "revise": ["deck_session"],
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

# The ORDER the rungs are climbed, which the set above does not carry — "venue" sits after
# "outline" there and before it here. "" is the manuscript itself: the rung with no
# deliverable word, which is why it cannot simply be named.
_PAPER_LADDER = ("onepager", "venue", "skeleton", "outline", "", "package")
_LADDER_NAME = {"": "manuscript"}

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

# The skeleton is headings only, so every structural ask is answered by re-running phase
# one against the annotations — cheap, and the whole reason the rung exists. Without an
# entry here it fell through to STAGE_TIERS["paper"], whose "cosmetic" tier is `revise`
# (the manuscript drafter): a comment on a heading would have queued a full draft against
# a structure the author had just objected to.
PAPER_DELIVERABLE_TIERS["skeleton"] = {
    "cosmetic":   ["skeleton", "comment"],
    "structural": ["skeleton", "comment"],
    # A narrative complaint about the structure is a complaint about the through-line, and
    # the through-line is the one-pager's.
    "narrative":  ["recut", "comment"],
    "upstream_literature": ["litreview:gather", "litreview:collect",
                            "litreview:report", "litreview:comment"],
}


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
        _title("paper", "template", slug, cycle), m.trundlr_project_id, description=brief,
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
    ids = [t.get("id") for t in client.tasks_for_project(m.trundlr_project_id)
           if (p := _parse_title(t.get("title") or "")) and p[3] is not None
           and p[0] == "paper" and p[1] == "template" and (p[2] or "") == venue]
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
        _title("paper", "package", venue, cycle), m.trundlr_project_id,
        command=_venued("haarpi raconteur package", venue),
        description=f"Assemble + compile the {venue} submission from {release.name}.",
        resource_id=_resource_id(tr_cfg, "runner"),
        duration=estimate_hours(client.all_tasks(), "paper", "package", 0.3),
        depends_on_id=_template_task_id(client, m, venue))
    client.create_task(
        _title("paper", "submission", venue, cycle), m.trundlr_project_id,
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
    "design": """\
A reviewer left unresolved annotations on an experiment PREREGISTRATION (the design of the
experiments, before any data exists):
{annotations}

Any unresolved annotation re-opens the attended design session — rayleigh re-authors the
preregistration (experiments.yaml + the prereg docx) to address them. There is one tier.

- "revise": address the annotations by revising the preregistered design.

Respond: {{"tier": "revise", "assessment": "<one sentence>"}}""",
    "build": """\
A reviewer left unresolved annotations on the METHODS DIGEST (raster's account of what the
build did — the model, its contracts, the frozen test suite):
{annotations}

Any unresolved annotation re-opens the attended build session — raster re-plans/re-builds to
address them and re-emits the digest. There is one tier.

- "revise": address the annotations by revising the build and re-emitting the methods digest.

Respond: {{"tier": "revise", "assessment": "<one sentence>"}}""",
    "deck": """\
A reviewer left unresolved comments on a presentation DECK (a venue-specific .pptx built from the
paper — slides, figures, speaker notes):
{annotations}

Any unresolved comment re-opens the attended deck session — razzle re-authors the deck spec to
address them and re-renders the .pptx. There is one tier.

- "revise": address the comments by revising the deck spec and re-rendering.

Respond: {{"tier": "revise", "assessment": "<one sentence>"}}""",
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

# A task title NAMES ITS TOOL, not its stage: "raconteur outline css2026 9", not "paper
# css2026 outline 9". The stage is an internal word ("paper") and the tool is the one the
# author actually runs ("raconteur"), so the board reads as the work does. The venue sits
# AFTER the step — "<tool> <step> <venue?> <cycle>" — so a glance down the column lines the
# steps up regardless of which venue each belongs to.
_STAGE_TOOL = {s: spec["tool"] for s, spec in project.DEFAULT_STAGES.items()}
_TOOL_STAGE = {t: s for s, t in _STAGE_TOOL.items()}
# A tool that owns more than one stage (rayleigh: design + experiments) cannot be mapped
# to a single stage by name — the STEP disambiguates it (a `design_session` title is design
# work, a `process`/`review_session` title is experiments). Keyed tool -> its stages in order.
_MULTISTAGE_TOOLS: dict[str, list[str]] = {}
for _s, _t in _STAGE_TOOL.items():
    _MULTISTAGE_TOOLS.setdefault(_t, []).append(_s)
_MULTISTAGE_TOOLS = {t: ss for t, ss in _MULTISTAGE_TOOLS.items() if len(ss) > 1}

# Every word that can stand where the step stands — the chain steps plus the verbs the
# one-off tasks use. Parsing is vocabulary-driven, not positional, because the venue now
# sits between the step and the cycle: the step is the token we RECOGNISE, and the venue is
# whatever is left over. That one rule reads both the new order and the old
# "<stage> <venue> <step> <cycle>", so realised-duration history survives the rename.
_STEP_VOCAB = ({step for d in STAGE_STEPS.values() for step in d}
               | {"template", "package", "submission", "approve", "next"})


def _title(stage: str, step: str, venue: str, cycle) -> str:
    """A task title in the one form this module writes: <tool> <step> <venue?> <cycle>."""
    tool = _STAGE_TOOL.get(stage, stage)
    parts = [tool, step] + ([venue] if venue else []) + [str(cycle)]
    return " ".join(parts)


def _parse_title(title: str) -> tuple[str, str, str, int | None] | None:
    """(stage, step, venue, cycle) from a title of any era, or None if it is not one.

    New order is "<tool> <step> <venue?> <cycle>"; the pre-rename order was
    "<stage> <venue?> <step> <cycle>". Both are read by the same rule: the first token
    names the tool (or, on an old title, the stage); the step is whichever remaining token
    is a known step; the venue is the leftover. cycle is None for an unnumbered title (a
    design session), which the callers that need a number then skip."""
    toks = (title or "").strip().split()
    if not toks:
        return None
    cycle = None
    if toks[-1].isdigit():
        cycle, toks = int(toks[-1]), toks[:-1]
    if not toks:
        return None
    head = re.sub(r"\s+", "", toks[0].lower())
    stage = _TOOL_STAGE.get(head, head)                       # tool -> stage, else as-is
    stage = next((s for s in project.DEFAULT_STAGES
                  if stage == s or stage.startswith(s)), stage)
    rest = [t.lower() for t in toks[1:]]
    step = next((t for t in rest if t in _STEP_VOCAB), rest[-1] if rest else "")
    # A tool with >1 stage: pick the stage whose registry owns this step (else leave the
    # by-name default — an uncycled opening title like "rayleigh design session" has no
    # step to resolve, and _canonical/next_cycle ignore it anyway).
    for _s in _MULTISTAGE_TOOLS.get(head, ()):
        if step in STAGE_STEPS.get(_s, {}):
            stage = _s
            break
    venue = " ".join(t for t in rest if t != step)
    stage, step = _STEP_SYNONYMS.get((stage, step), (stage, step))
    return stage, step, venue, cycle


def _canonical(title: str) -> tuple[str, str] | None:
    """Reduce a task title to its (stage, step) identity across naming eras.

    The tool name folds to its stage ("raconteur" is paper work), the venue folds away
    ("raconteur outline ismir 3" is paper/outline work), and renamed verbs map forward
    through _STEP_SYNONYMS. None for a title that carries no cycle — a design session is
    not a step with a history to pool."""
    p = _parse_title(title)
    if p is None or p[3] is None:
        return None
    stage, step, _venue, _cycle = p
    return stage, step


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
    nums = []
    for t in titles:
        p = _parse_title(t)
        if p and p[3] is not None and p[0] == stage and (p[2] or "") == (venue or ""):
            nums.append(p[3])
    return max(nums, default=0) + 1


# ── queueing ─────────────────────────────────────────────────────────────────

# A verb that writes for one venue gets told which. razzle's venue-analogue is the presentation
# FORMAT (see razzle.formats), and it names the flag differently — so the flag travels with the
# pattern rather than being assumed to be `--venue`.
_VENUE_FLAGS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"raconteur (outline|draft|paper|package)\b"), "--venue"),
    (re.compile(r"razzle deck\b"), "--format"),
)


def _venued(command: str | None, venue: str) -> str | None:
    """Give a venue-aware verb its venue, explicitly.

    A queued command that names the venue it is writing for is a provenance feature: read
    back off the trundlr board a month later, `haarpi raconteur draft --venue jasss` says
    which paper it wrote, and a bare `draft` would not.
    """
    if not command or not venue:
        return command
    for pattern, flag in _VENUE_FLAGS:
        if pattern.search(command):
            return f"{command} {flag} {venue}"
    return command


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
        title = _title(st, name, v, cycle)
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


# ── the general solution: decompose → sequence → instruct (litreview) ─────────
# Instead of collapsing the whole annotation set into ONE tier and running that tier's
# fixed template chain, `haarpi next` (for litreview) enumerates a per-comment TASK LIST,
# derives the chain the tasks require, and steers each verb with what its tasks actually
# need. Open-loop by design: the human is the verification loop — each cycle is a fresh
# decomposition of the current annotations. See DESIGN_next_orchestration.md.

# The verb-need vocabulary. Every unresolved comment maps to exactly one.
_LITREVIEW_NEEDS = ("edit", "sources", "section", "ingest", "cite", "redirect", "correct")

_DECOMPOSE_PROMPT = """\
A reviewer left these unresolved annotations on a literature-review draft, numbered:
{annotations}

For EACH annotation decide what WORK it needs, then group annotations that need the SAME
work into one task. Every annotation number must appear in exactly one task.

Decide in THIS ORDER and stop at the first that fits — a later type never overrides an
earlier one:

1. "correct": the reviewer states that a NAME OR TERM the review uses is factually wrong, and
   says (or clearly implies) the right one — "you mean X, not Y", "that's called X", "get the
   name right, it's X". The work is a substitution, not a rewrite. Give "wrong" (the term as
   the review has it) and "right" (the term it should be). Choose this ONLY for a term the
   review actually uses; a request to discuss a new topic is "section" or "sources".
2. "redirect": the review is aimed wrong or needs a fundamentally different scope — a
   genuine change of direction, not "add more".
3. "section": asks for a new section, theme, strand, or sub-topic to be DEVELOPED as its
   own treatment — the review's STRUCTURE must change, not just its wording. Triggers:
   "a section on X", "identify/enumerate/lay out the Xs", "cover the specific techniques
   and how to use them", "this is too high level — I want the concrete X". A demand to
   build out a topic into its own strand is section, NOT sources, even when that topic is
   already mentioned in passing. When in doubt between section and sources, choose section:
   a section is drafted and spliced in without touching the rest of the review, so guessing
   this one wrong costs a strand the reviewer can reject, never a re-read of the document.
4. "cite": the reviewer says the papers are ALREADY in the Zotero library/collection and
   asks to cite them — "I've added Doblinger 2019 and Howell 2017 to Zotero, cite them",
   "these are in the collection now, use them". Nothing is fetched; the papers only need
   embedding so the reviser can cite them. Choose this, NOT ingest, whenever the reviewer
   states the works are already in the library/collection.
5. "ingest": names specific papers, DOIs, authors, or citations the reviewer wants pulled
   in that are NOT yet in the library — pasted reference text, "add @key", "cite Smith 2020",
   "these references:". The works must be fetched before they can be cited.
6. "sources": wants more evidence UNDER the structure that already exists — thicker support
   for a point the review already makes ("more on X", "go deeper", "what about Y"). Use this
   only when no new section is being asked for; it gathers literature, it does not re-plan.
7. "edit": satisfiable by rewriting text that is already there — reword, restructure,
   clarify, cut. No new sources, no new structure.

For "redirect", "section", and "sources" give a specific, searchable "query" (the topic to
gather or the section to plan). For "edit", "ingest", and "cite" set "query" to "".
For "correct" set "query" to "" and give "wrong" and "right" instead.

Respond ONLY with JSON:
{{"tasks": [{{"comments": [1, 2], "need": "sources", "query": "..."}},
            {{"comments": [3], "need": "correct", "wrong": "...", "right": "..."}}]}}"""


def decompose(comments: list[dict], cfg: dict) -> list[dict]:
    """Per-comment task list for a litreview annotation set (the 'list all the tasks' step).

    Returns ``[{comments: [texts], need: <one of _LITREVIEW_NEEDS>, query: str}]``. Falls back
    to a single ``edit`` task over everything if the brain returns nothing usable — a bad parse
    must degrade to the safe, in-place chain, never crash the gate.
    """
    from .brain import Brain
    texts = [c["text"] for c in comments]
    if not texts:
        return []
    o = cfg.get("ollama", {})
    b = Brain(o.get("url", "http://localhost:11434"),
              o.get("coordinator", "qwen3.6:27b-16k"),
              o.get("worker", "llama3.1:8b"), tool="haarpi")
    numbered = "\n".join(f"{i}. {t}" for i, t in enumerate(texts, 1))
    try:
        raw = _parse_json_obj(b.coordinator(
            _DECOMPOSE_PROMPT.format(annotations=numbered), _SYS, think=False))
        parsed = raw.get("tasks") or []
    except (ValueError, json.JSONDecodeError):
        parsed = []
    return _normalise_tasks(parsed, texts)


def _normalise_tasks(parsed: list[dict], texts: list[str]) -> list[dict]:
    """Coerce the model's task list into the contract, and guarantee total coverage.

    Every comment lands in exactly one task: unknown needs become ``edit``, a comment named
    twice keeps its first task, and any comment the model forgot is swept into a trailing
    ``edit`` task. The result is never empty when there are comments — the chain always has
    something to do, and it is always the safe thing when the model is unsure.
    """
    n = len(texts)
    claimed: set[int] = set()
    tasks: list[dict] = []
    for t in parsed:
        need = t.get("need") if isinstance(t, dict) else None
        if need not in _LITREVIEW_NEEDS:
            need = "edit"
        idxs = []
        for c in (t.get("comments") or []):
            try:
                i = int(c)
            except (TypeError, ValueError):
                continue
            if 1 <= i <= n and i not in claimed:
                claimed.add(i)
                idxs.append(i)
        if not idxs:
            continue
        query = (t.get("query") or "").strip() if isinstance(t, dict) else ""
        task = {"comments": [texts[i - 1] for i in idxs], "need": need, "query": query}
        if need == "correct":
            task["wrong"] = (t.get("wrong") or "").strip()
            task["right"] = (t.get("right") or "").strip()
            # A correction the model could not pin down to a term pair is not actionable as a
            # substitution. Degrade to `edit` rather than queueing a no-op step.
            if not (task["wrong"] and task["right"]):
                task["need"] = "edit"
        tasks.append(task)
    missed = [i for i in range(1, n + 1) if i not in claimed]
    if missed:
        tasks.append({"comments": [texts[i - 1] for i in missed],
                      "need": "edit", "query": ""})
    return _promote_explicit_cites(_promote_explicit_sections(tasks))


# A reviewer who writes "a section on X" is asking for STRUCTURE, and an in-place reviser
# structurally cannot add one — it only rewrites the paragraph a comment sits on. The 8B
# coordinator has been seen to file such asks under "sources" (gather more literature), which
# routes to `revise`, which then declines. This is the deterministic floor under the prompt:
# an unambiguous new-section request is a section task no matter what the model called it, so
# the chain re-plans (`report`) instead of sending a reviser at a job it cannot do. Kept tight
# on purpose — an indefinite/new determiner before "section", so "this section is unclear"
# (an edit of existing structure) never trips it.
_SECTION_UNIT = r"(?:section|theme|strand|sub-?topic)"
_SECTION_ASK = re.compile(
    # A determiner, then up to two words of adjective, then the unit. Requiring the determiner
    # IMMEDIATELY before it missed "add an entire section on X" and "another important section"
    # -- ordinary phrasings a reviewer has no reason to avoid. The unit list matches the words
    # _DECOMPOSE_PROMPT already invites ("a new section, theme, strand, or sub-topic"): the
    # deterministic floor was narrower than the vocabulary the prompt asks for, so a reviewer
    # writing "an entire theme" fell through to the 8B model, which flattened it to an edit.
    rf"\b(?:a|an|another|new|separate|dedicated|standalone|whole|entire)\s+"
    rf"(?:\w+\s+){{0,2}}{_SECTION_UNIT}\b"
    rf"|\bits own\s+(?:\w+\s+){{0,1}}{_SECTION_UNIT}\b"
    rf"|\badd a\s+(?:\w+\s+){{0,2}}{_SECTION_UNIT}\b",
    re.IGNORECASE)


def _promote_explicit_sections(tasks: list[dict]) -> list[dict]:
    """Move any comment that unambiguously asks for a new section into a ``section`` task.

    Coverage is preserved: a promoted comment leaves its old task and joins the section task,
    a task emptied by promotion is dropped, and a comment's own words become the section's
    ``query`` (the exact focus the report/gather is steered by)."""
    promoted: list[str] = []
    kept: list[dict] = []
    for t in tasks:
        if t["need"] == "section":
            kept.append(t)
            continue
        stay = [c for c in t["comments"] if not _SECTION_ASK.search(c)]
        promoted += [c for c in t["comments"] if _SECTION_ASK.search(c)]
        if stay:
            kept.append({**t, "comments": stay})
    for c in promoted:
        kept.append({"comments": [c], "need": "section", "query": c.strip()})
    return kept


# A reviewer who says the papers are ALREADY in Zotero and asks to cite them wants embed-and-cite
# (`build → revise`), NOT a fetch (`ingest`). The 8B coordinator files this under `ingest` even
# when the wording is unambiguous — it sees "cite <author> <year>" and stops there — which is
# exactly the DRvehicle misroute. This is the deterministic floor under the `cite` rule: an
# explicit "already in Zotero / in the collection / in the library" membership assertion is a
# `cite` task no matter what the model called it. Kept tight — it requires naming zotero / the
# collection / the library, so a genuine ingest ("cite Smith 2020", "add @key", "these
# references:") never trips it and gets wrongly routed away from the fetch it needs.
_CITE_ASK = re.compile(
    r"\b(?:added?|already|now|have|has|it'?s|they'?re|these are|are)\b[^.!?]*"
    r"\b(?:zotero|the collection|the library)\b",
    re.IGNORECASE)


def _promote_explicit_cites(tasks: list[dict]) -> list[dict]:
    """Move any comment asserting its papers are ALREADY in Zotero into a ``cite`` task.

    The deterministic floor under decompose rule ``cite``: an explicit "already in Zotero / the
    collection / the library" assertion routes embed-and-cite (``build → revise``), never a fetch
    (``ingest``). ``section``/``redirect`` asks outrank it — they re-plan the review, and run
    first, so their tasks are left intact; only edit/sources/ingest comments are demoted here.
    Coverage is preserved exactly as in :func:`_promote_explicit_sections`."""
    promoted: list[str] = []
    kept: list[dict] = []
    for t in tasks:
        if t["need"] in ("section", "redirect", "cite"):
            kept.append(t)
            continue
        stay = [c for c in t["comments"] if not _CITE_ASK.search(c)]
        promoted += [c for c in t["comments"] if _CITE_ASK.search(c)]
        if stay:
            kept.append({**t, "comments": stay})
    for c in promoted:
        kept.append({"comments": [c], "need": "cite", "query": ""})
    return kept


def chain_from_tasks(tasks: list[dict]) -> dict:
    """Derive the litreview rework chain, its instructions, and a summary tier FROM the tasks.

    The chain is BUILT, not looked up. Sources reach the corpus by two routes that end the
    same way — a word-sense ``audit`` of what changed, then (for a ``revise`` re-draft) a
    ``build`` that embeds it, because ``revise`` loads a cached corpus and no longer embeds:

      * ``sources``/``section``/``redirect`` fetch new literature: ``gather → collect → audit``.
      * ``ingest`` pulls reviewer-supplied references not yet in Zotero: ``ingest → collect →
        audit`` (collect lets the human add any the fetch missed).
      * ``cite`` names papers the reviewer already put in Zotero: no fetch, no audit (deference
        forbids quarantining sources the reviewer named) — only a ``build`` to embed them.

    The redraft verb is ``report`` ONLY for a ``redirect`` (it re-plans every section and embeds
    inline, so it needs no separate ``build``); every other set redrafts with ``revise``, which
    walks the comments and answers each one in kind — a sentence rewrite for a prose comment, a
    drafted section spliced in at the anchor for a ``section`` ask. ``gather_topics`` and
    ``section_focus`` are the exact queries of the tasks that need them — the specific comments
    deterministically steer the gather.

    ``tier`` is emergent (redirection > gap_fill > cosmetic), so the confirm-gate and notify that
    key on it keep working as a SUMMARY of the decomposition rather than its driver.
    """
    needs = {t["need"] for t in tasks}
    gather_topics = [t["query"] for t in tasks
                     if t["need"] in ("sources", "section", "redirect") and t["query"]]
    section_focus = [t["query"] for t in tasks
                     if t["need"] in ("section", "redirect") and t["query"]]
    corrections = [{"wrong": t["wrong"], "right": t["right"]} for t in tasks
                   if t["need"] == "correct" and t.get("wrong") and t.get("right")]
    needs_sources = bool(needs & {"sources", "section", "redirect"})
    needs_redirect = bool(needs & {"redirect"})
    needs_report = needs_redirect

    steps: list[str] = []
    # A correction is NOT a queued step. It is a deterministic substitution across the brief, the
    # litrev config and the current draft, applied by the planner before the chain is queued, so
    # everything downstream reads the corrected term. Queueing it would make a one-right-answer
    # edit depend on a task running, which is the failure it exists to fix.
    if "ingest" in needs:
        steps.append("ingest")
    if needs_sources:
        steps += ["gather", "collect"]
    elif "ingest" in needs:
        # ingest matches what it can in Zotero and lists the rest; the human finalises those at
        # a collect step before the re-draft.
        steps.append("collect")
    # A changed corpus is word-sense audited before any re-draft reads it.
    if "collect" in steps:
        steps.append("audit")
    # `report` regenerates every section, so it is reachable ONLY from a redirect — a genuine
    # change of direction, where a second full read is the honest price.
    #
    # Everything else redrafts with `revise`, which answers the comments one at a time and
    # matches the response to the ask: a prose comment gets a tracked sentence rewrite, a
    # section ask gets a drafted section spliced in at the comment that asked for it. The verb
    # used to be chosen by the HEAVIEST need in the set, so a single section ask sent the whole
    # annotation set to a verb that could not carry in-place edits and the edits beside it were
    # dropped without a word. Rework is now scaled to each ask rather than to the set.
    redraft = "report" if needs_redirect else "revise"
    # `revise` loads a cached corpus, so a corpus that CHANGED (collect present) or papers the
    # reviewer added straight to Zotero for citing (`cite`) must be EMBEDDED first: `build` runs
    # immediately before revise. A `report` re-draft embeds inline, so it never gets a build.
    if redraft == "revise" and ("collect" in steps or "cite" in needs):
        steps.append("build")
    # A chain whose ONLY work is a correction needs no re-draft: the substitution is
    # deterministic and total, and a reviser adds nothing but the chance of missing an
    # occurrence. It still re-renders so the reviewer gets the corrected document back.
    if not (corrections and needs == {"correct"}):
        steps.append(redraft)
    steps.append("mindmap")     # per-draft diagnostic: regenerate the contribution map beside the draft
    steps.append("comment")

    tier = ("redirection" if "redirect" in needs
            else "gap_fill" if needs_sources else "cosmetic")
    return {"steps": steps, "tier": tier, "corrections": corrections,
            "gather_topics": gather_topics, "section_focus": section_focus}


def _tasks_assessment(tasks: list[dict]) -> str:
    """One-line human summary of the task breakdown, for the plan record and the log."""
    from collections import Counter
    counts = Counter(t["need"] for t in tasks)
    return ", ".join(f"{counts[n]}×{n}" for n in _LITREVIEW_NEEDS if counts[n])


def _apply_corrections(root: Path, m: project.Manifest, directory: str,
                       corrections: list[dict]) -> dict[str, int]:
    """Substitute a corrected term everywhere the project states it about ITSELF.

    The brief in haarpi.yaml is haarpi's; the litrev config's topic/focus/research_prompt are
    rabbitHole's, reached through the same soft-import as the gather steering. Both must move
    together — the brief seeds a re-init and the config drives every gather, so correcting one
    and not the other leaves the error live in the half that was missed.

    Returns ``{target: substitutions}``, which the caller records on the plan and reports. A
    correction that changes NOTHING is reported as such: it means the term the reviewer named
    is not the term the project holds, and quietly succeeding would hide that.
    """
    counts: dict[str, int] = {}
    for c in corrections:
        wrong, right = c.get("wrong", ""), c.get("right", "")
        if not (wrong and right):
            continue
        try:
            from rabbithole import steering as _rhsteer
        except ImportError:
            _rhsteer = None
        if _rhsteer is not None:
            try:
                for k, v in _rhsteer.apply_correction(directory, wrong, right).items():
                    counts[k] = counts.get(k, 0) + v
            except Exception as e:  # noqa: BLE001 — never crash the gate on a steering write
                print(f"  [warn] could not correct the litrev config ({e})")
        try:
            from rabbithole.steering import _sub_term
            new_brief, n = _sub_term(m.brief, wrong, right)
        except ImportError:
            new_brief, n = m.brief, 0
        if n:
            m.brief = new_brief
            project.save_manifest(m, root)
            counts[project.MANIFEST] = counts.get(project.MANIFEST, 0) + n
    return counts


def _write_litreview_steering(directory: str, built: dict) -> str | None:
    """INSTRUCT the gather/report: write a new numbered litrev config carrying the tasks' queries.

    This is the channel the current classify path never wrote — its gather_topics went only into
    a task description and steered nothing. The config helpers live in rabbitHole (litreview's
    own config format); haarpi soft-imports them, exactly as it soft-imports raconteur for the
    paper ladder, so there is no hard dependency and a stack without rabbitHole degrades quietly.
    """
    if not (built["gather_topics"] or built["section_focus"]):
        return None
    try:
        from rabbithole import steering as _rhsteer
    except ImportError:
        return None
    extra_focus = "; ".join(built["section_focus"])
    try:
        if "gather" in built["steps"]:
            fp = _rhsteer._write_gap_config(
                directory, {"gather_topics": built["gather_topics"]}, extra_focus)
        elif extra_focus:
            fp = _rhsteer._write_section_config(directory, extra_focus)
        else:
            return None
    except Exception as e:  # noqa: BLE001 — a steering-config failure must not crash the gate;
        print(f"  [warn] could not write gather-steering config ({e}) — "
              f"the chain will run unsteered")   # the chain still runs, just from the base config
        return None
    return getattr(fp, "name", str(fp))


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
            # A deck (.pptx) is reviewed IN PLACE — PowerPoint comments live in the same file the
            # tool drafted (`…_deck_ra.pptx`); there is no rename to a reviewer's initials. So the
            # "a human went last" signal is the presence of a comment, not the chain tail. A draft
            # nobody has commented on is not finished markup; a release (bare chain) is never markup.
            for p in d.glob("*.pptx"):
                parsed = naming.parse(p, m.short_title)
                if not parsed or naming.is_release(parsed[1]):
                    continue
                if redline.pptx_comment_threads(p):
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


def _render_pptx_pdf(pptx: Path) -> Path | None:
    """A PDF twin of a minted deck, same content as the .pptx, written beside it.

    LibreOffice headless is the only faithful .pptx renderer on a server (python cannot draw
    slides). Best-effort: if `soffice`/`libreoffice` isn't installed the mint is never blocked —
    it just skips the PDF. Fidelity depends on the deck's fonts being installed where this runs;
    for a pixel-exact match, export the PDF from PowerPoint itself. Each call uses a throwaway
    user profile so concurrent conversions don't collide on LibreOffice's single-instance lock."""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        return None
    with tempfile.TemporaryDirectory() as prof:
        try:
            subprocess.run(
                [soffice, "--headless", f"-env:UserInstallation=file://{prof}",
                 "--convert-to", "pdf", "--outdir", str(pptx.parent), str(pptx)],
                check=True, capture_output=True, timeout=180)
        except (subprocess.SubprocessError, OSError):
            return None
    pdf = pptx.with_suffix(".pdf")
    return pdf if pdf.is_file() else None


def _archive_chain(root: Path, m: project.Manifest, stage: str, release: Path) -> int:
    """Move the spent chain files aside so output/ holds releases + live work only."""
    d = release.parent
    dest = d.parent / "archive" / release.stem
    n = 0
    for p in list(d.glob("*.docx")) + list(d.glob("*.md")) + list(d.glob("*.pptx")):
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


# How each attended stage opens: (opening verb, task label, blurb). One tool now owns two
# attended stages (rayleigh: `design` opens with `init` — the analytical framework; `experiments`
# opens with `plan` — the executable experiments against the raster-built tooling), so the verb is
# keyed by STAGE, not tool. Both are interactive Cale+Claude sessions; `rayleigh plan` hands off to
# conduct itself once the compute is confirmed.
_OPENING: dict[str, tuple[str, str, str]] = {
    "design":      ("init", "design session",
                    "Interactive preregistration/design session (the analytical framework)"),
    "build":       ("plan", "design session", "Interactive design session"),
    "experiments": ("plan", "experiment design session",
                    "Design the executable experiments that use the built tooling to fulfil the "
                    "framework, then hand off to conduct"),
    # (deck opens via `_open_deck` — its opening move is the `razzle interview` configure task, and
    # the per-format authoring is run by hand from the commands the interview prints, so it is not a
    # simple `haarpi <tool> <verb>` keyed here.)
}


def _open_deck(client, m: project.Manifest, tr_cfg: dict) -> None:
    """Open the deck stage with TWO tasks: the human interview, then the unattended authoring.

    The interview is genuinely interactive — it asks which formats, and per format the venue, date,
    presenters, logos and funders, none of which a tool may invent — so it stays the human's.

    The authoring that follows is not. Every decision it needs was settled by the interview, and
    what it produces meets the human again at the redline gate, so nobody needs to sit through it.
    It is queued as ONE task depending on the interview, and fans out over the configured formats
    when it RUNS: haarpi still does not create a task per format, because at the moment the board
    is written the interview has not been held and the formats do not exist yet.

    It runs on the CPU runner. The `claude` resource would describe the work better, but nothing
    polls that resource, so a task filed there would never execute — the lane has to be one with a
    runner behind it.
    """
    interview = client.create_task(
        "razzle deck: configure", m.trundlr_project_id,
        description="Configure this project's deck(s): run `haarpi razzle interview` — a pure-python "
                    "(no-LLM) session that captures the presentation format(s) and, per format, the "
                    "venue, date, presenting authors, affiliation logos, and funders. It writes the "
                    "config; the authoring task behind this one reads it and runs unattended.",
        resource_id=_resource_id(tr_cfg, "human"), duration=0.5)
    client.create_task(
        "razzle deck: author", m.trundlr_project_id,
        command="haarpi razzle deck --all-formats --headless",
        depends_on_id=(interview or {}).get("id"),
        description="Author a deck for every format the interview configured, and render each to "
                    "the branded .pptx. Unattended — the facts were settled in the interview and "
                    "the rendered deck meets you at the gate.",
        resource_id=_resource_id(tr_cfg, "cpu"), duration=1.5)


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
        if stage == "deck" and not _has_assembled_submission(root, m):
            continue        # the deck opens on submission-assembled, not a bare manuscript release
        tool = spec["tool"]
        if stage == "deck":
            # The deck stage opens with a single `razzle interview` configure task (like every other
            # stage's one opening move). The interview captures the formats + facts and prints the
            # per-format `razzle deck --format <fmt>` commands; haarpi does not own the format
            # vocabulary and does not queue a task per format — the deck is tracked by its deliverable.
            _open_deck(client, m, tr_cfg)
        elif spec.get("attended"):
            verb, label, blurb = _OPENING.get(stage, ("init", "design session",
                                                      "Interactive design session"))
            client.create_task(
                f"{tool} {label}", m.trundlr_project_id,
                description=f"{blurb} — run: haarpi {tool} {verb}",
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


def _ladder_line(root: Path, m: project.Manifest, venue: str, here: str) -> str:
    """Which rungs have a release, and which one this markup is. Progress, in one line.

    `haarpi next` resolved all of this and said none of it: it named neither the file it
    gated nor where that file sat on the ladder. On a project whose manuscript chain reads
    ``_ra_C_DCR_JIR_DCR.docx`` the first is not guessable, and the second is what tells you
    a queued draft is about to fail for want of a rung that was never minted.
    """
    from . import naming as _n
    parts = []
    for rung in _PAPER_LADDER:
        name = _LADDER_NAME.get(rung, rung)
        if rung == "package":
            parts.append(name if rung != here else f"{name} ← HERE")
            continue
        home = m.stage_dir(root, "paper")
        if venue:
            home = home / venue
        d = (home / rung / "output") if rung else (home / "output")
        rel = None
        if d.is_dir():
            rel = _n.find_latest_release(
                d, m.short_title, "docx",
                chain_includes=[x for x in (venue, rung) if x] or None)
        mark = " ← HERE" if rung == here else ""
        parts.append(f"{name} ✓{mark}" if rel else f"{name}{mark}")
    return " · ".join(parts)


def run_next(root: Path, stage: str | None = None, file: Path | None = None,
             dry_run: bool = False, no_queue: bool = False) -> int:
    """Read the finished markup: mint a release, or classify and queue rework.

    ``no_queue`` mints and records the gate but queues nothing. For iterating on a rung —
    regenerate, redline, mint, look at it — without a chain appearing in trundlr each time
    and having to be cancelled. The gate record is still written, so the ladder's own state
    stays truthful; only the scheduler is left alone.
    """
    m = project.load_manifest(root)
    cfg = pipeline_config()
    tr_cfg = cfg.get("trundlr", {})
    # One place decides. Three branches below queue, and they must agree — a mint that
    # skipped the rung queue while rework still fired would be worse than no flag at all.
    queueing = bool(m.trundlr_project_id) and not no_queue
    skipped = " ; queueing skipped (--no-queue)" if m.trundlr_project_id and no_queue else ""

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

    # Say where we are and what we are reading BEFORE deciding anything, so the header is
    # there whether this mints, queues rework, or refuses. Resolved but unsaid until now:
    # the run named neither the file it gated nor its place on the ladder.
    deliverable = _deliverable_of(markup, m.short_title) if stage == "paper" else ""
    # The deck is laid out one folder per format (slides/<fmt>/), so the markup's parent NAMES the
    # format — which is what a deck rework has to be told, or it re-authors the wrong deliverable.
    venue = (naming.venue_of(markup, m.short_title) if stage == "paper"
             else markup.parent.name if stage == "deck" else "")
    check = redline.gate_check(markup)
    rung = _DELIVERABLE_LABEL.get(deliverable, deliverable) if stage == "paper" else stage
    print(f"haarpi next: {stage}" + (f" · {venue}" if venue else "")
          + (f" · {deliverable or 'manuscript'} rung" if stage == "paper" else ""))
    print(f"  reading   {markup.name}")
    print(f"  markup    {len(check['unresolved'])} unresolved comment(s), "
          f"{check['reviewer_changes']} reviewer edit(s)")
    if stage == "paper":
        print(f"  ladder    {_ladder_line(root, m, venue, deliverable)}")

    ahash = project.annotation_hash(check["unresolved"], check["reviewer_changes"],
                                    markup.name)
    if project.already_planned(root, ahash):
        prior = next((e for e in reversed(project.list_plans(root))
                      if e.get("annotation_hash") == ahash), {})
        fp = project.plan_file_for(root, ahash)
        print(f"  [stop]    this annotation set was already planned (hash {ahash}) — loop "
              f"guard, refusing to plan it twice.")
        if prior:
            print(f"            recorded {prior.get('at','?')} on {prior.get('markup','?')}"
                  + (f", released {prior['release']}" if prior.get("release") else ""))
        if fp:
            try:
                shown = fp.relative_to(root)
            except ValueError:
                shown = fp
            print(f"            blocking: {shown}")
            print(f"            to re-run it:  mv -n {shown} {shown.parent}/old/")
        return 0
    infix = "_".join(p for p in (venue, deliverable or
                                 (m.stages[stage].get("infix") or "")) if p)
    if check["clean"]:
        ext = markup.suffix.lstrip(".").lower()      # docx deliverable, or a deck's pptx
        rel_name = naming.release_name(m.short_title, ext, infix=infix)
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
        # A rung may need its release reconciled with what the author actually approved.
        # The skeleton does: its word-plan comments were written against the structure as
        # generated, and the author has since moved subsections. Reconciling at the mint is
        # what keeps a routine structural edit from becoming an error the next rung refuses.
        post = None
        if deliverable in ("skeleton", "outline"):
            try:
                if deliverable == "skeleton":
                    from raconteur.skeleton import reconcile_plan as post
                else:
                    from raconteur.outline import reconcile_plan as post
            except ImportError:               # raconteur not installed in this stack
                post = None
        # ONE artifact per release: the document the author gated, with its word plan on
        # the headings. A markdown sibling was a second copy of an approved contract, and a
        # derived one — it went stale the moment its deriver was fixed, and could not carry
        # a comment at all.
        # The paper ladder is off the markdown sibling entirely now. skeleton and outline
        # went first; the manuscript followed once package stopped reading one — it takes
        # the release .docx through read_release, and the abstract off the same file. What
        # remains on markdown is the litreview stage, whose consumers still read it.
        deck_pdf = None
        if ext == "pptx":
            # A deck is minted by PROMOTION, not redline resolution: its comments are all
            # resolved, a .pptx has no tracked changes to accept, and there is no markdown
            # sibling. Copy the reviewed .pptx to the release name; the author hand-polishes
            # from here (gate model B — the deck is a communication artifact, not a prereg).
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(markup, dst)
            # A PDF twin of the released deck, same content (for circulation/archival). Best-effort:
            # a missing converter never blocks the mint.
            deck_pdf = _render_pptx_pdf(dst)
        else:
            result = redline.mint_release(
                markup, dst, post=post,
                md_sibling=stage != "paper")

        if deliverable:
            # A ladder rung, not the stage: mint this deliverable's release and
            # queue the next rung. The working chain stays put (no archive) and
            # downstream STAGES neither advance nor refresh — only the bare
            # manuscript's gate speaks for the paper stage.
            project.record_plan(root, {
                "type": "gate", "stage": stage, "deliverable": deliverable,
                "venue": venue, "annotation_hash": ahash, "markup": markup.name,
                "release": dst.name})
            queued_note = skipped
            if queueing:
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
            return 0

        archived = _archive_chain(root, m, stage, dst)
        project.record_plan(root, {
            "type": "gate", "stage": stage, "annotation_hash": ahash,
            "markup": markup.name, "release": dst.name, "archived": archived})
        opened, refreshed, packaged = [], [], ""
        if queueing:
            try:
                client = trundlr.TrundlrClient(tr_cfg.get("url", ""))
                opened = _advance(root, m, client, tr_cfg)
                refreshed = _refresh_stale(root, m, client, tr_cfg, stage, dst)
                if stage == "paper" and venue:      # the approved manuscript -> package it
                    packaged = _queue_packaging(root, m, client, tr_cfg, venue, dst)
            except trundlr.TrundlrError as e:
                print(f"  [trundlr] advance skipped: {e}")
        msg = (f"{stage}: gate PASSED — released {dst.name}"
               + (f" (+PDF {deck_pdf.name})" if deck_pdf else
                  "; PDF skipped (LibreOffice not found)" if ext == "pptx" else "")
               + (f"; opened {', '.join(opened)}" if opened else "")
               + (f"; refresh queued for {', '.join(refreshed)}" if refreshed else "")
               + packaged + skipped)
        print(f"haarpi next: {msg}")
        return 0

    # unresolved asks -> plan + queue rework
    dtiers = PAPER_DELIVERABLE_TIERS.get(deliverable) if deliverable else None
    built = None
    if stage == "litreview":
        # The general solution: decompose the comments into a per-comment task list, derive the
        # chain the tasks require, and steer each verb with what its tasks need. Open-loop — the
        # human is the verification loop. See DESIGN_next_orchestration.md.
        tasks = decompose(check["unresolved"], cfg)
        built = chain_from_tasks(tasks)
        tier, steps = built["tier"], built["steps"]
        plan = {"tier": tier, "steps": steps, "tasks": tasks,
                "gather_topics": built["gather_topics"],
                "assessment": _tasks_assessment(tasks)}
    else:
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
    # INSTRUCT: write the numbered litrev config that steers the gather/report at the tasks'
    # queries. This is a file side-effect, so it happens only past the dry-run return, and for
    # both the queued and the manual paths (a manual gather deserves steering too).
    steer_config = _write_litreview_steering(str(root), built) if built else None
    if steer_config:
        summary.append(f"  steering config: {steer_config}")
    # CORRECT: a factual correction is applied to the project's own statements of itself before
    # anything else in the chain reads them. Deterministic, so it happens here rather than
    # becoming a task nobody can verify ran.
    corrections = (built or {}).get("corrections") or []
    corr_counts = _apply_corrections(root, m, str(root), corrections) if corrections else {}
    for c in corrections:
        applied = ", ".join(f"{k} ×{v}" for k, v in corr_counts.items()) or "NOTHING MATCHED"
        summary.append(f"  correction: {c['wrong']!r} -> {c['right']!r}  [{applied}]")
    confirm = tier in (cfg.get("planner", {}).get("confirm_tiers") or [])
    if confirm:
        summary.append("  confirm_tiers: an 'approve plan' task gates this chain")
    if plan.get("gather_topics"):
        summary.append(f"  gather topics: {', '.join(plan['gather_topics'])}")
    entry = {"type": "plan", "stage": stage, "deliverable": deliverable, "venue": venue,
             "annotation_hash": ahash,
             "markup": markup.name, "tier": tier, "steps": steps,
             "assessment": plan.get("assessment", ""),
             "steer_config": steer_config,
             "corrections": corrections, "correction_counts": corr_counts,
             "bindings": _current_bindings(root, m, stage)}
    if not queueing:
        project.record_plan(root, entry)
        why = ("--no-queue" if m.trundlr_project_id else "no project id")
        print("\n".join(summary + [f"  [trundlr] {why} — run the chain manually:"]
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
    return 0


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
        if any((p := _parse_title(t)) and p[0] == "litreview" for t in titles):
            print(f"haarpi queue: litreview already has tasks "
                  f"({len(titles)} total) — nothing to queue.")
            return 0
        queued = queue_chain(client, m.trundlr_project_id, "litreview",
                             ["gather", "collect", "report", "mindmap", "comment"], tr_cfg,
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


def _ask_multiline(label: str, default: str = "") -> str:
    """Read a long-form, possibly multi-paragraph value (the research brief).

    A single `input()` returns at the first newline, so pasting a multi-paragraph
    brief would answer *this* prompt with line one and let every remaining line fall
    through into the following questions (initials, priority) — the paste corruption
    that split one brief across `brief` and `initials`. Here we consume every pasted
    line until an explicit terminator — a line containing only `.`, or EOF/Ctrl-D —
    so the whole paste lands in a single field. Interior blank lines (paragraph
    breaks) are preserved; they do not end the read."""
    hint = "paste it, then a line with just '.' (or Ctrl-D) to finish"
    tail = f" [{default}]" if default else ""
    print(f"  {label} — {hint}{tail}:")
    lines: list[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip() == ".":
            break
        lines.append(line)
    return "\n".join(lines).strip() or default


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
        # Suggest the project name's OWN case (camelCase), not a lowercased slug: the tools
        # name every file with the project name, and naming.parse matches the short_title
        # case-sensitively — so a lowercased short_title makes `haarpi next` blind to its own
        # markup and the ladder stalls silently (the postIneq case, 2026-08).
        short_title=short_title or _ask("short title (filename stem — match the name's case)",
                                        name or default_name),
        brief=brief if brief is not None else _ask_multiline("research brief (long-form)"),
        initials=initials or _ask("your initials (revision chain)", "DCR"),
        trundlr_priority=(trundlr.clamp_priority(priority) if priority is not None
                          else _ask_priority() if asks_trundlr
                          else trundlr.PRIORITY_DEFAULT),
    )
    lines = []
    client = None
    pid = None
    # REGISTERING the trundlr project is safe here: it creates no runnable work. Only the
    # queueing below has to wait for the project to exist on disk.
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
        except trundlr.TrundlrError as e:
            client = None
            lines.append(f"[trundlr] skipped ({e}) — register + queue later with "
                         "`haarpi queue`")
    else:
        lines.append("[trundlr] not configured/disabled — queue later with `haarpi queue`")

    # The project must EXIST before any runnable task naming it is published. `queue_chain` used
    # to run above these three lines, so a `rabbithole gather` was claimable while init was still
    # scaffolding: an idle runner took it and died on "No litrev.yaml found in litReview. Run
    # `rabbitHole init` first" one second after init queued it (humanTraject, 2026-08-31). The
    # task then sat in_progress — the runner could not record a failure that finished before its
    # own scheduled start — and every other task on that runner's resource stopped with it.
    project.save_manifest(m, root)
    project.scaffold(root, m)
    seeded = project.seed_tool_configs(root, m)
    if seeded:
        lines.append("seeded stage config(s): " + ", ".join(seeded))

    if client is not None and pid is not None:
        try:
            queued = queue_chain(client, pid, "litreview",
                                 ["gather", "collect", "report", "mindmap", "comment"], tr_cfg,
                                 description=m.brief[:300])
            lines.append(f"queued litreview cycle {queued['cycle']} "
                         f"({len(queued['tasks'])} tasks, ends in `haarpi next`)")
        except trundlr.TrundlrError as e:
            lines.append(f"[trundlr] registered but queued nothing ({e}) — queue with "
                         "`haarpi queue`")
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


def _submission_assembled(state: str) -> bool:
    """A submission-state string (:func:`_submission_state`) that means the submission is at least
    assembled — 'assembled …' (built, maybe uncompiled) or 'packaged …' (compiled PDF/docx)."""
    return state.startswith(("assembled", "packaged"))


def _has_assembled_submission(root: Path, m: project.Manifest) -> bool:
    """True once at least one selected venue's submission is assembled — the 'paper is finished and
    going out' milestone that opens the deck stage. A deck is built from the one-pager + manuscript +
    figures, but it should not open the moment a manuscript mints: it opens when the paper is actually
    done and committed to a venue, so the deck's venue is known and its narrative/results are final."""
    return any(_submission_assembled(_submission_state(root, m, v)) for v in _selected_venues(root))


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

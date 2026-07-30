"""The shared redline engine: read a comment, classify it, then respond — once, for every
redlining agent.

rabbitHole and raconteur each grew their own near-identical redline loop (sentence-indexed
edits, deterministic guards, a fail-closed audit, one honest reply per comment). This module
is that loop, extracted, so a new deliverable inherits the discipline instead of re-deriving
it. See `packages/haarpi/DESIGN_redline_engine.md`.

The division of labour:

  * THE ENGINE owns the invariant — the per-comment pipeline, the four primals, the new
    TRIAGE stage (classify BEFORE attempting), the sentence-edit mechanics, and the
    fail-closed contract. It is deliberately ignorant of where citations come from or what a
    litreview paragraph should read like.

  * THE POLICY owns the substance — the citeable evidence (a corpus vs a refs.bib), the
    house-style prompts, the guard set (which still carries tool vocabulary: "corpus" vs
    "bibliography"), and how a routed comment is dispatched. One object per tool.

The polestar is unchanged: guards in Python decide THAT an edit is broken and say so as an
imperative; the LLM decides only what code cannot — does the edit MEAN what the comment asked?
And fail closed: a broken edit under a reply claiming "done" is worse than a visible skip.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Protocol, runtime_checkable

from haarpi.text import sentence_units

# The context window handed to the per-paragraph calls. One paragraph plus its evidence is
# small; the generous ceiling is headroom for a dense bibliography slice, not an estimate.
NUM_CTX = 16384


# ── the four primals ──────────────────────────────────────────────────────────

class Disposition(str, Enum):
    """What became of one comment. Every comment gets exactly one — silence is not a decision.

    `str` mixin so a disposition prints and compares as its value: a routed comment is
    stored as ``"routed:sources"`` and the class is recovered by splitting, which keeps the
    on-disk disposition map a plain ``{id: str}`` the tools already speak.
    """

    EDITED = "edited"       # a tracked prose rewrite that passed every guard and the audit
    ROUTED = "routed"       # answerable, but not by editing this paragraph — carries a class
    SKIPPED = "skipped"     # fail-closed: no verifiable edit could be produced; left as-is
    OBSOLETE = "obsolete"   # the anchored text was deleted — nothing here to revise


def routed(cls: str) -> str:
    """The wire form of a ROUTED disposition, e.g. ``routed:sources``."""
    return f"{Disposition.ROUTED.value}:{cls}"


def route_class_of(disposition: str) -> str | None:
    """The class of a ROUTED disposition, or None if it is not one."""
    if disposition.startswith(Disposition.ROUTED.value + ":"):
        return disposition.split(":", 1)[1]
    return None


# ── the data a policy is handed ───────────────────────────────────────────────

@dataclass
class ParaContext:
    """One commented paragraph, and everything the engine knows about it that is not prose
    style or evidence provenance. Assembled by the orchestrator from the shared
    `haarpi.redline` primitives; handed to the policy so it can build evidence and prompts.
    """

    heading: str
    text: str
    comments: list[str]                     # thread-assembled asks (followups + repeat signal)
    anchored: set[int] = field(default_factory=set)   # sentence indices the comment bears on
    cited_keys: set[str] = field(default_factory=set)  # [@keys] already in the paragraph
    named_keys: set[str] = field(default_factory=set)  # [@keys] the COMMENTS name — triage
    kind: str = "prose"                     # "methods" | "results" | "prose" | ...
    authored: dict[str, str] = field(default_factory=dict)   # ⟦a:N⟧ → the author's exact words
    is_heading: bool = False                # the comment sits on a heading, not body prose
    obsolete_ids: set[str] = field(default_factory=set)  # comment ids whose anchor was deleted


@dataclass
class Evidence:
    """The ONLY sources this paragraph may cite, and the prose context around them.

    `known` is the set of citeable keys — a guard rejects any `[@key]` not in it. `context`
    is the rendered block (a corpus digest, or a refs.bib slice plus the section writeup)
    dropped into the revise prompt. The engine can extend both when triage pulls a named
    source in, without knowing what either contains.
    """

    known: set[str]
    context: str

    def with_line(self, line: EvidenceLine) -> Evidence:
        return Evidence(known=self.known | {line.citekey},
                        context=(self.context + "\n" + line.text).strip())


@dataclass
class EvidenceLine:
    """A single source made citeable — its key, and the one line the reviser is shown."""

    citekey: str
    text: str


@dataclass
class TriageResult:
    """The verdict of stage ①. Either proceed to the edit attempt (possibly with sources
    pulled in), or a terminal disposition with the reply to leave."""

    proceed: bool
    disposition: str | None = None
    reply: str | None = None
    extra_evidence: list[EvidenceLine] = field(default_factory=list)

    @classmethod
    def go(cls, extra: list[EvidenceLine] | None = None) -> TriageResult:
        return cls(proceed=True, extra_evidence=extra or [])

    @classmethod
    def stop(cls, disposition: str, reply: str) -> TriageResult:
        return cls(proceed=False, disposition=disposition, reply=reply)


@runtime_checkable
class RedlinePolicy(Protocol):
    """What a tool injects to make the engine speak its dialect. Every method is a seam the
    two tools genuinely differ on; everything they share is in the engine."""

    author: str                          # "rabbitHole" | "raconteur" — the tracked-change author
    route_classes: tuple[str, ...]       # this deliverable's ROUTE vocabulary; [0] = the
                                         #   fallback for an unrecognised class ("sources")
    # route_verb (optional, default "ROUTE"): the word the policy's audit prompt uses to open
    # a route verdict. raconteur says "ROUTE:", rabbitHole says "CORPUS:" — same shape, and
    # the engine reads it via getattr so a policy that omits it gets "ROUTE".

    # ── evidence ──────────────────────────────────────────────────────────────
    def evidence_for(self, ctx: ParaContext) -> Evidence:
        """The citeable set and prose context for this paragraph."""

    def resolve_named_source(self, citekey: str) -> EvidenceLine | None:
        """A `[@key]` a comment names but the paragraph cannot yet cite. Pull it in and
        return its line, or None to route the comment as a missing source. rabbitHole fills
        this with a whole-library Zotero lookup; raconteur returns None until it may delegate.
        """

    # ── prompts / house style ───────────────────────────────────────────────────
    def revise_system(self) -> str: ...
    def audit_system(self) -> str: ...

    def revise_user(self, ctx: ParaContext, evidence: Evidence,
                    numbered_sentences: str, comment_block: str) -> str:
        """The revise prompt body. The engine supplies the shared, mechanical parts (the
        numbered sentences, the comment block); the policy wraps them in its house style and
        drops in the evidence context and any authored-span legend."""

    def audit_user(self, ctx: ParaContext, revised: str, comment_block: str) -> str: ...

    # ── guards beyond what the audit judges ─────────────────────────────────────
    def guard_findings(self, old_text: str, new_text: str, touched: set[int],
                       ctx: ParaContext, evidence: Evidence) -> list:
        """Everything mechanical this deliverable can decide about the rewrite — the core
        set (dropped citekeys, author-year prose, sentinels, minimality, unresolved keys)
        plus anything tool-specific (authored atoms, style signature, a section-kind cite
        gate). Each returned object must expose `.imperative`: the instruction fed back to a
        re-revise. An empty list means the text is mechanically sound."""


# ── sentence-indexed edits (pure, shared) ─────────────────────────────────────

def number_sentences(units: list[str], anchored: set[int]) -> str:
    """Render the paragraph as numbered sentences, marking those a comment bears on with ▶."""
    return "\n".join(
        f"{'▶' if i in anchored else ' '} {i + 1}. {u.strip()}"
        for i, u in enumerate(units))


def apply_sentence_edits(units: list[str], edits: dict) -> str:
    """Rebuild the paragraph from the original units plus the reviser's replacements.

    Every sentence the reviser did not return is copied byte-for-byte — this is what makes
    minimality true by construction rather than hoped for from a diff: the untouched
    sentences are literally the original objects.
    """
    out: list[str] = []
    for i, unit in enumerate(units):
        key = str(i + 1)
        if key not in edits:
            out.append(unit)
            continue
        repl = edits[key]
        if repl is None:
            continue  # deleted
        trailing = unit[len(unit.rstrip()):]   # keep the original inter-sentence spacing
        out.append(repl.strip() + trailing)
    return "".join(out)


def parse_sentence_edits(raw: str, n_units: int,
                         authored: dict | None = None) -> tuple[dict, dict, list[str]]:
    """Parse the reviser's JSON. Returns (edits, copyedits, errors).

    Strict on purpose: a lenient parser that fell back to an empty dict on malformed output
    would look exactly like a well-formed edit of nothing, and the engine would write no
    tracked change while replying that the comment was addressed.

    `copyedits` are suggested corrections to the author's own ⟦a:N⟧ spans — never applied,
    delivered as comments — so a copyedit naming a span that does not exist is dropped.
    """
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    obj = None
    if m:
        try:
            parsed = json.loads(m.group(0))
            obj = parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            obj = None
    if obj is None:
        return {}, {}, ['Output was not a JSON object. Return only a JSON object mapping '
                        'sentence number to replacement text, e.g. {"2": "…"}.']

    copyedits: dict[str, str] = {}
    raw_copy = obj.pop("copyedits", None)
    if isinstance(raw_copy, dict):
        for k, v in raw_copy.items():
            key = f"⟦{str(k).strip().strip('⟦⟧')}⟧"
            if isinstance(v, str) and v.strip() and (authored is None or key in authored):
                copyedits[key] = v.strip()

    edits, errors = {}, []
    for k, v in obj.items():
        if not str(k).strip().isdigit() or not (1 <= int(k) <= n_units):
            errors.append(f'"{k}" is not a sentence number between 1 and {n_units}.')
            continue
        if v is not None and not isinstance(v, str):
            errors.append(f'The value for sentence {k} must be text or null.')
            continue
        edits[str(int(k))] = v
    return edits, copyedits, errors


def _is_ok(verdict: str) -> bool:
    v = verdict.strip().upper()
    return v == "OK" or v.startswith("OK ") or v.startswith("OK.")


def _route_class(verdict: str, route_classes: tuple[str, ...]) -> str:
    """The class from a "ROUTE: <class>: <reason>" audit verdict, validated against this
    policy's vocabulary. An unrecognised class falls back to the first one the policy
    declares — by convention its "missing sources" class."""
    rest = verdict.split(":", 1)[1] if ":" in verdict else ""
    head = rest.strip().split(":", 1)[0].strip().lower()
    return head if head in route_classes else route_classes[0]


# ── stage ①: triage — read the comment, classify it, BEFORE attempting ────────

def triage(ctx: ParaContext, evidence: Evidence, policy: RedlinePolicy) -> TriageResult:
    """Classify a comment before spending a rewrite attempt on it.

    This is the stage that did not exist in either tool: both went straight to the doomed
    edit, so "add @doblinger" (a source not in the corpus) was ground through the whole retry
    budget and fell out as a misleading SKIPPED. Here a named source that is not yet citeable
    is either pulled in — so the attempt CAN cite it — or routed honestly as a missing source,
    without ever pretending a prose edit was possible.
    """
    if ctx.is_heading:
        return TriageResult.stop(
            routed("section"),
            "This comment sits on a heading, not body prose — a redline rewrites the "
            "paragraph a comment bears on, and rewriting a heading would mangle it.")

    pulled: list[EvidenceLine] = []
    for key in sorted(ctx.named_keys - evidence.known):
        line = policy.resolve_named_source(key)
        if line is None:
            return TriageResult.stop(
                routed(policy.route_classes[0]),
                f"The comment names [@{key}], which is not in the citeable set and could not "
                f"be pulled in — routed as a missing source rather than faked in prose.")
        pulled.append(line)
    return TriageResult.go(pulled)


# ── stages ②–④: attempt → audit → classify (the fail-closed loop) ─────────────

def redline_paragraph(brain, ctx: ParaContext, policy: RedlinePolicy,
                      rounds: int = 2) -> tuple[str | None, str, dict[str, str]]:
    """Rewrite one commented paragraph and hold it to the adversarial bar.

    Returns (new_text, disposition, copyedits):
      - ("…text…", "edited", copyedits)   — passed every deterministic guard and the audit.
      - (None, "routed:<class>", {})       — a comment a prose edit cannot satisfy; caller routes.
      - (None, "skipped", copyedits)       — no edit could be produced that keeps the paragraph
                                             verifiable. Fail closed: leave it, and say so.

    `copyedits` carries any correction the reviser proposed to the author's own ⟦a:N⟧ spans —
    delivered to the author as comments, never applied.
    """
    units = sentence_units(ctx.text)
    if not units:
        return None, Disposition.SKIPPED.value, {}

    evidence = policy.evidence_for(ctx)

    # ① triage before any attempt.
    verdict = triage(ctx, evidence, policy)
    if not verdict.proceed:
        return None, verdict.disposition, {}
    for line in verdict.extra_evidence:
        evidence = evidence.with_line(line)

    comment_block = "\n".join(f"- {c}" for c in ctx.comments)
    numbered = number_sentences(units, ctx.anchored)
    base_user = policy.revise_user(ctx, evidence, numbered, comment_block)
    revise_sys = policy.revise_system()

    copyedits: dict[str, str] = {}
    critique: str | None = None
    for _ in range(rounds):
        user = base_user if critique is None else (
            base_user + f"\n\nYour previous attempt had these problems — fix every one, "
            f"changing as little else as possible:\n{critique}\n\nReturn the corrected JSON "
            f"object of changed sentences only.")
        try:
            raw = brain.coordinator(user, revise_sys, num_ctx=NUM_CTX).strip()
        except Exception:  # noqa: BLE001 — a failed call is a fail-closed skip, not a crash
            return None, Disposition.SKIPPED.value, copyedits

        edits, proposed, errors = parse_sentence_edits(raw, len(units), ctx.authored or None)
        if errors:
            critique = "\n".join(f"- {e}" for e in errors)
            continue
        copyedits.update(proposed)
        if not edits:
            # It cannot both leave the paragraph alone and have addressed the comment.
            return None, Disposition.SKIPPED.value, copyedits

        new_text = apply_sentence_edits(units, edits)
        touched = {int(k) - 1 for k in edits}

        # ② deterministic guards first — the expensive audit never sees a broken paragraph.
        findings = policy.guard_findings(ctx.text, new_text, touched, ctx, evidence)
        if findings:
            critique = "\n".join(f"- {f.imperative}" for f in findings)
            continue

        # ③ the one question left for the brain: does the edit mean what the comment asked?
        try:
            audit = brain.coordinator(policy.audit_user(ctx, new_text, comment_block),
                                      policy.audit_system(), num_ctx=NUM_CTX).strip()
        except Exception:  # noqa: BLE001 — fail closed rather than claim an unchecked success
            return None, Disposition.SKIPPED.value, copyedits

        route_verb = getattr(policy, "route_verb", "ROUTE").upper()
        if audit.upper().startswith(route_verb):
            return None, routed(_route_class(audit, policy.route_classes)), copyedits
        if _is_ok(audit):
            return new_text, Disposition.EDITED.value, copyedits
        critique = audit   # ④ audit found a problem → another round

    # Rounds exhausted. Fail closed: leave the paragraph as the reviewer wrote it.
    return None, Disposition.SKIPPED.value, copyedits

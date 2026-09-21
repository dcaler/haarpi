"""rabbitHole revise — apply reviewer annotations from a _ra.docx to re-draft the narrative.

Default mode is an in-place REDLINE: edit a copy of the annotated docx, answering each
comment with a rabbitHole-authored tracked change on the sentence(s) it anchors to, and
leave every reviewer comment in place so the next output shows them. The reviewer reads a
true tracked-changes redline beside their own notes. See `redline.py`.

The per-paragraph reviser returns only the sentences it CHANGED, keyed by index, so the set
of touched sentences is known exactly rather than estimated from a prose diff. Minimality,
citation integrity, and equation integrity are decided in Python (`guards`); the LLM audit
is left with the one question code cannot answer — does the edit mean what the comment
asked for? When no revision clears every guard, the paragraph is left as the reviewer wrote
it and the reply says so: a tracked change that quietly dropped a source, under a reply
claiming the comment was addressed, is the failure this path exists to prevent.

Breadth guards deliberately do NOT run here. A revise answers the reviewer's annotations and
nothing else; comments that need new sources route to the corpus chain below.

Comments a redline cannot satisfy in place — "include paper X", "mine its citations",
"a lot more on Y" — need the corpus to change first. revise does NOT plan that follow-up:
planning is `haarpi next`'s job (haarpi.planner), the pipeline's sole planner, which reads
the finished redline at the gate and queues the corpus chain. revise drafts and replies
only; each reply names the real reason a comment was left (so the gate's plan is grounded).

Pass --resynth for the alternative clean rewrite (no tracked changes, comments dropped):
  1. Find *_ra.docx in output/ (or accept --file path)
  2. Extract tracked changes + comments from the docx
  3. Load existing notes from work/annotations/ and slim corpus from work/corpus.json
  4. Re-synthesise the narrative using the revision brief
  5. Re-locate the cited claims against the current full text (embedding retrieval,
     keyed by citekey) so the annotated bibliography stays verifiable
  6. Write output/*_ra.md and .docx
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

from haarpi import redline as hredline
from haarpi import redline_engine as _engine
from haarpi.redline_engine import Evidence, EvidenceLine, ParaContext, route_class_of

from . import ledger, config, corpus as corpus_mod, docxio, guards, render, runlog
from .brain import Brain
from .models import Candidate
from .summarize import (
    _make_citekeys, _compact_lines, _full_lines, bibliography, citation_check,
    locate_claims, SYNTH_SYS, _enforce_paragraph_citations, _is_ok,
    _HAVE_CHROMA, _cited_indices, _legacy_notes_by_paper, _located_filename, Section,
)


def _para_digest(compact: dict[str, str], full: dict[str, str],
                 para_keys: list[str], budget: int = 20_000) -> str:
    """The evidence list for one paragraph rewrite, sized to fit the context window.

    Full digest lines — the ones carrying the numbers — for the sources the paragraph already
    cites, because those are the claims the reviser must keep grounded. Compact lines for the
    rest of the corpus, so it knows what else exists without spending 31k tokens saying so.

    The old code sent the full digest for all 84 sources to every paragraph call. At
    num_ctx=16384 Ollama discarded most of it, silently, and the reviser cited from whatever
    happened to survive at the tail.
    """
    lines: list[str] = []
    spent = 0
    for k in dict.fromkeys(para_keys):
        line = full.get(k)
        if line:
            lines.append(line)
            spent += len(line)
    for k, line in compact.items():
        if k in set(para_keys) or spent + len(line) > budget:
            continue
        lines.append(line)
        spent += len(line)
    return "\n".join(lines)


# ── output paths ───────────────────────────────────────────────────────────────

def _revision_paths(paths, annotated_docx: Path) -> tuple[Path, Path]:
    """Output paths: append _ra to the annotated file's stem."""
    stem = f"{annotated_docx.stem}_ra"
    return paths.output / f"{stem}.md", paths.output / f"{stem}.docx"


# ── load existing intermediates ───────────────────────────────────────────────

def _load_corpus(paths) -> list[Candidate]:
    if not paths.corpus_json.exists():
        return []
    data = json.loads(paths.corpus_json.read_text(encoding="utf-8"))
    return [Candidate.from_dict(d) for d in data]


def _load_notes(paths, corpus: list[Candidate], citekeys: dict[int, str]) -> list[dict]:
    """Read the per-paper notes `report` cached, keyed by citekey.

    Notes belong to the paper, not to its position in a list that changes whenever the Zotero
    collection does. A legacy positional note is accepted only when the paper it names matches
    the paper we are asking about; otherwise this loader would confidently hand Zhang's
    findings to Parrish. Missing notes come back empty rather than wrong.
    """
    legacy = _legacy_notes_by_paper(paths.annotations_dir)
    notes: list[dict] = []
    for i, c in enumerate(corpus):
        ck = citekeys.get(i) or f"{i:03d}"
        fp = paths.annotations_dir / f"{_located_filename(ck)}.json"
        if fp.exists():
            try:
                notes.append(json.loads(fp.read_text(encoding="utf-8")))
                continue
            except (OSError, json.JSONDecodeError):
                pass
        notes.append(legacy.get(c.author_year(), {}))
    return notes


# ── revision synthesis ────────────────────────────────────────────────────────

_REVISE_PROMPT = """\
Review topic: {topic}
Focus: {focus}

CURRENT NARRATIVE (the draft you are revising):
{narrative}

EVIDENCE DIGEST (ground truth — stay grounded in these sources):
{digest}

REVIEWER ANNOTATIONS (the ONLY changes to make):
{revision_context}

Produce the revised narrative by making the SMALLEST set of edits that fully \
addresses every annotation. This is an iterative revision, not a rewrite: the \
reviewer must be able to read your output against the current draft and see their \
comments — and nothing else — addressed.

Rules:
- Change ONLY what an annotation requires. Leave every other sentence, paragraph, \
section, heading, ordering, and citation exactly as it stands in the current \
narrative — word for word.
- Do NOT restructure, reorder, merge, split, or re-theme sections the reviewer did \
not flag. Do NOT swap in new sources, drop existing ones, or rewrite prose for style \
where no annotation calls for it.
- Where an annotation does require a change, make it precisely and locally, keeping \
the surrounding text intact.
- Maintain [@citekey] citation format throughout. Do not add a bibliography — that is \
generated separately.

Output only the revised narrative."""


# ── revision audit (replaces the fresh-synthesis peer-review critique) ─────────
# A revise must respond ONLY to the reviewer's annotations and leave everything else
# verbatim, so the reviewer can iterate against a stable draft. The fresh-synthesis
# critique loop is wrong here: its peer-review pass judges the draft against generic
# quality ideals and re-themes/merges/drops unflagged content. Instead we run an audit
# that holds the revision to the annotations themselves — every comment addressed, and
# nothing changed that no comment asked for — with the previous draft as the baseline.

_AUDIT_SYS = """\
You are a revision auditor. A literature-review draft has been revised in response to a
set of reviewer annotations. Your ONLY job is to check that the revision (a) fully and
genuinely addresses every annotation, and (b) changed nothing the annotations did not
call for. Judge against the annotations, not your own taste. Respond with ONLY a
numbered list of specific, actionable problems, one per line, quoting the text you mean.
If every annotation is adequately addressed and nothing else was altered, respond "OK"."""

_AUDIT_PROMPT = """\
Review topic: {topic}
Focus: {focus}

REVIEWER ANNOTATIONS (what this revision was supposed to do):
{revision_context}

PREVIOUS DRAFT (the baseline — everything not flagged should survive unchanged):
{previous}

REVISED DRAFT (under audit):
{narrative}

Check, against the annotations only:
1. Unaddressed comments — flag any annotation the revised draft does not yet adequately
   satisfy. Quote the annotation and say what is still missing or wrong.
2. Superficial fixes — flag any annotation answered in name only (e.g. a single word
   changed where the comment asked for a reworked claim or added evidence). Quote both
   the annotation and the weak fix.
3. Overreach — flag any substantive change from the previous draft that NO annotation
   called for: a section reordered, merged, split, re-themed or dropped; a source added
   or removed; a passage rewritten for style. Quote the changed text. The reviewer must
   be able to iterate against a stable draft, so unrequested changes are defects here.

Output: numbered list with quoted text; skip checks with no issues. If the revision
fully and only addresses the annotations, respond "OK"."""

_REVISE_FROM_AUDIT_PROMPT = """\
You revised a literature-review draft to address reviewer annotations, and an auditor
found the problems below. Fix every one: address any annotation still outstanding or
only superficially handled, and REVERT any change the auditor flags as unrequested back
to the previous draft's wording. Change nothing else. Maintain [@citekey] citation
format throughout. Do not add a bibliography — that is generated separately.

REVIEWER ANNOTATIONS:
{revision_context}

PREVIOUS DRAFT (the baseline to preserve where no annotation applies):
{previous}

CURRENT REVISED DRAFT:
{narrative}

Auditor's problems to fix:
{critique}

Output only the corrected narrative."""


def _audit_revise_loop(brain: Brain, cfg, previous: str, narrative: str,
                       revision_context: str, digest: str,
                       rounds: int | None = None) -> str:
    """Iterate audit→fix until the revision addresses every comment and only those.

    Each round audits the current draft against the reviewer annotations (with the
    previous draft as baseline) and, if anything is outstanding or overreaching, applies
    a focused fix. Stops early when the audit returns "OK", or after `rounds` rounds
    (default: brain.cfg.critique_rounds). Ends on the citation-coverage backstop."""
    if rounds is None:
        rounds = max(1, int(getattr(brain.cfg, "critique_rounds", 2)))

    for r in range(1, rounds + 1):
        tag = f" (round {r}/{rounds})" if rounds > 1 else ""
        print(f"  {runlog.stamp()}Auditing revision — comments addressed?{tag}...",
              flush=True)
        try:
            audit = brain.coordinator(
                _AUDIT_PROMPT.format(
                    topic=cfg.topic, focus=cfg.focus or "",
                    revision_context=revision_context,
                    previous=previous.strip(), narrative=narrative),
                _AUDIT_SYS, num_ctx=16384)
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] revision audit failed ({e}); skipping.", file=sys.stderr)
            break

        if _is_ok(audit):
            print(f"  {runlog.stamp()}Audit clean — every comment addressed, no overreach.",
                  flush=True)
            break

        print(f"  {runlog.stamp()}Revising to address audit{tag}...", flush=True)
        try:
            narrative = brain.coordinator(
                _REVISE_FROM_AUDIT_PROMPT.format(
                    revision_context=revision_context, previous=previous.strip(),
                    narrative=narrative, critique=audit.strip()),
                SYNTH_SYS, num_ctx=16384)
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] revision fix failed ({e}); keeping current.", file=sys.stderr)
            break

    # Hard backstop: no body paragraph may be citation-free (locate/bibliography
    # depend on it). No-op when every paragraph already cites a source.
    return _enforce_paragraph_citations(brain, narrative, digest)


# ── in-place redline revision (comment-preserving, tracked changes) ────────────
# An alternative to re-synthesising the whole narrative: edit a COPY of the annotated
# docx in place, answering each comment by rewriting only the paragraph it anchors to,
# recording every rewrite as a `rabbitHole`-authored tracked change with the comment
# left in place. The reviewer reads a true redline beside their comments. The deterministic
# docx surgery lives in `redline`; this is just the per-paragraph brain call.

_PARA_REVISE_SYS = """\
You are revising ONE paragraph of a scholarly literature review to satisfy a reviewer's
comment(s) on it. The paragraph is given to you as NUMBERED SENTENCES. You return only the
sentences you changed, keyed by their number — never the whole paragraph. A sentence you do
not return survives word for word, which is the point: it keeps its citations, its
grounding, and its evidence intact.

Make the SMALLEST change that fully and genuinely addresses every comment. Revise the
sentence(s) the comment bears on. Leave the rest alone.

HOUSE STYLE — organise around ideas not sources; state a claim, then attach its citation
immediately after; never make a citation the grammatical subject; tight, active prose.

CITATIONS — cite ONLY sources in the EVIDENCE list, and ALWAYS as a [@citekey] tag using the
exact key shown there: write "[@smith2021]", NEVER "Smith (2021)" or "(Smith, 2021)". An
author-year citation is invisible to the bibliography and silently unverifies the claim.
Every [@citekey] in a sentence you rewrite must survive in your version unless a comment
asks you to remove that source.

PLACEHOLDERS — a token like ⟦m:1⟧ stands for an equation in the original. Reproduce it
exactly, in the sentence whose claim it supports. Never retype an equation as prose, never
move one to another sentence, and never invent a placeholder of your own.

OUTPUT — a single JSON object mapping sentence number to its replacement text. Use null to
delete a sentence. Return nothing else: no prose, no commentary, no code fence.
  {"2": "The revised second sentence [@smith2021].", "5": null}
If no sentence needs to change, return {}."""

_PARA_REVISE_PROMPT = """\
Review topic: {topic}
Focus: {focus}

PARAGRAPH, as numbered sentences. ▶ marks the sentence(s) the reviewer's comment is
anchored to — those are the ones to revise:
{sentences}

REVIEWER COMMENT(S) on this paragraph (address every one):
{comments}

EVIDENCE you may cite — each line begins with the [@citekey] to cite that source by:
{digest}

Return the JSON object of changed sentences only."""


def _number_sentences(units: list[str], anchored: set[int]) -> str:
    """Render the paragraph as numbered sentences, marking those a comment bears on."""
    return "\n".join(
        f"{'▶' if i in anchored else ' '} {i + 1}. {u.strip()}"
        for i, u in enumerate(units))


def _apply_sentence_edits(units: list[str], edits: dict) -> str:
    """Rebuild the paragraph from the original units plus the reviser's replacements.

    Every sentence the reviser did not return is copied byte-for-byte. This is what makes
    the sentence-level redline true by construction rather than hoped for from a diff: the
    untouched sentences are literally the original objects.
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
        trailing = unit[len(unit.rstrip()):]  # keep the original inter-sentence spacing
        out.append(repl.strip() + trailing)
    return "".join(out)


def _parse_sentence_edits(raw: str, n_units: int) -> tuple[dict, list[str]]:
    """Parse the reviser's JSON, keeping only in-range integer keys. Returns (edits, errors).

    Strict on purpose: `summarize._parse_json_obj` falls back to a read-notes-shaped dict on
    a parse failure, which here would silently look like a well-formed edit of nothing.
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
        return {}, ['Output was not a JSON object. Return only a JSON object mapping '
                    'sentence number to replacement text, e.g. {"2": "…"}.']
    edits, errors = {}, []
    for k, v in obj.items():
        if not str(k).strip().isdigit() or not (1 <= int(k) <= n_units):
            errors.append(f'"{k}" is not a sentence number between 1 and {n_units}.')
            continue
        if v is not None and not isinstance(v, str):
            errors.append(f'The value for sentence {k} must be text or null.')
            continue
        edits[str(int(k))] = v
    return edits, errors


# ── per-paragraph adversary (LINT + AUDIT parity for the redline path) ──────────
# The redline used to accept the reviser's first draft with no scrutiny, so author-year
# citations, dropped [@citekey] tags, uncited paragraphs, and comments that a prose edit
# cannot satisfy all sailed through. This gives the redline the same adversarial bar the
# synthesis/resynth paths have, scoped to a single paragraph: deterministic citation guards
# (mechanical, precise in Python) plus an LLM audit that the edit addresses the comment
# minimally and is satisfiable in place at all.

_PARA_AUDIT_SYS = """\
You audit ONE revised paragraph of a literature review against the reviewer comment(s) it
was meant to satisfy. Mechanical checks — citation format, dropped citations, equations,
which sentences were touched — have already been made in code and passed; do not repeat
them. Judge only what code cannot: MEANING.

Respond with EXACTLY one of three things, nothing else:
- "OK" — the revision fully and genuinely addresses every comment.
- A line "CORPUS: <class>: <brief reason>" — a comment that CANNOT be satisfied by editing
  this paragraph's prose. <class> is exactly one of:
      table     — asks for a table, chart, or figure
      section   — asks for a new section, subsection, or discussion not belonging here
      sources   — asks for evidence or sources not present in the review
  Do not accept a prose gesture as satisfying such a request.
- Otherwise a numbered list of specific problems: a comment not really addressed, or
  addressed in name only. Quote the text you mean."""

_PARA_AUDIT_PROMPT = """\
Review topic: {topic}
Focus: {focus}

REVIEWER COMMENT(S) the revision must satisfy:
{comments}

ORIGINAL PARAGRAPH:
{paragraph}

REVISED PARAGRAPH (under audit):
{revised}

Judge only against the comment(s): is each fully and genuinely addressed, and is the comment
even satisfiable by editing this paragraph's prose at all? Respond "OK", or
"CORPUS: <class>: <reason>", or a numbered list."""

# The reason class the audit returns, mapped to what the reviewer is told. Answering a
# request for a table with "this needs sources not yet in the corpus" is a false diagnosis:
# gathering papers will never satisfy it. The class is what makes the reply honest.
_CORPUS_CLASSES = ("table", "section", "sources")


def _corpus_class(verdict: str) -> str:
    """Extract the reason class from a "CORPUS: <class>: <reason>" verdict."""
    rest = verdict.split(":", 1)[1] if ":" in verdict else ""
    head = rest.strip().split(":", 1)[0].strip().lower()
    return head if head in _CORPUS_CLASSES else "sources"


def _para_guard_findings(old_text: str, new_text: str, touched: set[int],
                         anchored: set[int], n_units: int) -> list[guards.Finding]:
    """Everything about a paragraph rewrite that Python can decide precisely.

    Note what is NOT here: whether the edit means what the comment asked for. That is the
    audit's only remaining job — everything else moved into code, where it is exact.
    """
    return (guards.author_year_prose(new_text)
            + ([] if guards.CITE_TAG_RE.search(new_text) else [guards.Finding(
                "uncited", "paragraph",
                "The paragraph now cites no source — every paragraph must carry at least "
                "one [@citekey] from the EVIDENCE list.")])
            + guards.dropped_citekeys(old_text, new_text)
            + guards.dropped_sentinels(old_text, new_text)
            + guards.invented_sentinels(old_text, new_text)
            + guards.minimal_edit_violation(touched, anchored, n_units))


# A comment names a source either bracketed ("[@doblinger2019]") or bare
# ("add info from @doblinger2019"). The bare form is what reviewers actually write, and
# guards.all_citekeys only matches the bracketed form — so this catches both. A bare token is
# required to look like a citekey (it must carry a 4-digit year) so an @-mention or an email
# address is not mistaken for a source and wrongly routed.
_COMMENT_KEY_RE = re.compile(
    r"\[@([^\]\s]+)\]"                                   # bracketed: any key
    r"|(?<![\w@])@([A-Za-z0-9][\w:.\-]*\d{4}[a-z]?)")    # bare: must carry a year


def _named_citekeys(comments: list[str]) -> set[str]:
    """The [@citekeys] a reviewer names in their comments — the keys triage tries to make
    citeable before the reviser is ever asked to cite them."""
    keys: set[str] = set()
    for c in comments:
        for m in _COMMENT_KEY_RE.finditer(c):
            keys.add(m.group(1) or m.group(2))
    return keys


def _cite_targets(comments: list[str], corpus: list[Candidate],
                  citekeys: dict[int, str]) -> list[str]:
    """Corpus citekeys a reviewer NAMES in a comment — by explicit [@key] or by author-year
    ("Doblinger 2019").

    This is what turns "I've added Doblinger 2019 and Howell 2017 to Zotero, cite them", left on
    a section heading, into the set of already-collected sources to cite in that section. Only
    sources actually in the corpus are returned: the reviser can cite only what `build` embedded,
    so a still-missing paper falls through to the sources route rather than being fabricated. The
    author-year match needs BOTH the first-author surname and the year (when the corpus item has
    one), so a bare surname or a stray year cannot pull in the wrong paper.
    """
    text = " ".join(comments)
    named = _named_citekeys(comments)
    out: list[str] = []
    for i, c in enumerate(corpus):
        ck = citekeys.get(i)
        if not ck or ck in out:
            continue
        if ck in named:
            out.append(ck)
            continue
        surname = (c.first_author_last or "").strip()
        if surname and re.search(rf"\b{re.escape(surname)}\b", text, re.IGNORECASE) \
                and (c.year is None or re.search(rf"\b{c.year}\b", text)):
            out.append(ck)
    return out


class RabbitHolePolicy:
    """rabbitHole's dialect for the shared redline engine: evidence is the corpus digest, the
    prompts are the litreview house style, the guards are rabbitHole's set (every paragraph
    must cite), and the audit routes with the verb ``CORPUS`` rather than ``ROUTE``.

    Constructed per paragraph, because the digest is scoped to the paragraph's own citekeys.

    ``resolve_named_source`` is a CORPUS-PROMOTE: a source the reviewer names that is in the
    corpus but not in this paragraph's digest (the digest is budget-capped, so a large corpus
    can crowd a source out) is surfaced so the reviser can cite it. A named key that is in
    NEITHER routes as a missing source — honestly, with an instruction to add it to the project
    Zotero collection — instead of the old misleading "couldn't without dropping a citation".
    It reads only the local corpus; it never touches the wider Zotero library.
    """

    author = "rabbitHole"
    route_verb = "CORPUS"
    # sources-first: the engine falls back to route_classes[0] for an unrecognised audit
    # class, and rabbitHole's safe default is "sources" (matching the original _corpus_class).
    route_classes = ("sources", "table", "section")

    def __init__(self, cfg, digest: str, source_lines: dict[str, str] | None = None):
        self._topic = cfg.topic
        self._focus = cfg.focus or ""
        self._digest = digest
        # citekey -> the evidence line for every source in the corpus (full line where the
        # corpus has one, else compact). What a promote draws from.
        self._source_lines = source_lines or {}

    def evidence_for(self, ctx: ParaContext) -> Evidence:
        # known = the keys already citeable in THIS paragraph's digest. Triage only tries to
        # promote a named key that is not already here.
        return Evidence(known=set(guards.all_citekeys(self._digest)), context=self._digest)

    def resolve_named_source(self, citekey: str):
        line = self._source_lines.get(citekey)
        return EvidenceLine(citekey=citekey, text=line) if line else None

    def revise_system(self) -> str:
        return _PARA_REVISE_SYS

    def audit_system(self) -> str:
        return _PARA_AUDIT_SYS

    def revise_user(self, ctx: ParaContext, evidence: Evidence,
                    numbered_sentences: str, comment_block: str) -> str:
        return _PARA_REVISE_PROMPT.format(
            topic=self._topic, focus=self._focus,
            sentences=numbered_sentences, comments=comment_block, digest=evidence.context)

    def audit_user(self, ctx: ParaContext, revised: str, comment_block: str) -> str:
        return _PARA_AUDIT_PROMPT.format(
            topic=self._topic, focus=self._focus,
            comments=comment_block, paragraph=ctx.text, revised=revised)

    def guard_findings(self, old_text: str, new_text: str, touched: set[int],
                       ctx: ParaContext, evidence: Evidence) -> list:
        n_units = len(guards.sentence_units(ctx.text))
        return _para_guard_findings(old_text, new_text, touched, ctx.anchored, n_units)

    def soft_finding_kinds(self) -> frozenset[str]:
        """The reviewer's comment is an explicit ask: honor it. A rewrite that answers the
        comment but cannot keep a citation or an equation is HELD as a fallback, not vetoed —
        if no clean rewrite emerges, rabbitHole lands it and flags the sacrifice rather than
        replying "I couldn't without dropping a citation." Fabrication is never soft: an
        INVENTED equation, author-year prose, over-reach, or a paragraph emptied of every
        citation stay hard blockers, exactly as before."""
        return frozenset({"dropped-citekey", "dropped-equation"})


def _redline_para_adversary(brain: Brain, cfg, paragraph: str, comments: list[str],
                            digest: str, anchored: set[int] | None = None,
                            rounds: int | None = None,
                            source_lines: dict[str, str] | None = None,
                            ) -> tuple[str | None, str]:
    """Rewrite one commented paragraph, through the shared engine, in rabbitHole's dialect.

    The loop, the primals, the triage stage, and the fail-closed contract now live in
    ``haarpi.redline_engine``; this is the thin adapter that keeps rabbitHole's call signature
    and outcome vocabulary (``edited`` / ``corpus:<class>`` / ``skipped``) so the orchestration
    and the adversary tests are unchanged.

    ``source_lines`` (citekey -> evidence line for every corpus source) lets triage promote a
    named source out of the corpus and into this paragraph's evidence before the reviser runs.
    """
    if rounds is None:
        rounds = max(1, int(getattr(brain.cfg, "critique_rounds", 2)))
    policy = RabbitHolePolicy(cfg, digest, source_lines)
    ctx = ParaContext(heading="", text=paragraph, comments=list(comments),
                      anchored=set(anchored or ()), named_keys=_named_citekeys(comments),
                      kind="prose")
    new_text, disposition, _copyedits = _engine.redline_paragraph(
        brain, ctx, policy, rounds=rounds)
    if disposition == _engine.Disposition.OVERRIDDEN.value:
        # The edit lands, but it sacrificed a soft-protected atom. Derive the note from
        # old-vs-new text (never the model) so the reply names exactly what was dropped.
        return new_text, f"override:{_override_note(paragraph, new_text)}"
    cls = route_class_of(disposition)
    outcome = f"corpus:{cls}" if cls else disposition   # engine "routed:x" → rabbitHole "corpus:x"
    return new_text, outcome


def _override_note(old_text: str, new_text: str) -> str:
    """A short, honest phrase naming what an override sacrificed, computed from the two texts
    so it can never overstate: the citekeys and equation placeholders present before but not
    after. Reads as the tail of "Doing so {note}"."""
    dropped_keys = sorted(set(guards.all_citekeys(old_text)) - set(guards.all_citekeys(new_text)))
    dropped_eqs = [s for s in guards.sentinels(old_text) if s not in set(guards.sentinels(new_text))]
    parts: list[str] = []
    if dropped_keys:
        parts.append(("dropped the citations " if len(dropped_keys) > 1 else "dropped the citation ")
                     + ", ".join(f"[@{k}]" for k in dropped_keys))
    if dropped_eqs:
        parts.append(("dropped the equations " if len(dropped_eqs) > 1 else "dropped the equation ")
                     + ", ".join(dropped_eqs))
    return " and ".join(parts) if parts else "changed grounded content"


def _is_section_ask(text: str) -> bool:
    """True when a comment asks for a NEW section rather than an edit to this one.

    The same test the planner decomposes with, so what `haarpi next` calls a `section` task and
    what the redline loop grafts can never drift apart.
    """
    try:
        from haarpi.planner import _SECTION_ASK
    except ImportError:
        return False
    return bool(_SECTION_ASK.search(text or ""))


def _sections_from_docx(docx: Path) -> tuple[list[Section], list[int]]:
    """The review's existing sections, read straight from the reviewed .docx.

    Recovered from the document rather than from the markdown of the draft it was made from:
    the reviewer's copy has no markdown of its own, and walking the initials chain back to find
    one is a fragile step that fails outright once a cycle is archived. The heading and the
    section's prose are all the planner and the placement embedding need.

    Returns ``(sections, heading_paragraph_indices)``, the two aligned by index.
    """
    from docx import Document
    from .docxio import is_section_heading
    doc = Document(str(docx))
    out: list[Section] = []
    heads: list[int] = []
    cur: Section | None = None
    for idx, par in enumerate(doc.paragraphs):
        style = par.style.name if par.style is not None else ""
        text = par.text.strip()
        if is_section_heading(style):
            low = text.lower()
            if low.startswith("annotated bibliography"):
                break                      # the bibliography is not a section of the narrative
            if not text or low.startswith(("narrative review", "most load-bearing sources")):
                cur = None                 # wrappers and the coverage block are not sections
                continue
            cur = Section(heading=text, claim="", text="")
            out.append(cur)
            heads.append(idx)
        elif cur is not None and text:
            cur.text = f"{cur.text}\n\n{text}" if cur.text else text
    return out, heads


def _pending_corrections(paths) -> list[dict]:
    """The term corrections the planner recorded for the latest litreview cycle.

    Read from the plan ledger rather than re-derived here: `haarpi next` already decided what is
    wrong and what is right, and a reviser that made its own call could disagree with the
    substitution already applied to the brief and the config — which is exactly the split that
    let a wrong term live in two places at once.
    """
    try:
        from haarpi import project as _hproject
    except ImportError:
        return []
    root = paths.root.parent          # paths.root is the litReview dir
    try:
        plans = _hproject.list_plans(root)
    except Exception:  # noqa: BLE001
        return []
    for plan in reversed(plans):
        if (plan or {}).get("stage") != "litreview":
            continue
        if plan.get("type") not in (None, "plan"):
            continue
        return [c for c in (plan.get("corrections") or [])
                if c.get("wrong") and c.get("right")]
    return []


def _planned_sections(paths) -> dict[str, list]:
    """The sections `haarpi next` planned for this cycle, grouped by the ask that produced each.

    A LIST per ask, because one comment routinely plans more than one section and this used to
    be ``{s["ask"]: s}`` — a dict comprehension keyed by the ask, so the second section for a
    comment silently overwrote the first. elephantRoom cycle 8 planned five sections from three
    comments; two were destroyed by that collision before anything read them. The reviewer asked
    for household-level distributional impacts, which was planned as "Household heterogeneity in
    ABM", and got "Distributional equity of CBAM" — a section about trade between countries —
    because it happened to come second in the plan.

    Read from the plan ledger for the same reason the corrections are: the planner already
    decided what each ask becomes, and it decided BEFORE the gather so the search could be aimed
    at the section. A reviser that re-planned here would draft a section the corpus was never
    filled for — which is how a request about supply-chain dependency came back as a section
    about decoupling growth from emissions, written from the papers that happened to rank
    nearest to the reviewer's own wording.
    """
    try:
        from haarpi import project as _hproject
    except ImportError:
        return {}
    try:
        plans = _hproject.list_plans(paths.root.parent)
    except Exception:  # noqa: BLE001
        return {}
    for plan in reversed(plans):
        if (plan or {}).get("stage") != "litreview" or plan.get("type") not in (None, "plan"):
            continue
        out: dict[str, list] = {}
        for sec in (plan.get("sections") or []):
            if sec.get("ask") and sec.get("heading"):
                out.setdefault(sec["ask"], []).append(sec)
        return out
    return {}


def _graft_edits(brain: Brain, cfg, docx: Path, section_asks: list, corpus,
                 compact, full, citekeys: dict[int, str],
                 outcomes: dict[str, str], grafted_at: dict[int, str],
                 paths=None) -> list[dict]:
    """Draft the section(s) the reviewer asked for and return them as ``insert_section`` edits.

    Placement, in priority order — unchanged from the standalone verb, because the reasoning is
    unchanged: where the reviewer wrote is a statement about where the ask belongs.

      1. the paragraph the requesting comment sits on;
      2. the nearest existing section by embedding, when the ask carried no usable anchor;
      3. the end of the review — reported, never silent.
    """
    from . import graft as _graft
    existing, head_paras = _sections_from_docx(docx)
    planned = _planned_sections(paths) if paths is not None else {}
    out: list[dict] = []
    # One ask at a time. The whole set used to go in together and come back as a flat list
    # matched to asks BY POSITION, so two sections drafted for one fused ask credited the
    # first to every comment and left the second belonging to nobody: three identical replies
    # naming a section, and a second section the reviewer was never told existed.
    for anchor, texts in section_asks:
        ask = " ".join(texts)
        ids = [str(i) for i in anchor["ids"]]
        pre = planned.get(ask) or []
        seed = [_graft.Section(heading=p["heading"], claim=p["claim"], ask=ask) for p in pre]
        try:
            # max_new follows the PLAN. It was pinned at 1, so even without the dict collision
            # above only the first of a comment's sections could ever have been drafted.
            new = _graft.draft_sections(brain, cfg, existing, [ask], compact, full,
                                        set(citekeys.values()), corpus_size=len(corpus),
                                        max_new=max(1, len(seed)), tag="revise/graft",
                                        planned=seed or None)
        except Exception as e:  # noqa: BLE001 — a failed graft must not lose the edits beside it
            print(f"  [warn] could not draft the section for {ask[:60]!r} ({e})",
                  file=sys.stderr)
            new = []
        if not new:
            # The planner found nothing to add for this ask: the review already covers it.
            outcomes.update({i: "section_covered" for i in ids})
            continue
        # EVERY section this ask planned, not just the first. A comment that planned two and
        # received one used to report the one it received and stay silent about the other.
        drafted, declined, empty = [], [], []
        at_para = anchor["para"]
        for sec in new:
            if sec.unsupported:
                # Distinct from "already covered", and the distinction is the whole point: one
                # says the review has this ground, the other says the corpus does not. They call
                # for opposite next moves, and the reviewer is the one who decides which.
                declined.append({"heading": sec.heading, "missing": sec.unsupported,
                                 "claim": sec.claim, "ask": ask})
                continue
            paras = [t for t in sec.text.split("\n\n") if t.strip()]
            if not paras:
                empty.append(sec.heading)
                continue
            out.append({"para": at_para, "op": "insert_section",
                        "heading": sec.heading, "paras": paras})
            grafted_at[at_para] = sec.heading
            drafted.append(sec.heading)
        if len(seed) > len(new):
            # A planned section that came back as nothing at all. Say so — the whole failure
            # this replaces was two sections disappearing with no line anywhere naming them.
            lost = [p["heading"] for p in pre][len(new):]
            print(f"  [warn] {len(lost)} planned section(s) were not returned by the drafter: "
                  f"{', '.join(repr(h) for h in lost)}", file=sys.stderr)
            empty.extend(lost)
        if not drafted and not declined:
            outcomes.update({i: "section_covered" for i in ids})
            continue
        payload = json.dumps({"grafted": drafted, "unsupported": declined, "covered": empty})
        outcomes.update({i: f"sections:{payload}" for i in ids})
        existing.append(sec)     # the next ask cannot re-plan the section just written
        print(f"  {runlog.stamp()}Grafting {sec.heading!r} after the section at para "
              f"{at_para} — the comment's own anchor", flush=True)
    return out


def _redline_revise(brain: Brain, cfg, paths, docx: Path,
                    corpus: list[Candidate], notes: list[dict],
                    citekeys: dict[int, str]) -> tuple[Path, dict]:
    """Answer each anchored comment with an in-place, tracked-change paragraph rewrite.

    Returns (output_docx_path, summary). The brain is called once per commented
    paragraph; everything else (comment preservation, redline XML) is deterministic.
    """
    from . import redline
    anchors = redline.comment_anchors(docx)
    cmap = redline.comments_by_id(docx)
    compact = _compact_lines(corpus, notes, citekeys)
    full = _full_lines(corpus, notes, citekeys)
    # Every corpus source's best evidence line, keyed by citekey — full where the corpus has
    # one, else compact. Triage promotes a named source out of this into a paragraph's digest.
    source_lines = {**compact, **full}

    edits: list[dict] = []
    skipped: list[str] = []
    outcomes: dict[str, str] = {}   # comment id -> "edited" | "grafted" | "corpus" | "skipped"

    # A section ask is answered in THIS pass, at the comment that asked for it. It used to route
    # the whole annotation set to a separate whole-document verb, so the edits alongside it were
    # silently dropped — rework was scaled to the heaviest ask in the set rather than to each ask.
    # Nothing is written until `apply_edits`, so a graft never moves the paragraph another
    # comment is anchored to.
    # ONE ASK PER COMMENT, not per anchor paragraph. Grouping by anchor fused every section
    # request that happened to sit on the same heading into a single ask: three separate
    # requests on elephantRoom's p14 became one ask, one drafted section credited to all three
    # comments, and a second section that no comment was ever told about. A reviewer who leaves
    # three notes in one place has made three requests.
    section_asks = [({**a, "ids": [i]}, [cmap[i]["text"]])
                    for a in anchors for i in a["ids"]
                    if i in cmap and cmap[i]["text"] and _is_section_ask(cmap[i]["text"])]
    grafted_at: dict[int, str] = {}      # para index -> heading, for the log
    if section_asks:
        edits += _graft_edits(brain, cfg, docx, section_asks, corpus, compact, full,
                              citekeys, outcomes, grafted_at, paths)
    handled = {a["para"] for a, _ in section_asks}

    for a in anchors:
        ids = [str(i) for i in a["ids"]]
        comments = [cmap[i]["text"] for i in a["ids"] if i in cmap and cmap[i]["text"]]
        if a["para"] in handled:
            # A paragraph can carry a section ask AND an ordinary comment. The graft answered the
            # section ask and recorded its outcome; anything else on the paragraph still owes its
            # reviewer an answer, so ONLY the section-ask ids drop out here. Skipping the whole
            # paragraph would silently lose the comment beside the ask — the failure this
            # redesign exists to close, reintroduced one level down.
            ids = [str(i) for i in a["ids"]
                   if not _is_section_ask((cmap.get(i) or {}).get("text", ""))]
            comments = [t for t in comments if not _is_section_ask(t)]
            if not ids or not comments:
                continue
        if not comments or not a["text"].strip():
            skipped.append(f"para {a['para']} (no text or no comment body)")
            outcomes.update({i: "skipped" for i in ids})
            continue
        # A comment on a heading is not a prose edit — the heading itself must not be rewritten.
        # But a heading comment that NAMES sources already in the corpus ("I've added X and Y,
        # cite them") means "cite them IN THIS SECTION": answer it by citing those sources in the
        # section's first body paragraph. A heading comment that names nothing citeable is a
        # "find more" ask, and routes to the corpus chain as before.
        if redline.is_heading_style(a.get("style", "")):
            targets = _cite_targets(comments, corpus, citekeys)
            body = redline.first_body_paragraph_under(docx, a["para"]) if targets else None
            if targets and body:
                key_tags = ", ".join(f"[@{k}]" for k in targets)
                ask = (f'The reviewer left this note on the section heading above: '
                       f'"{" ".join(comments)[:300]}". Those sources are in the corpus and they '
                       f'want them cited IN THIS SECTION. Cite {key_tags} in this paragraph where '
                       f'they best support the text, keeping every existing citation and the '
                       f"paragraph's meaning intact.")
                print(f"  {runlog.stamp()}Para {a['para']} is a heading; citing {key_tags} "
                      f"in the section body (para {body['para']})...", flush=True)
                digest = _para_digest(compact, full, guards.all_citekeys(body["text"]))
                new_text, outcome = _redline_para_adversary(
                    brain, cfg, body["text"], [ask], digest, anchored=set(),
                    source_lines=source_lines)
                if new_text and outcome == "edited":
                    edits.append({"para": body["para"], "op": "replace", "text": new_text})
                    outcomes.update({i: f"cited_section:{body['para']}" for i in ids})
                    continue
            skipped.append(f"para {a['para']} (comment on heading "
                           f"'{a['text'][:48]}' — needs ingest/gather routing, "
                           f"not a prose rewrite)")
            outcomes.update({i: "corpus:sources" for i in ids})
            continue
        anchored = set(a.get("anchored") or ())
        print(f"  {runlog.stamp()}Revising para {a['para']} for "
              f"{len(comments)} comment(s)"
              f"{f' (anchored to sentence {sorted(i + 1 for i in anchored)})' if anchored else ''}"
              f"...", flush=True)
        digest = _para_digest(compact, full, guards.all_citekeys(a["text"]))
        new_text, outcome = _redline_para_adversary(
            brain, cfg, a["text"], comments, digest, anchored=anchored,
            source_lines=source_lines)
        if outcome == "edited" and new_text:
            edits.append({"para": a["para"], "op": "replace", "text": new_text})
            outcomes.update({i: "edited" for i in ids})
        elif outcome.startswith("override") and new_text:
            # No clean rewrite was possible, but the comment was explicit — honor it. The edit
            # lands as a tracked change; the reply flags what it sacrificed so the human keeps
            # the accept/reject call with the trade-off in front of them.
            edits.append({"para": a["para"], "op": "replace", "text": new_text})
            outcomes.update({i: outcome for i in ids})
            print(f"  {runlog.stamp()}Para {a['para']}: honored the comment though it "
                  f"{outcome.split(':', 1)[1] if ':' in outcome else 'changed grounded content'}"
                  f" — flagged for review", flush=True)
        elif outcome.startswith("corpus"):
            # The audit found a comment a prose edit can't satisfy (a table, a new section,
            # sources not yet in the review). Don't fabricate a rewrite — route it and reply
            # honestly instead of falsely claiming the paragraph was fixed.
            skipped.append(f"para {a['para']} (comment needs a non-prose change / new "
                           f"sources — routed, not rewritten)")
            outcomes.update({i: outcome for i in ids})
        else:
            skipped.append(f"para {a['para']} (no revision passed the citation / equation / "
                           f"minimal-edit guards — left unchanged)")
            outcomes.update({i: "skipped" for i in ids})

    _, out_docx = _revision_paths(paths, docx)
    summary = redline.apply_edits(docx, out_docx, edits, author="rabbitHole")
    summary["skipped_paras"] = skipped
    summary["comment_outcomes"] = outcomes

    # Regenerate the annotated bibliography against the POST-edit narrative so a
    # newly-cited source still gets a verifiable entry. Read the accepted body text
    # (tracked changes applied) to learn the current cited set, re-locate by citekey,
    # and replace the bibliography section in place.
    try:
        narrative = redline.accepted_body_text(out_docx)
        collection = None
        if _HAVE_CHROMA:
            try:
                from . import chroma as _chroma
                collection = _chroma.get_collection(paths.work / "chroma")
            except Exception as e:  # noqa: BLE001
                print(f"  [warn] ChromaDB unavailable ({e}) — locate will use "
                      f"head-truncation", file=sys.stderr)
        print(f"  {runlog.stamp()}Regenerating annotated bibliography "
              f"(full curated corpus, re-locating claims)...", flush=True)
        located = locate_claims(brain, narrative, corpus, notes, cfg, paths,
                                collection=collection, citekeys=citekeys, scope="all")
        biblio_md = bibliography(corpus, located,
                                 cited_indices=set(_cited_indices(narrative, citekeys)))
        bib_summary = redline.replace_bibliography(out_docx, biblio_md)
        summary.update(bib_summary)
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] bibliography regeneration failed ({e}); "
              f"keeping the carried-over bibliography.", file=sys.stderr)

    # A correction is total, not span-local: the reviewer is saying the project has a fact wrong,
    # so it is wrong in every paragraph, including the ones they left no note on. The planner
    # already fixed the brief, the config and the draft markdown; this is the .docx they hold.
    try:
        corrections = _pending_corrections(paths)
        if corrections:
            print(f"  {runlog.stamp()}Applying {len(corrections)} term correction(s) "
                  f"across the narrative...", flush=True)
            sub = redline.tracked_substitute(out_docx, corrections)
            summary.update({"corrections": sub["substitutions"],
                            "correction_paragraphs": sub["paragraphs"]})
            for term, n in sub["per_term"].items():
                print(f"    {term!r}: {n} substitution(s)"
                      + ("   NOTHING MATCHED" if not n else ""))
    except Exception as e:  # noqa: BLE001 — a correction must not cost the redline
        print(f"  [warn] could not apply the term corrections ({e}).", file=sys.stderr)

    # The load-bearing block is only true of the draft it was computed from, and this pass has
    # just changed which sources carry the argument — a redline adds citations, a graft adds
    # whole sections. The full-render path has always recomputed it; the redline path did not,
    # so a reviewed document kept a ranking several cycles old or, if the block postdated the
    # draft being reviewed, never grew one at all.
    try:
        from .summarize import top_sources_block
        narrative = redline.accepted_body_text(out_docx)
        print(f"  {runlog.stamp()}Re-ranking the load-bearing sources...", flush=True)
        block = top_sources_block(brain, cfg, narrative, corpus, citekeys)
        summary.update(redline.replace_top_sources(out_docx, block))
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] could not refresh the load-bearing sources block ({e}); "
              f"the document keeps the one it had.", file=sys.stderr)

    # The front-matter stats describe THIS draft or they describe nothing. Carried through
    # untouched they described the last full render: a reviewed elephantRoom draft claimed
    # "Sources: 162" directly above a block that had just recounted them and said 184, under a
    # date eight weeks stale and a focus line still naming a model the reviewer had corrected.
    try:
        from datetime import date
        narrative = redline.accepted_body_text(out_docx)
        model = (cfg.brain.claude_model if brain.backend == "claude"
                 else cfg.brain.coordinator_model)
        fields = [("Project:", cfg.project_name),
                  ("Date:", date.today().isoformat()),
                  ("Sources:", str(len(corpus))),
                  ("Generated by:", f"rabbitHole — {model}")]
        if cfg.focus:
            fields.append(("Focus:", cfg.focus))
        line = guards.metrics(narrative, set(citekeys.values())).line()
        if line:
            fields.append(("Foundation:", line))
        summary.update(redline.replace_front_matter(out_docx, fields))
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] could not refresh the front-matter stats ({e}); the document keeps "
              f"the ones it had — treat them as belonging to the previous render.",
              file=sys.stderr)

    return out_docx, summary


def _synthesize_revision(brain: Brain, cfg, corpus: list[Candidate],
                         notes: list[dict], citekeys: dict[int, str],
                         current_narrative: str, revision_context: str,
                         style_profile: str = "") -> str:
    """Re-draft the whole narrative in one call (`revise --resynth`).

    Document-scale by nature: it must hold the previous draft, the new draft, and the evidence
    at once. The compact digest keeps that tractable for a modest corpus, but a large one will
    still overflow the coordinator's context — `brain._check_context` says so, loudly, naming
    this call site. The default redline path has no such limit: it works one paragraph at a
    time. Prefer it.
    """
    digest = "\n".join(_compact_lines(corpus, notes, citekeys).values())
    prompt = _REVISE_PROMPT.format(
        topic=cfg.topic, focus=cfg.focus,
        narrative=current_narrative.strip(),
        digest=digest,
        revision_context=revision_context,
    )
    sys_prompt = SYNTH_SYS
    if style_profile:
        sys_prompt = (sys_prompt.rstrip()
                      + f"\n\nWRITING STYLE\nMatch the following author's voice and "
                        f"prose style throughout:\n{style_profile}")
    print(f"  {runlog.stamp()}Re-synthesising narrative (coordinator)...", flush=True)
    narrative = brain.coordinator(prompt, sys_prompt, num_ctx=16384)
    return _audit_revise_loop(brain, cfg, current_narrative, narrative,
                              revision_context, digest)


# ── route comments that a redline cannot satisfy in place ──────────────────────


def _reply_to_comments(out_docx: Path, outcomes: dict[str, str], routing: dict) -> None:
    """Add a rabbitHole-authored threaded reply to each reviewer comment saying what was
    actually done — an in-place edit, a routed follow-up, or nothing.

    The reply must name the real reason. Telling a reviewer who asked for a table that "this
    needs sources not yet in the corpus" is a false diagnosis: no amount of gathering will
    satisfy it. The audit returns a reason class precisely so this reply can be honest.
    """
    from . import redline
    if not outcomes:
        return
    tier = routing.get("tier")
    queued = routing.get("queued")
    redraft = "report" if routing.get("needs_report") else "revise"
    if queued:
        fetch = f"Queued a {tier} follow-up cycle (gather → … → {redraft}) to bring them in."
    else:
        fetch = "Run `rabbitHole gather` (or `ingest` for a named paper), then revise."

    if queued and routing.get("needs_report"):
        section_msg = ("rabbitHole: this asks for a new section, which an in-place redline "
                       "can't add — it only rewrites the paragraph a comment sits on. Queued "
                       "a follow-up cycle ending in `report`, which re-plans the review's "
                       "sections from the corpus. Your ask is carried across in the project "
                       "focus; `report` does not read this file, so this redline stays as the "
                       "record of the current cycle.")
    else:
        section_msg = ("rabbitHole: this asks for a new section, which an in-place redline "
                       "can't add — it only rewrites the paragraph a comment sits on. Left "
                       "the paragraph as it stands. Run `rabbitHole report` to re-plan the "
                       "review's sections from the corpus; note it starts a new cycle and "
                       "does not read this file.")

    corpus_msg = {
        "sources": ("rabbitHole: a source this comment needs is not in the corpus, which an "
                    "in-place edit can't add. If you named a specific paper, add it to the "
                    "project's Zotero collection and re-run `rabbitHole revise` — the next run "
                    "pulls it into the corpus so the reviser can cite it. Otherwise " + fetch[0].lower() + fetch[1:]),
        "table": ("rabbitHole: this asks for a table, which isn't a prose edit — a redline "
                  "can only rewrite sentences. Left the paragraph as it stands; add the "
                  "table yourself, or ask for the numbers to be restated in the prose."),
        "section": section_msg,
    }
    def _graft_reply(headings: list) -> str:
        if len(headings) == 1:
            what = f"a new section, \u201c{headings[0]}\u201d,"
        else:
            named = ", ".join(f"\u201c{h}\u201d" for h in headings[:-1])
            what = f"{len(headings)} new sections, {named} and \u201c{headings[-1]}\u201d,"
        return (f"rabbitHole: drafted {what} and inserted "
                f"{'it' if len(headings) == 1 else 'them'} as a tracked change at the end of "
                "the section this comment sits in — where you left the note is where the ask "
                "belongs. Every other paragraph is untouched; reject the insertion to drop "
                f"{'it' if len(headings) == 1 else 'them'}, or move "
                f"{'it' if len(headings) == 1 else 'them'} if that reads better elsewhere.")

    def _declined_reply(missing: str) -> str:
        return (f"rabbitHole: did not draft this section — the corpus cannot carry it. It is "
                f"missing {missing}. Nothing was written rather than assembling it from the "
                f"sources that merely ranked nearest, which would have read as a real section. "
                f"A `gather` for exactly this has been recorded, and the next cycle searches "
                f"for it and drafts the section without you restating the ask.")

    replies: dict[str, str] = {}
    for cid, outcome in outcomes.items():
        if outcome.startswith("sections:"):
            # One comment can plan several sections, and they can land differently — two
            # drafted, one declined. The reply says what happened to each rather than
            # reporting whichever the old single-valued outcome happened to hold.
            try:
                payload = json.loads(outcome.split(":", 1)[1])
            except Exception:  # noqa: BLE001
                payload = {}
            parts = []
            if payload.get("grafted"):
                parts.append(_graft_reply(payload["grafted"]))
            for d in payload.get("unsupported") or []:
                parts.append(_declined_reply(d["missing"] if isinstance(d, dict) else d))
            if payload.get("covered") and not parts:
                parts.append("rabbitHole: read this as a request for a new section, but the "
                             "review already covers that ground, so nothing was added.")
            replies[cid] = "\n\n".join(parts) if parts else section_msg
            continue
        if outcome == "edited":
            replies[cid] = ("rabbitHole: revised the paragraph above as a tracked change to "
                            "address this comment.")
        elif outcome.startswith("grafted:"):
            replies[cid] = _graft_reply([outcome.split(":", 1)[1]])
        elif outcome.startswith("unsupported:"):
            replies[cid] = _declined_reply(outcome.split(":", 1)[1])
        elif outcome == "section_covered":
            replies[cid] = ("rabbitHole: read this as a request for a new section, but the "
                            "review already covers that ground in an existing section, so "
                            "nothing was added. If you want it separated out regardless, say "
                            "which existing section it should be split from.")
        elif outcome.startswith("cited_section"):
            replies[cid] = ("rabbitHole: you left this on a section heading and named sources "
                            "already in the corpus, so I cited them in this section's first body "
                            "paragraph as a tracked change (a heading itself can't carry the "
                            "citation). Move it to another paragraph in the section if you'd "
                            "prefer it there.")
        elif outcome.startswith("override"):
            note = outcome.split(":", 1)[1] if ":" in outcome else "changed grounded content"
            replies[cid] = ("rabbitHole: revised the paragraph above as a tracked change to "
                            f"address this comment. Doing so {note} — I could not honor the "
                            "comment while keeping it, so verify this was the intended "
                            "trade-off (reject the change to keep the original).")
        elif outcome.startswith("corpus"):
            cls = outcome.split(":", 1)[1] if ":" in outcome else "sources"
            replies[cid] = corpus_msg.get(cls, corpus_msg["sources"])
        elif outcome == "skipped":
            # Say so. Silence here reads as "the tool ignored me"; worse, an earlier version
            # emitted the edit anyway and claimed success.
            replies[cid] = ("rabbitHole: could not produce a revision that addressed this "
                            "without dropping a citation or an equation from the paragraph, "
                            "so the paragraph is unchanged. Narrow the comment, or revise "
                            "this one by hand.")
    if not replies:
        return
    try:
        # The shared writer (raconteur uses it too): it shadows each parent's exact anchor and
        # sets w15:paraIdParent, so the reply nests as a real thread even when a paragraph
        # carries several comments — and it creates commentsExtended.xml when it is absent.
        n = hredline.add_replies(out_docx, replies, author="rabbitHole", initials="rH")
        print(f"  Replies added: {n} reviewer comment(s) answered (authored rabbitHole).")
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] could not add reply comments ({e}).", file=sys.stderr)


# ── orchestration ─────────────────────────────────────────────────────────────

def _ledger_refresh(cfg, gc, paths, corpus, citekeys, document_text: str,
                    *, verb: str) -> None:
    """Rewrite refs.bib + disposition.json for the document this run produced, and reconcile.

    `report` has always done this; `revise` never did. Both emit documents, so both must —
    otherwise the accountability artifacts describe a run that is no longer the current one.
    Never fatal: a revise that cannot reach Zotero still produced a valid document, and the
    reconciliation it prints is the thing the reviewer needs to see.
    """
    try:
        _bib, _disp, rec = ledger.refresh(cfg, gc, paths, corpus, citekeys,
                                          ledger.narrative_only(document_text), verb=verb)
        ledger.print_reconciliation(rec)
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] could not refresh refs.bib / disposition.json ({e}).",
              file=sys.stderr)


def run(directory: str = ".", brain_override: str | None = None,
        docx_path: str | None = None, redline: bool = True,
        queue: bool = True) -> int:
    # `queue` (and the CLI `--no-queue` flag) is retained for backward compatibility — revise
    # no longer plans follow-ups in either mode; `haarpi next` is the sole planner. Kept so the
    # pipeline's `haarpi rabbithole revise --no-queue` and any already-queued task still parse.
    del queue
    docxio.require_docx()
    t0 = runlog.start()

    cfg = config.load_project(directory)
    gc = config.load_global()
    paths = config.project_paths(directory)
    brain = Brain(cfg.brain, gc, backend_override=brain_override)

    print(f"rabbitHole revise — {cfg.project_name}")

    # 1. Find the docx to revise
    if docx_path:
        docx = Path(docx_path)
    else:
        docx = docxio.find_annotated_docx(paths)
    if not docx or not docx.exists():
        print("[error] No *_ra.docx found in output/. "
              "Specify one with --file or run 'rabbitHole report' first.", file=sys.stderr)
        return 1
    print(f"  Annotated file: {docx.name}")

    # 2. Extract annotations
    revision_context = docxio.build_revision_context(docx)
    if not revision_context:
        print("[warn] No tracked changes or comments found in the docx. Nothing to revise.")
        return 0
    n_comments = len(docxio.read_comments(docx))
    tc = docxio.read_track_changes(docx)
    print(f"  Found: {n_comments} comment(s), "
          f"{len(tc['deletions'])} deletion(s), {len(tc['insertions'])} insertion(s).")

    # 3. Read current narrative from the matching .md (same stem, without user initials)
    #    e.g. digipros_litreview_ra_DCR.docx → look for digipros_litreview_ra.md
    ra_stem = re.sub(r"_[^_]+$", "_ra", docx.stem)  # replace last suffix with _ra
    md_path = paths.output / f"{ra_stem}.md"
    if not md_path.exists():
        # Fall back to body text extracted from the docx itself
        current_narrative = docxio.read_body_text(docx)
    else:
        current_narrative = md_path.read_text(encoding="utf-8")

    # 4. Load the corpus + notes exactly as `build` left them. revise does NOT embed new
    #    sources: embedding lives solely in `build`, the one step that changes the corpus, so
    #    revise's learned duration is pure drafting and stays estimable. New Zotero papers
    #    reach the review through a `build` step `haarpi next` queues ahead of revise (an "I've
    #    added … to Zotero, cite them" comment routes the `cite` need — see haarpi.planner
    #    chain_from_tasks). Notes are keyed by citekey, so the cached annotations stay aligned.
    corpus = _load_corpus(paths)
    if not corpus:
        print("[error] work/corpus.json not found — run 'rabbitHole report' first.",
              file=sys.stderr)
        return 1
    if gc.have_zotero and cfg.zotero.get("collection_key"):
        # Cheap staleness check (collection listing + metadata probe — no PDF, no embedding):
        # warn if sources were added to Zotero without a build to embed them, so a stale
        # corpus never masquerades as complete.
        stale = corpus_mod.count_uningested(cfg, gc, corpus)
        if stale:
            print(f"  [warn] {stale} source(s) in the Zotero collection are not in the "
                  f"corpus; revise will not embed them. Run `rabbitHole build` first (or "
                  f"leave an 'I've added these to Zotero, cite them' comment) so they can "
                  f"be cited.", file=sys.stderr)
        # Heal a corpus first built before citekeys were captured: fill any empty citekey
        # from Zotero's Extra so the review cites the user's curated keys. Metadata only.
        filled = corpus_mod.backfill_citekeys(cfg, gc, paths, corpus)
        if filled:
            corpus_mod.persist(paths, corpus)
            print(f"  {runlog.stamp()}Backfilled {filled} Zotero citation key(s) "
                  f"into the cached corpus.", flush=True)
    citekeys = _make_citekeys(corpus)   # before the notes: they are keyed by it
    notes = _load_notes(paths, corpus, citekeys)

    # 4b. Redline mode: edit the annotated docx in place with tracked changes, leaving
    #     comments anchored and un-flagged paragraphs untouched. Skips the full
    #     re-synthesis (steps 5-8) entirely.
    if redline:
        print()
        out_docx, summary = _redline_revise(brain, cfg, paths, docx,
                                            corpus, notes, citekeys)
        print()
        print("=" * 60)
        print(f" revise (redline) complete  [{runlog.fmt_dt(time.time() - t0)}]")
        print("=" * 60)
        print(f"  {summary['replace']} paragraph(s) revised as tracked changes, "
              f"{summary['comments_preserved']} comment(s) preserved.")
        # Say what was GRAFTED. The summary used to report only the paragraph rewrites, so a
        # run that spliced two whole sections into the review announced "1 paragraph(s)
        # revised" and nothing else — the largest thing it did was the one thing it did not
        # mention, and the document read as finished for three days.
        outs = summary.get("comment_outcomes", {}) or {}
        grafted, declined, covered, needs_gather = set(), [], set(), []
        for o in outs.values():
            if o.startswith("sections:"):
                try:
                    payload = json.loads(o.split(":", 1)[1])
                except Exception:  # noqa: BLE001
                    continue
                grafted.update(payload.get("grafted") or [])
                declined.extend(d["missing"] if isinstance(d, dict) else d
                                for d in (payload.get("unsupported") or []))
                needs_gather.extend(d for d in (payload.get("unsupported") or [])
                                    if isinstance(d, dict))
                covered.update(payload.get("covered") or [])
            elif o.startswith("grafted:"):
                grafted.add(o.split(":", 1)[1])
            elif o.startswith("unsupported:"):
                declined.append(o.split(":", 1)[1])
        for h in sorted(grafted):
            print(f"  Section drafted and spliced in: {h}")
        for h in sorted(covered):
            print(f"  Section planned but not returned by the drafter: {h}")
        for d in declined:
            print(f"  Section NOT drafted — corpus cannot carry it: missing {d}")
        if declined:
            # THE DECLINE IS ALREADY A SEARCH BRIEF. It used to be written into a docx reply and
            # left there: the reviewer's comment stayed unresolved, the next gate re-planned the
            # same ask, and `revise` declined it again — a loop with a human as its only exit,
            # holding a query the tool had already written. Recording it hands `haarpi next` the
            # topics; the queueing stays at the gate, where routing belongs.
            recorded = 0
            if paths is not None and needs_gather:
                try:
                    from haarpi import project as _hproject
                    _hproject.record_plan(paths.root.parent, {
                        "type": "needs_gather", "stage": "litreview",
                        "sections": [{"heading": d.get("heading", ""),
                                      "claim": d.get("claim", ""),
                                      "ask": d.get("ask", ""),
                                      "missing": d.get("missing", "")}
                                     for d in needs_gather],
                    })
                    recorded = len(needs_gather)
                except Exception as e:  # noqa: BLE001 — never lose the document over a ledger write
                    print(f"  [warn] could not record the gather for the declined section(s) "
                          f"({e}); the reason is still in the reply.", file=sys.stderr)
            print(f"  [!] {len(declined)} requested section(s) went unwritten — the corpus "
                  f"could not carry them.")
            if recorded:
                print(f"      Recorded {recorded} gather topic(s) for the next cycle. "
                      f"`haarpi next` queues the search and re-drafts these sections; you do "
                      f"not need to restate the ask.")
        if "bib_entries" in summary:
            print(f"  Annotated bibliography regenerated: "
                  f"{summary['bib_entries']} entr(y/ies) re-located.")
        if summary.get("skipped_paras"):
            print(f"  Skipped {len(summary['skipped_paras'])} paragraph(s) "
                  f"(not a prose rewrite):")
            for s in summary["skipped_paras"]:
                print(f"    - {s}")
        if out_docx.exists():
            print(f"  Review (docx): {out_docx}")
        # Comments a redline cannot satisfy in place (new sources, a new section) are planned
        # by `haarpi next` at the gate — not here. revise drafts and replies only; routing
        # stays "not queued" so each reply names the real reason and points at the fix.
        routing = {"queued": False, "tier": None, "needs_report": False}
        # Reply to each reviewer comment, authored "rabbitHole", with what was done —
        # the docx itself becomes the accountability record (no separate ledger).
        _reply_to_comments(out_docx, summary.get("comment_outcomes", {}), routing)
        # A redline emits a document: it regenerates the whole annotated bibliography and a
        # graft splices in new sections carrying new citations. It used to refresh neither
        # refs.bib nor the ledger, so both went on describing the previous `report` — which is
        # how a minted review came to cite thirteen keys its own refs.bib never defined.
        # Reconcile against the document actually produced, not against the input narrative.
        _ledger_refresh(cfg, gc, paths, corpus, citekeys,
                        docxio.read_body_text(out_docx), verb="revise (redline)")
        return 0

    # 5. Style profile
    style_profile = ""
    if cfg.use_style:
        from .style import load_style_profile
        style_profile = load_style_profile()

    # 6. Re-synthesise
    print()
    narrative = _synthesize_revision(brain, cfg, corpus, notes, citekeys,
                                     current_narrative, revision_context, style_profile)

    # 7. Bibliography — re-locate each cited claim against the CURRENT full text so
    #    the annotated bibliography stays verifiable: a reviewer must be able to
    #    confirm every citation maps to non-hallucinated source text. The revision
    #    may drop or add sources, so this keys off the new citations. locate_claims
    #    caches per-citekey and only computes what's missing (embedding retrieval).
    print()
    print(f"  {runlog.stamp()}Locating cited claims for the annotated bibliography...")
    collection = None
    if _HAVE_CHROMA:
        try:
            from . import chroma as _chroma
            collection = _chroma.get_collection(paths.work / "chroma")
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] ChromaDB unavailable ({e}) — locate will use "
                  f"head-truncation", file=sys.stderr)
    located = locate_claims(brain, narrative, corpus, notes, cfg, paths,
                            collection=collection, citekeys=citekeys, scope="all")
    biblio = bibliography(corpus, located,
                          cited_indices=set(_cited_indices(narrative, citekeys)))
    unmatched = citation_check(narrative, citekeys)
    if unmatched:
        print(f"\n[citation check] {len(unmatched)} unmatched citekey(s): "
              f"{', '.join(f'[@{k}]' for k in unmatched[:8])}"
              + (" ..." if len(unmatched) > 8 else ""))

    # 8. Write output. The resynth path rebuilds the narrative wholesale, so it reports the
    # same foundation metrics `report` does — but it does NOT run the breadth guards: a
    # revise answers the reviewer's annotations and nothing else (see guards, scoping rule).
    metrics_line = guards.metrics(narrative, set(citekeys.values())).line()
    print(f"  {runlog.stamp()}[polestar] {metrics_line}")
    _ledger_refresh(cfg, gc, paths, corpus, citekeys, narrative, verb="revise")
    out_md, out_docx = _revision_paths(paths, docx)
    from .render import _paged_reference, build_markdown, pandoc_convert
    from .summarize import top_sources_block
    # Re-ranked, not carried over: a revise changes which paragraphs carry the argument, so the
    # load-bearing list is only true of the draft it was computed from.
    md_text = build_markdown(cfg, brain.backend, narrative, biblio, corpus, unmatched,
                             metrics_line,
                             top_sources_block(brain, cfg, narrative, corpus, citekeys))
    out_md.write_text(md_text, encoding="utf-8")
    # No citeproc: the [@citekeys] stay as written, and the annotated bibliography that
    # names each one is already in the document. See render.write_review.
    pandoc_convert(out_md, out_docx, reference_doc=_paged_reference(paths))

    print()
    print("=" * 60)
    print(f" revise complete  [{runlog.fmt_dt(time.time() - t0)}]")
    print("=" * 60)
    print(f"  Review (md)  : {out_md}")
    if out_docx.exists():
        print(f"  Review (docx): {out_docx}")
    return 0

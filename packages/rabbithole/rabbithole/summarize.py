"""report — read the corpus, synthesize the review, then locate the cited claims.

Pipeline (annotation is a POST-synthesis product, not an independent per-paper step):
  1. read (MAP)    : per-paper notes for synthesis. Sequential, written to disk one
                     paper at a time -> a slow run is resumable and shows progress
                     (work/annotations/NNN.json).
  2. synthesize    : the coordinator (27B) writes the thematic narrative with
                     [@citekey] pandoc citations, from a digest of the notes.
  3. locate (POST) : for each source the narrative actually CITES, find where in
                     that paper the review's claims live (section/page + quote). That
                     set of located claims IS the annotated bibliography. Written
                     per-paper to work/located/NNN.json (resumable).

The corpus is the FOUNDATION the review rests on, not a menu to select from. Synthesis
therefore drives every curated source to a decision — cited, or rejected with a reason —
and records the ledger in work/disposition.json. Breadth, density, and verifiability are
enforced by `guards`, deterministically; the LLM is asked only what code cannot decide
(whether a source earns a place, whether an edit means what a comment asked for).
"""

from __future__ import annotations

import json
import math
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import config, corpus as corpus_mod, guards, render, runlog
from .brain import Brain
from .models import Candidate, norm_doi
from .pdfs import page_marked_text


def _make_citekeys(corpus: list[Candidate]) -> dict[int, str]:
    """Map corpus index → citation key.

    Prefer the source's own Better BibTeX key (parsed from Zotero's Extra field),
    so the review cites with the user's curated keys and the exported refs.bib
    matches. Items with no Zotero key — manually-added folder PDFs, or the rare
    unpinned item — fall back to a generated {last}{year} key, disambiguated
    against every key already in use so it never collides with a real one.
    """
    def _base(c: Candidate) -> str:
        last = re.sub(r"[^a-z0-9]", "", c.first_author_last.lower())
        return f"{last}{c.year}" if c.year else f"{last}nd"

    keys: dict[int, str] = {}
    used: set[str] = {c.citekey for c in corpus if c.citekey}

    # 1) Honour Zotero/Better BibTeX keys verbatim.
    needs_gen: list[int] = []
    for i, c in enumerate(corpus):
        if c.citekey:
            keys[i] = c.citekey
        else:
            needs_gen.append(i)

    # 2) Generate for the rest, suffixing a/b/c… past any collision.
    if needs_gen:
        labels = [f"{c.first_author_last} {c.year or ''}".strip()
                  for c in (corpus[i] for i in needs_gen)]
        print(f"  [note] {len(needs_gen)} source(s) have no Zotero citation key; "
              f"generating one: {', '.join(labels[:5])}"
              + (" …" if len(labels) > 5 else ""), file=sys.stderr)
        for i in needs_gen:
            base = _base(corpus[i])
            key = base
            j = 0
            while key in used:
                key = base + "abcdefghijklmnopqrstuvwxyz"[j % 26]
                j += 1
            used.add(key)
            keys[i] = key

    # Two Zotero items can carry the same Better BibTeX key (a dedup miss upstream: same
    # paper, different titles, one without a DOI). Step 1 honours both verbatim, and then the
    # key→index inversion silently keeps whichever comes last — so the bibliography can print
    # the poorer record, with no year and no link, for a source the narrative cites.
    for f in guards.duplicate_citekeys(keys):
        print(f"  [warn] {f.imperative}", file=sys.stderr)
    return keys

try:
    from . import chroma as _chroma
    _HAVE_CHROMA = True
except ImportError:
    _HAVE_CHROMA = False

_MAP_CHUNK_CHARS = 14000
_DIRECT_CHARS = 20000
_LOCATE_FALLBACK_CHARS = 24000  # used only when ChromaDB is unavailable
_LOCATE_TOP_K = 4               # chunks to retrieve per claim via ChromaDB


# Shared run clock (rabbithole.runlog): run() calls runlog.start(); the deep
# synthesis/locate helpers — some reused by `revise` — stamp progress lines via
# _stamp() without threading a start time through every signature.
_stamp = runlog.stamp
_fmt_dt = runlog.fmt_dt


def _chunk(text: str, size: int) -> list[str]:
    return [text[i:i + size] for i in range(0, len(text), size)]


def _paper_text(c: Candidate) -> str:
    if c.pdf_path and Path(c.pdf_path).exists():
        t = page_marked_text(Path(c.pdf_path))
        if t:
            return t
    return c.fulltext


def _parse_json_obj(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            v = json.loads(m.group(0))
            if isinstance(v, dict):
                return v
        except json.JSONDecodeError:
            pass
    return {"argument": raw.strip()[:500], "methods": "", "findings": "",
            "limitations": "", "relevance": "", "themes": []}


def _parse_json_list(raw: str) -> list:
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if m:
        try:
            v = json.loads(m.group(0))
            if isinstance(v, list):
                return v
        except json.JSONDecodeError:
            pass
    return []


# ──────────────────────────────────────────────────────────────────────────
# 1. read (MAP) — per-paper notes for synthesis, written incrementally
# ──────────────────────────────────────────────────────────────────────────
READ_SYS = """\
You are summarizing a paper so it can be used in a literature review. Read the
paper text and respond with ONLY a JSON object, no other text:

{
  "argument": "the paper's main argument / contribution (1-2 sentences)",
  "methods": "methods or approach (1 sentence)",
  "findings": "key findings, stated QUANTITATIVELY wherever the paper reports numbers — copy effect sizes, percentages, p-values, odds/risk ratios, correlation coefficients, and sample sizes verbatim, each with its direction and the outcome it refers to (e.g. 'reducing collection-point distance cut miss-sorted packaging by ~30%'). If the paper gives no numbers, say so. 1-3 sentences",
  "limitations": "stated or evident limitations (1 sentence, or empty)",
  "relevance": "why this paper matters for the review — what it specifically contributes or enables (1 sentence)",
  "gaps": "a genuine, load-bearing gap WITHIN the paper's own scope that a reader would expect it to cover but it does not. Leave empty if the only 'gaps' are topics outside the paper's discipline (1 sentence, or empty)",
  "themes": ["3-6 short theme tags"]
}
Base everything strictly on the provided text. Do not invent.
Write each field in plain English; paraphrase rather than copying technical phrases verbatim.
Exception: in "findings", reproduce reported quantities (numbers, units, statistics) EXACTLY — never round them away or replace a figure with words like "significantly" or "substantially"."""


# Bump whenever the extraction logic (READ_SYS, _read_prompt, _condense, the retrieval
# queries) changes in a way that should re-read papers already in the cache. A cached
# note carries its _v; when it is older than this, read_notes re-extracts it so a prompt
# fix actually reaches the existing corpus instead of silently applying only to new papers.
#   v1 (2026-06-29): findings now capture quantitative effect estimates verbatim.
_NOTES_VERSION = 1


def _read_prompt(c: Candidate, topic: str, focus: str, body: str) -> str:
    return (f"Review topic: {topic}\nFocus: {focus}\n\n"
            f"Paper: {c.title} ({c.author_year()})\nVenue: {c.venue}\n\n"
            f"--- PAPER TEXT ---\n{body}\n--- END ---\n\nJSON:")


def _condense(brain: Brain, text: str) -> str:
    """Map step for long papers: summarise chunks, then concatenate."""
    chunks = _chunk(text, _MAP_CHUNK_CHARS)
    sys = ("Extract the key points (argument, methods, findings, notable quotes "
           "with page markers) from this section. Be concise, but reproduce every "
           "reported quantity VERBATIM — effect sizes, percentages, p-values, "
           "odds/risk ratios, correlation coefficients, sample sizes — each with the "
           "outcome it refers to. Do not summarise numbers away into words.")
    jobs = [(sys, f"Section:\n{ch}\n\nKey points:") for ch in chunks]
    parts = brain.worker_map(jobs, num_ctx=8192)
    return "\n\n".join(parts)


def _legacy_notes_by_paper(d: Path) -> dict[str, dict]:
    """Index-keyed notes (``057.json``) from before the cache was keyed by citekey.

    A positional cache is only valid while the corpus keeps its order. De-duplicate one
    Zotero item and every paper after it inherits its neighbour's notes — silently, because
    nothing compared the cached ``_paper`` to the paper at that index. Re-key by the paper
    the note actually describes; an ambiguous label (two papers, same author and year) is
    dropped so it is re-read rather than guessed at.
    """
    found: dict[str, dict] = {}
    ambiguous: set[str] = set()
    for fp in sorted(d.glob("[0-9][0-9][0-9].json")):
        try:
            note = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        paper = note.get("_paper")
        if not paper:
            continue          # unidentifiable: cannot be trusted at any index
        if paper in found:
            ambiguous.add(paper)
        found[paper] = note
    for paper in ambiguous:
        found.pop(paper, None)
    return found


def read_notes(brain: Brain, corpus: list[Candidate], cfg, paths,
               collection=None, citekeys: dict[int, str] | None = None,
               refresh_notes: bool = True) -> list[dict]:
    citekeys = citekeys or {}
    d = paths.annotations_dir
    d.mkdir(parents=True, exist_ok=True)
    legacy = _legacy_notes_by_paper(d)
    notes: list[dict] = [None] * len(corpus)  # type: ignore
    done = 0
    refreshed = 0
    migrated = 0
    t_step = time.time()
    for i, c in enumerate(corpus):
        ck = citekeys.get(i) or f"{i:03d}"
        # Keyed by citekey, as work/located/ already is: a paper's notes belong to the paper,
        # not to its position in a list that changes whenever the collection does.
        fp = d / f"{_located_filename(ck)}.json"
        cached = None
        if fp.exists():
            try:
                cached = json.loads(fp.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                cached = None
        elif c.author_year() in legacy:
            cached = legacy[c.author_year()]     # migrate, preserving the expensive read
            fp.write_text(json.dumps(cached, indent=2, ensure_ascii=False), encoding="utf-8")
            migrated += 1

        is_refresh = False
        if cached is not None:
            # Use the cache unless its extraction is older than the current logic. An
            # unversioned note (no _v) predates versioning and counts as stale, so an
            # extraction-prompt fix re-reads the existing corpus instead of silently
            # applying only to papers added later. --no-refresh-notes keeps stale notes.
            if not refresh_notes or cached.get("_v", 0) >= _NOTES_VERSION:
                notes[i] = cached
                done += 1
                continue
            is_refresh = True
            refreshed += 1
        label = f"{c.first_author_last} {c.year or ''}".strip()
        tag = " (refresh)" if is_refresh else ""
        print(f"  {_stamp()}[{i + 1}/{len(corpus)}] {label}{tag}", end="", flush=True)
        text = _paper_text(c)
        # Index full page-marked text in ChromaDB before condensing for notes
        if collection is not None and not _chroma.is_paper_indexed(collection, ck):
            n_chunks = _chroma.index_paper(collection, brain, ck, text)
            print(f"  ({n_chunks} chunks indexed)", end="", flush=True)
        print(flush=True)
        if len(text) > _DIRECT_CHARS:
            if collection is not None:
                queries = [
                    f"{cfg.topic} {cfg.focus} main argument contribution hypothesis",
                    "methodology research design data collection analysis",
                    "results findings outcomes evidence limitations",
                ]
                text = _chroma.query_paper_multi(collection, brain, ck, queries,
                                                 n_per_query=3)
                if not text:
                    text = _condense(brain, _paper_text(c))  # fallback
            else:
                text = _condense(brain, text)
        try:
            # think=False: extraction, not judgement. The paper says what it says; a
            # scratchpad only re-narrates it at 3 tok/s before the JSON arrives.
            raw = brain.coordinator(_read_prompt(c, cfg.topic, cfg.focus, text),
                                    READ_SYS, think=False)
            note = _parse_json_obj(raw)
            note["_paper"] = c.author_year()
            note["_v"] = _NOTES_VERSION
            fp.write_text(json.dumps(note, indent=2, ensure_ascii=False),
                          encoding="utf-8")  # write only on success -> resumable
            done += 1
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] read failed for {c.first_author_last} {c.year or ''}: {e} "
                  f"(will retry on next run)", file=sys.stderr)
            note = {"argument": "", "methods": "", "findings": "", "limitations": "",
                    "relevance": "", "themes": [], "_paper": c.author_year()}
        notes[i] = note
    extra = f", {refreshed} refreshed for updated extraction" if refreshed else ""
    extra += f", {migrated} re-keyed from the positional cache" if migrated else ""
    print(f"  notes ready: {done}/{len(corpus)}{extra}  "
          f"[{_fmt_dt(time.time() - t_step)}]")
    return notes


# ──────────────────────────────────────────────────────────────────────────
# 2. synthesize (REDUCE)
# ──────────────────────────────────────────────────────────────────────────
SYNTH_SYS = """\
You are writing the narrative section of a scholarly literature review.

STRUCTURE
- Organise around IDEAS, not sources. The unit of the review is an idea about the project's topic and focus, traceable to the source(s) that support it. Sources are evidence for ideas, never a catalogue to march through.
- The corpus is the FOUNDATION this review rests on, not a menu to select from. Every source you were given is either cited in the narrative or explicitly rejected with a reason — silence is not a decision. A review that quietly uses a third of its evidence has not been made tight; it has been left unfinished.
- The failure to avoid is SERIAL EXPOSITION: sources introduced one at a time, each getting its sentence or its paragraph, marched through in sequence. That failure is about STRUCTURE, not about how many sources you cite. Citing many sources is the goal. Weaving is citation-dense by construction — you cannot compare, qualify, or connect sources you have not cited.
- Build a SMALL number of thematic sections. Each develops a few related ideas and WEAVES them — comparing, qualifying, connecting — into a claim that says something about THIS project. Fit sections to the material; do not use a fixed template. A 20-source review has perhaps 4-6 sections, never one per source.
- Develop each section by ACCRETION across at least three paragraphs: the first puts a few citable ideas on the table, each grounded in its source(s); every later paragraph brings in NEW sources and connects them to the ideas already raised, so the evidence base grows paragraph by paragraph. "Three paragraphs" means three paragraphs' worth of cited ideas building on one another — NOT two citations padded across three paragraphs. Carry the "what this means for the project" point INSIDE these evidence-bearing paragraphs; never park it in a separate, citation-free conclusion. A heading over a single paragraph that just reports one source's findings is an annotated-bibliography entry — the failure to avoid.
- Each "## " heading names ONE idea in <=6 words. Never join concepts with commas or "and" (avoid "Dimensionality, Complexity, and Temporal Evolution"). If a section spans several ideas, split it or pick the single organising idea.
- When an idea has an established, well-cited origin, ground it there first (high citation counts in the digest) before layering on recent work or preprints; prefer the peer-reviewed article over a preprint when both support a claim.

EXPLAIN WHY IT MATTERS (the priority)
- For every claim, make plain why it matters to THIS review's topic and focus. Use the Relevance note in the digest to connect each source to the project's goal.
- State significance positively: what a source contributes or enables. Only name a gap when it is genuine and load-bearing for the topic. Do NOT note that a source omits a field outside its discipline (e.g. a musicology paper "did not use agent-based modeling") — that is obvious and adds nothing.
- Name gaps, tensions, and directions specifically; never "further work is needed".

QUANTIFY (do not flatten results into direction)
- When the digest gives a magnitude for a finding, STATE IT: the number, its direction, and what it measures — "kerbside collection raised paper-sorting accuracy by 30% [@key]", not "kerbside collection improves sorting". Reporting only the direction when a magnitude is available is a defect.
- Carry the effect size, percentage, p-value, odds/risk ratio, correlation, or sample size through to the claim, exactly as the digest states it. When several sources speak to one idea, prefer the strongest quantitative evidence.
- Never substitute "significantly", "substantially", or "markedly" for a number the digest actually provides.

CITATIONS
- EVERY paragraph cites at least one source. There are no transition-only, scene-setting, or conclusion-only paragraphs: if a paragraph states ideas worth a paragraph, those ideas have sources — name them. A paragraph containing no [@citekey] is a defect, not a stylistic choice.
- TRIANGULATE. A claim resting on one source is a lead; a claim two or three sources agree on, qualify, or conflict over is a foundation. Reach for the second and third source on every substantive claim, and say how they relate: "Modest tolerance thresholds produce exaggerated segregation [@schelling1971], an effect that survives when preferences are made asymmetric [@zhang2011] but weakens once physical venues constrain movement [@silver2021]."
- Roughly one source per three sentences of argument is a floor, not a target. A long paragraph standing on two citations is assertion with a citation attached.
- State each finding as a claim in its own right, then attach the source in square brackets immediately after: "Modest tolerance thresholds produce exaggerated segregation [@schelling1971]."
- Never make a citation the grammatical subject or agent of a sentence. Do NOT write "[@schelling1971] showed that...". Rewrite so the claim leads and the citation follows.
- Use the citekey EXACTLY as given in the digest (the [@key] tag beside each source). Do not invent or alter citekeys.

LANGUAGE
- Paraphrase; do not lift technical phrases verbatim from the sources. A reader should not need the original papers' vocabulary to follow your argument. This does NOT apply to quantities: keep reported numbers exact (see QUANTIFY) — a figure is evidence, not jargon.
- When a field-specific term is genuinely needed — no plain equivalent carries the same precision — introduce it once with a brief gloss in parentheses: "...using principal component analysis (a technique that compresses many correlated variables into a smaller, uncorrelated set)..."
- Prefer concrete, active verbs. "The study found that participants who received X showed Y" beats "an association between X and Y was observed".
- Write for an intelligent reader who is not already expert in this exact sub-field. They can follow careful reasoning but should not be expected to know domain acronyms or insider shorthand on sight.

STYLE
- Be tight. One main idea per sentence; at most one subordinate clause. Prefer plain verbs over nominalisations.
- Keep paragraphs to 3-5 sentences, and give every section at least three of them. Let length follow the material: a well-synthesised section runs several hundred words. Do not pad with filler, but never stop a section at a single paragraph.
- Cut filler: "it is worth noting", "this highlights", "rests on the demonstration that", "underscores that", "a growing body of research". Every sentence adds a new fact or connection.
- Use "## " headings for themes. Do not write a bibliography (that is added separately).

Write only the narrative review."""

# Two-pass critique. The LINT pass is mechanical — format, filler, headings, order —
# and runs without reasoning (think=False) because there is nothing to deliberate.
# The SUBSTANCE pass is a demanding peer reviewer reading for the quality of the
# argument; it runs with reasoning on (the coordinator default). Keeping them apart
# stops the cheap mechanical wins from starving the judgement that needs attention.

# Citation format, uncited paragraphs, and heading shape are all decided in `guards`, so
# they are gone from here. Two detectors for one defect burns a pass, and an LLM can
# hallucinate an offender the regex knows isn't there. What is left is what code cannot
# judge: whether a phrase is empty, and whether the evidence is introduced in the wrong
# order.
_LINT_SYS = """\
You are a copy-editor auditing one section of a literature review for MECHANICAL defects
only. Respond with ONLY a numbered list of specific, actionable problems, one per
line, each quoting the offending text. If there are none, respond "OK"."""

_LINT_PROMPT = """\
Audit this section for mechanical defects only. Do NOT comment on argument, coverage, or
substance — a separate reviewer handles those. Citation format and uncited paragraphs are
checked in code; ignore them.

Section: {heading}

{narrative}

Check:
1. Filler phrases — flag any of: "it is worth noting", "this highlights",
   "further work is needed", "a growing body of research", "underscores",
   "plays a crucial role", "rests on the demonstration that", "has been shown to",
   or similar content-free phrases.
2. Evidence order — flag any paragraph that opens with a recent (post-2020) or preprint
   source before citing the foundational or well-cited work on the same point.

Output: numbered list with quoted text; skip checks with no issues. If all pass, "OK"."""

# Section-scoped, because a whole-narrative critique cannot be applied without re-emitting
# the whole narrative — and at 84 sources neither fits the context window. The reviewer sees
# ONE section and the evidence that section was drafted from.
_SUBSTANCE_SYS = """\
You are a demanding peer reviewer reading one section of a literature review for the
QUALITY OF ITS ARGUMENT. Ignore formatting, citation style, and typography — a
copy-editor handles those, and citation mechanics are checked in code. Respond with ONLY a
numbered list of specific, actionable problems, one per line, quoting the text you mean. If
the section is genuinely strong, respond "OK"."""

_SUBSTANCE_PROMPT = """\
Review topic: {topic}
Focus: {focus}

This section's idea: {heading} — {claim}

Evidence this section may draw on (ground truth; each line begins with its [@citekey]):
{candidates}

Section under review:
{narrative}

Judge:
1. Serial exposition — flag any passage that introduces its sources one at a time, each
   getting its own sentence or paragraph, instead of setting them against each other around
   a shared point. This is about STRUCTURE, not citation count: a densely cited paragraph
   that weaves is right; a lightly cited one that marches is wrong.
2. Synthesis vs summary — flag any paragraph that walks through sources one at a time
   instead of connecting their ideas around a shared point.
3. Claim support — flag any claim not backed by the evidence above, or that overstates what
   its cited source actually shows. Quote the claim and name the mismatch.
4. Under-triangulated claims — flag any substantive claim resting on a single source when
   the evidence above holds others that agree with it, qualify it, or contradict it. Name the
   source that should be brought alongside. A claim on one source is a lead, not a foundation.
5. Unused evidence — name any source above that bears on this section's idea and is not
   cited. The evidence list is the foundation this section rests on, not a menu; say what
   each unused source would add, or why it genuinely bears on nothing here.
6. Does it serve the idea — flag anything in the section that does not develop
   "{heading}", and anything the idea needs that is missing.
7. Specific gaps — flag any gap, tension, or future direction stated vaguely
   ("more research is needed", "warrants further study") rather than as a named,
   load-bearing gap.
8. Unquantified findings — flag any claim stated only directionally ("improves",
   "increases", "is more effective") when the evidence gives a magnitude for it. Quote
   the claim and name the number that should appear in it. Vague words ("significantly",
   "substantially") standing in for an available figure are defects.

Output: numbered list with quoted text; skip checks with no issues. If strong, "OK"."""

# A whole-narrative revise prompt used to live here. It cannot exist any more: applying a
# critique to the review meant re-emitting the review, and at 84 sources neither the evidence
# nor the output fits a 16k window. Repairs are section-scoped — see `_SECTION_REVISE_PROMPT`.

_GROUND_PROMPT = """\
Every paragraph in a literature review must cite at least one source. The paragraphs listed
below contain NO citation. Revise the narrative so each one either (a) states the source(s)
for its ideas in [@citekey] form drawn from the digest, or (b) is merged into an adjacent
paragraph that already carries the evidence. Do NOT invent citekeys, and do NOT keep any
transition- or conclusion-only paragraph that stands without a citation. Change nothing else.

Evidence digest:
{digest}

Current narrative:
{narrative}

Paragraphs lacking a citation:
{offenders}

Output only the revised narrative."""

# Canonical in `guards` now, so redline/revise/summarize share one definition of a citation
# and one sentence splitter. Re-exported here because callers already import them from this
# module.
_CITE_TAG_RE = guards.CITE_TAG_RE
_all_citekeys = guards.all_citekeys


def _is_ok(text: str) -> bool:
    return text.strip().upper().startswith("OK")


def _uncited_paragraphs(narrative: str, max_snippet: int = 160) -> list[str]:
    """Body paragraphs (heading lines stripped) that contain no [@citekey] citation.

    Returns a short snippet of each offender, for use as a critique item or in the
    grounding backstop. Headings, blank lines, and pure-heading blocks are ignored."""
    return [p.snippet(max_snippet) for p in guards.parse_paragraphs(narrative) if not p.keys]


def _digest(corpus: list[Candidate], notes: list[dict], citekeys: dict[int, str]) -> str:
    lines = []
    for i, (c, a) in enumerate(zip(corpus, notes)):
        ck = citekeys.get(i, "")
        label = f"[@{ck}]" if ck else f"({c.author_year()})"
        themes = ", ".join(a.get("themes", []))
        cites = f" [{c.cited_by_count} citations]" if c.cited_by_count else ""
        gaps = a.get("gaps", "").strip()
        gap_str = f" NOT addressed: {gaps}" if gaps else ""
        rel = a.get("relevance", "").strip()
        rel_str = f" Relevance: {rel}" if rel else ""
        lines.append(
            f"- {label}{cites} {a.get('argument','')} "
            f"Findings: {a.get('findings','')}{rel_str} Themes: {themes}{gap_str}".strip())
    return "\n".join(lines)


# ── sectioned synthesis ───────────────────────────────────────────────────────
# A whole-corpus synthesis does not fit in a 16k context: 84 sources' worth of evidence is
# ~31k tokens of digest alone, and Ollama silently discards the head of an oversized prompt.
# The last SchellingChords review cited 15 of 84 sources — every one of them from the tail of
# the digest, because the model never saw the rest. No prompt could have fixed that.
#
# So the review is built one section at a time, and no call ever sees more than it can hold:
#
#   1. plan       compact digest of the WHOLE corpus (~250 chars/source) -> section ideas.
#                 The only call that needs a global view, and the only one cheap enough to
#                 have one. Section count follows the material, never the corpus size.
#   2. shortlist  embed each source's compact line, embed each section's idea, cosine ->
#                 the ~18 sources that bear on it. Retrieval, not judgement: no LLM call,
#                 exactly as `locate` already works. Deliberately recall-biased — the
#                 shortlist over-offers and the drafting prunes.
#   3. draft      one call per section, seeing ONLY its own shortlist's full digest lines.
#                 A section re-cites freely; a foundational source belongs in several.
#   4. polish     per-section critique -> re-draft that section. Local defects, local fix.
#   5. orphans    a source no section cited is offered to its nearest section. What survives
#                 must be rejected BY NAME, with a reason.
#   6. repair     deterministic guards over the assembly; each Finding carries its section,
#                 so a repair re-drafts ONE section rather than re-emitting the review.

_COMPACT_ARG_CHARS = 150      # enough to tell what a paper argues, cheap enough for 84 of them
_SHORTLIST_K = 18             # recall-biased: over-offer, let drafting prune to ~12
_SHORTLIST_CHARS = 24_000     # ~6k tokens of full digest lines per drafting call
_MAX_SECTIONS = 12

# A revision call carries the section's current text and a critique on top of its evidence,
# so it gets a smaller evidence budget than a fresh draft. Without this the orphan pass — which
# appends every orphan's full digest line to an already-full candidate list — sent 15k-token
# prompts into a 10.6k-token budget, and Ollama quietly ate the front of them.
_REVISE_CANDIDATE_CHARS = 16_000
_MAX_ORPHANS_PER_OFFER = 4      # more than a paragraph can absorb without listing them
_MAX_OFFERS_PER_SOURCE = 2      # nearest section, then one more. Never a tour of all of them.
_REJECT_BATCH_CHARS = 6_000     # keep the rejection prompt inside the context budget


def _truncate(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "…"


def _compact_lines(corpus: list[Candidate], notes: list[dict],
                   citekeys: dict[int, str]) -> dict[str, str]:
    """One short line per source: enough to know it exists and what it argues.

    The whole corpus in ~5k tokens, so the planner can see all of it at once. The full line
    (`_digest`) carries the numbers and is spent only where a section actually draws on it.
    """
    out: dict[str, str] = {}
    for i, (c, a) in enumerate(zip(corpus, notes)):
        ck = citekeys.get(i)
        if not ck:
            continue
        cites = f", {c.cited_by_count} cites" if c.cited_by_count else ""
        themes = ", ".join((a.get("themes") or [])[:4])
        arg = _truncate(a.get("argument", ""), _COMPACT_ARG_CHARS)
        out[ck] = f"- [@{ck}] ({c.author_year()}{cites}) {arg}" + (f" [{themes}]" if themes else "")
    return out


def _full_lines(corpus: list[Candidate], notes: list[dict],
                citekeys: dict[int, str]) -> dict[str, str]:
    """Digest lines indexed by citekey — the detailed evidence, spent per section."""
    out: dict[str, str] = {}
    for line in _digest(corpus, notes, citekeys).splitlines():
        keys = guards.all_citekeys(line)
        if keys:
            out[keys[0]] = line.strip()
    return out


@dataclass
class Section:
    """One idea, its shortlisted evidence, and its drafted paragraphs."""
    heading: str
    claim: str
    candidates: list[str] = field(default_factory=list)   # citekeys, most relevant first
    text: str = ""


# ── 1. plan the sections ──────────────────────────────────────────────────────

_PLAN_SYS = """\
You plan the thematic sections of a scholarly literature review. Each section is ONE IDEA
about the project's topic and focus — never a source, never a bucket of sources, never a
list of related concepts joined by "and". Respond with ONLY a JSON array, nothing else."""

_PLAN_PROMPT = """\
Review topic: {topic}
Focus: {focus}

You have {n} curated sources. Here is one line per source:
{compact}

Plan the sections of the review.

- Each section names ONE idea in at most 6 words. Never join concepts with a comma, "and",
  or "/". "Dimensionality, Complexity, and Temporal Evolution" is three sections, not one.
- Each section carries a claim: one sentence saying what the section argues about THIS
  project's topic and focus. Not a description of the sources — an argument.
- The corpus is the foundation this review rests on, not a menu to select from. A section
  develops its idea across at least three paragraphs and can carry perhaps 8-12 sources, so
  {n} sources need enough sections to hold them. You will be asked to justify, by name, any
  source no section uses.
- A source may serve several sections. Foundational work usually does. Do not try to
  partition the corpus.
- Order the sections so the argument builds: foundations first, then what they enable, then
  the tensions and what remains open.

Output a JSON array of objects, between 4 and {max_sections} of them:
[{{"heading": "Preference thresholds drive clustering", "claim": "Mild local preferences \
suffice to produce macro-level segregation, which gives the project its mechanism for \
emergent tonal stability."}}]"""


def _plan_sections(brain: Brain, cfg, compact: dict[str, str]) -> list[Section]:
    raw = brain.coordinator(
        _PLAN_PROMPT.format(topic=cfg.topic, focus=cfg.focus or "", n=len(compact),
                            compact="\n".join(compact.values()),
                            max_sections=_MAX_SECTIONS),
        _PLAN_SYS, num_ctx=16384)
    sections: list[Section] = []
    for obj in _parse_json_list(raw)[:_MAX_SECTIONS]:
        if isinstance(obj, dict) and obj.get("heading"):
            sections.append(Section(heading=str(obj["heading"]).strip(),
                                    claim=str(obj.get("claim", "")).strip()))
    return sections


# ── 2. shortlist each section's evidence (embeddings, no LLM) ─────────────────

def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _shortlist(brain: Brain, sections: list[Section], compact: dict[str, str],
               full: dict[str, str], top_k: int = _SHORTLIST_K) -> list[list[float]]:
    """Rank every source against every section idea. Returns the similarity matrix
    (section × source) so the orphan pass can reuse it, and fills `sec.candidates`.

    Selection is retrieval, not judgement — the same reasoning that keeps `locate` free of
    LLM calls. The shortlist is capped by CHARACTER BUDGET, not just count, so a section
    whose sources carry long digest lines still fits the drafting call's context.
    """
    keys = list(compact)
    print(f"  {_stamp()}Embedding {len(keys)} sources and {len(sections)} section ideas...",
          flush=True)
    src_vecs = brain.embed_batch([compact[k] for k in keys])
    sec_vecs = brain.embed_batch([f"{s.heading}. {s.claim}" for s in sections])

    matrix: list[list[float]] = []
    for sec, sv in zip(sections, sec_vecs):
        sims = [_cosine(sv, v) for v in src_vecs]
        matrix.append(sims)
        ranked = sorted(range(len(keys)), key=lambda i: sims[i], reverse=True)
        chosen, budget = [], _SHORTLIST_CHARS
        for i in ranked[:top_k * 2]:
            line = full.get(keys[i], "")
            if len(chosen) >= top_k or len(line) > budget:
                continue
            chosen.append(keys[i])
            budget -= len(line)
        sec.candidates = chosen
        print(f"    §{len(matrix)} {sec.heading[:44]:<44} {len(chosen)} candidate sources",
              flush=True)
    return matrix


def _candidate_block(sec: Section, full: dict[str, str], extra: list[str] = (),
                     budget: int = _SHORTLIST_CHARS) -> str:
    """The evidence a section may cite, capped so the call fits its context window.

    `extra` goes first and is never dropped: it is what this call exists to add (an orphan
    source being offered a home). The shortlist fills whatever budget remains.
    """
    lines, spent = [], 0
    for k in dict.fromkeys(list(extra) + list(sec.candidates)):
        line = full.get(k, f"- [@{k}]")
        if lines and spent + len(line) > budget:
            continue
        lines.append(line)
        spent += len(line)
    return "\n".join(lines)


# ── 3/4. draft and polish one section ─────────────────────────────────────────

_DRAFT_PROMPT = """\
Review topic: {topic}
Focus: {focus}

You are writing ONE section of the review. The full outline, in order:
{outline}

Write the section marked ▶: "{heading}"
Its claim: {claim}
{transition}
EVIDENCE you may cite — each line begins with the [@citekey] to cite that source by. Cite
ONLY from this list, using the exact key shown:
{candidates}

Write at least three paragraphs that ACCRETE: the first puts a few cited ideas on the table,
each later paragraph brings in sources not yet used in this section and connects them to what
is already established. Most paragraphs should cite several sources — weaving means setting
sources against each other, so it is citation-dense by construction. Aim to use most of the
evidence above; a source you leave out is a source you are asserting bears on nothing here.

Carry the "what this means for the project" point INSIDE the evidence-bearing paragraphs.
Never end with a citation-free conclusion.

Do NOT write the "## " heading — output only the paragraphs of this section."""

_SECTION_REVISE_PROMPT = """\
Review topic: {topic}
Focus: {focus}

Section: {heading} — {claim}

EVIDENCE you may cite — cite ONLY from this list, using the exact key shown:
{candidates}

Current draft of this section:
{narrative}

Problems to fix:
{critique}

Fix every problem. Preserve all correct content; change only what the problems require.
Keep [@citekey] citation format. Do NOT write the "## " heading — output only the
paragraphs of this section."""


def _outline(sections: list[Section], current: int) -> str:
    return "\n".join(f"{'▶' if i == current else ' '} {i + 1}. {s.heading}"
                     for i, s in enumerate(sections))


def _tail_sentence(text: str) -> str:
    units = guards.sentence_units(text)
    return units[-1].strip() if units else ""


def _draft_section(brain: Brain, cfg, sections: list[Section], i: int,
                   full: dict[str, str], sys_prompt: str,
                   prev_tail: str = "", extra: list[str] = ()) -> str:
    sec = sections[i]
    transition = (f"\nThe previous section ends: \"{prev_tail}\"\nOpen so the argument "
                  f"continues from there.\n" if prev_tail else "")
    return brain.coordinator(
        _DRAFT_PROMPT.format(
            topic=cfg.topic, focus=cfg.focus or "", outline=_outline(sections, i),
            heading=sec.heading, claim=sec.claim, transition=transition,
            candidates=_candidate_block(sec, full, extra)),
        sys_prompt, num_ctx=16384).strip()


def _section_guards(sec: Section, text: str, corpus_keys: set[str]) -> list[guards.Finding]:
    """The deterministic batteries, applied to one section in isolation.

    `thin_sections` and the disposition ledger are deliberately absent: they are properties
    of the whole review, and are enforced later against the assembly.
    """
    mini = f"## {sec.heading}\n\n{text.strip()}"
    paras = guards.parse_paragraphs(mini)
    return (guards.uncited_paragraphs(paras)
            + guards.author_year_prose(text)
            + (guards.unresolved_keys(text, corpus_keys) if corpus_keys else [])
            + guards.short_sections(paras)
            + guards.accretion_violations(paras)
            + guards.triangulation_violations(paras)
            + guards.sparse_paragraphs(paras))


def _polish_section(brain: Brain, cfg, sections: list[Section], i: int,
                    full: dict[str, str], sys_prompt: str, corpus_keys: set[str],
                    rounds: int | None = None) -> str:
    """Critique -> re-draft, scoped to one section.

    Deterministic guards first (they decide precisely, and cost nothing), then the two LLM
    passes on what they cannot decide. A clean lint can no longer let a one-paragraph section
    or a paragraph resting on one source slip through: the guards hold a veto over early exit.
    """
    if rounds is None:
        rounds = max(1, int(getattr(brain.cfg, "critique_rounds", 2)))
    sec = sections[i]
    text = sec.text
    for r in range(1, rounds + 1):
        findings = _section_guards(sec, text, corpus_keys)
        try:
            lint = brain.coordinator(_LINT_PROMPT.format(heading=sec.heading, narrative=text),
                                     _LINT_SYS, num_ctx=16384, think=False)
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] lint failed ({e}); skipping.", file=sys.stderr)
            lint = "OK"
        try:
            substance = brain.coordinator(
                _SUBSTANCE_PROMPT.format(
                    topic=cfg.topic, focus=cfg.focus or "", heading=sec.heading,
                    claim=sec.claim, narrative=text,
                    candidates=_candidate_block(sec, full, budget=_REVISE_CANDIDATE_CHARS)),
                _SUBSTANCE_SYS, num_ctx=16384)
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] peer review failed ({e}); skipping.", file=sys.stderr)
            substance = "OK"

        if _is_ok(lint) and _is_ok(substance) and not findings:
            break

        parts = []
        if findings:
            kinds = sorted({f.kind for f in findings})
            print(f"    [guard] §{i + 1} {len(findings)} finding(s): {', '.join(kinds)}",
                  flush=True)
            parts.append(guards.as_critique(findings, "MECHANICAL — fix every one:"))
        if not _is_ok(lint):
            parts.append("COPY-EDIT:\n" + lint.strip())
        if not _is_ok(substance):
            parts.append("SUBSTANTIVE:\n" + substance.strip())

        print(f"    {_stamp()}§{i + 1} revising (round {r}/{rounds})...", flush=True)
        try:
            # think=False: the judgement already happened — the guards, the lint and the
            # peer review decided what is wrong. This call applies a critique it was handed.
            text = brain.coordinator(
                _SECTION_REVISE_PROMPT.format(
                    topic=cfg.topic, focus=cfg.focus or "", heading=sec.heading,
                    claim=sec.claim, narrative=text, critique="\n\n".join(parts),
                    candidates=_candidate_block(sec, full, budget=_REVISE_CANDIDATE_CHARS)),
                sys_prompt, num_ctx=16384, think=False).strip()
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] section revision failed ({e}); keeping current.", file=sys.stderr)
            break
    return text


def _assemble(sections: list[Section]) -> str:
    return "\n\n".join(f"## {s.heading}\n\n{s.text.strip()}"
                       for s in sections if s.text.strip())


# ── 5. orphans and the rejection ledger ───────────────────────────────────────
# Every curated source is cited, or explicitly rejected with a reason. No threshold to argue
# about and no percentage to game: it forces a decision per source instead of letting silence
# do the work. Silence is how an 84-source corpus became a 15-source narrative without anyone
# deciding to drop 69 sources. Placing runs BEFORE rejecting — the corpus is the foundation
# the argument rests on, so dropping a source has to be argued for.
#
# An offer costs a paragraph, not a section. Re-emitting a whole section to insert two
# citations spent ~12k generated tokens per call and, over two rounds, 14h37m to cite 16
# sources — while the sections it rewrote lost two triangulated paragraphs on the way. So an
# offer now returns ONE paragraph plus the citekeys it declined, and a decline is routed to
# the next-nearest section instead of being re-offered to the section that just refused it.
# Refusals accumulate a reason per source, which is what the rejection ledger reads at the end:
# a source is only rejected once every section it resembles has said, in writing, why not.

_REJECT_SYS = """\
You decide which curated sources a literature review may legitimately leave uncited. Respond
with ONLY a JSON object mapping each citekey to a one-sentence reason it does not belong in
THIS review. Include a key only if the source genuinely bears on none of the review's ideas.
If every source should be cited, respond {}."""

_REJECT_PROMPT = """\
Review topic: {topic}
Focus: {focus}

The review's sections:
{outline}

These curated sources are cited in no section. Each was offered to the sections it most
resembles, and each of those sections declined it — their reasons are quoted beneath it:
{unplaced}

For each, decide: does it genuinely bear on none of these ideas? A source is NOT rejectable
merely because the review is already long enough, because its finding resembles one already
cited, or because it will appear in the annotated bibliography anyway — corroborating
evidence is what turns a claim into a foundation.

Reject only what is truly off-topic, superseded by a source already cited, or too weak to
support any claim. Give the reason in one sentence.

Output only the JSON object: {{"citekey": "reason", ...}}"""


_WEAVE_SYS = """\
You add evidence to one section of a scholarly literature review. You never restate or
re-emit the section: you write at most ONE new paragraph, and you say plainly which of the
offered sources do not belong here. Follow the output shape exactly."""

_WEAVE_PROMPT = """\
Review topic: {topic}
Focus: {focus}

Section: {heading} — {claim}

The section as it stands:
{narrative}

These curated sources are cited nowhere in the review, and of all its sections this one is
the closest match:
{offers}

For each offered source, decide whether it genuinely supports, qualifies, or complicates a
claim this section makes. A source belongs here only if you can say what it ADDS — never
append a sentence that merely names it.

Write ONE new paragraph, to stand as this section's last, weaving in every source that
belongs. Connect them to what the section has already established; set them against the work
it already cites. Cite with [@citekey]. If no offered source belongs here, write NONE in
place of the paragraph.

Then list the offered sources you left out, each with one sentence saying why it bears on
nothing THIS section argues. Judge it against this section only — another section may yet
take it.

Output exactly this shape, both headers present:

PARAGRAPH:
<the paragraph, or NONE>

DECLINED:
{{"citekey": "why it bears on nothing this section argues", ...}}"""


def _parse_weave(raw: str) -> tuple[str, dict[str, str]]:
    """Split a weave reply into (paragraph, declined). Tolerant: a reply that is bare prose
    with no headers is taken as the paragraph, declining nothing.

    Strict `json.loads` on the DECLINED block, not `_parse_json_obj` — that helper salvages
    non-JSON by wrapping it in a dict, which turns a prose apology into a citekey named
    "argument" declining a source that was never offered.
    """
    body, declined = raw, {}
    m = re.search(r"DECLINED:\s*(.*)$", raw, re.DOTALL | re.IGNORECASE)
    if m:
        body = raw[:m.start()]
        obj = re.search(r"\{.*\}", m.group(1), re.DOTALL)
        try:
            declined = {str(k): str(v)
                        for k, v in json.loads(obj.group(0)).items() if v} if obj else {}
        except (json.JSONDecodeError, AttributeError, TypeError):
            declined = {}
    body = re.sub(r"^.*?PARAGRAPH:\s*", "", body, flags=re.DOTALL | re.IGNORECASE)
    body = re.sub(r"^\s*#+\s.*$", "", body, flags=re.MULTILINE).strip()
    if body.strip().upper().startswith("NONE"):
        body = ""
    return body, declined


def _weave_sys(sys_prompt: str) -> str:
    """The weave role, carrying whatever style profile the synthesis system prompt carries.

    The paragraph this call returns is appended to a section written in the author's voice.
    Dropping the style block here would seam a stranger's prose onto the end of every section
    that takes an orphan.
    """
    i = sys_prompt.find("WRITING STYLE")
    return _WEAVE_SYS + (f"\n\n{sys_prompt[i:]}" if i >= 0 else "")


def _weave_orphans(brain: Brain, cfg, sec: Section, orphans: list[str],
                   full: dict[str, str], sys_prompt: str) -> tuple[str, dict[str, str]]:
    """Offer `orphans` to one section. Returns (paragraph, declined).

    The prompt carries the section's own text and the orphans' digest lines — and nothing
    else. Withholding the shortlist halves the prompt and removes the only way this call can
    cite a source it was not asked about.
    """
    offers = "\n".join(full.get(k, f"- [@{k}]") for k in orphans)
    raw = brain.coordinator(
        _WEAVE_PROMPT.format(topic=cfg.topic, focus=cfg.focus or "", heading=sec.heading,
                             claim=sec.claim, narrative=sec.text, offers=offers),
        _weave_sys(sys_prompt), num_ctx=16384, think=False)
    para, declined = _parse_weave(raw)
    # A paragraph that cites none of the offered sources has not placed any of them; appending
    # it would grow the section without earning it. Drop it and let the orphans route on.
    if para and not (set(guards.all_citekeys(para)) & set(orphans)):
        para = ""
    return para, {k: v for k, v in declined.items() if k in orphans}


def _reject_ledger(brain: Brain, cfg, sections: list[Section], unplaced: set[str],
                   lines: dict[str, str], refusals: dict[str, list[str]]) -> dict[str, str]:
    """Demand a reason for every still-uncited source, in batches that fit the context.

    One call carrying all 32 orphans' full digest lines ran ~12.2k tokens into a 10.6k-token
    budget: Ollama dropped the head of the prompt — the system message, the topic and the
    outline — and the model rejected one source out of thirty-two, having lost the very
    outline it was asked to judge against. Compact lines, and batched.
    """
    rejected: dict[str, str] = {}
    outline = _outline(sections, -1)

    def _flush(batch: list[str]) -> None:
        if not batch:
            return
        try:
            raw = brain.coordinator(
                _REJECT_PROMPT.format(topic=cfg.topic, focus=cfg.focus or "",
                                      outline=outline, unplaced="\n".join(batch)),
                _REJECT_SYS, num_ctx=16384, think=False)
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            parsed = json.loads(m.group(0)) if m else {}
            rejected.update({k: str(v) for k, v in parsed.items() if k in unplaced and v})
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] rejection ledger batch failed ({e}).", file=sys.stderr)

    batch: list[str] = []
    spent = 0
    for k in sorted(unplaced):
        entry = lines.get(k, f"- [@{k}]")
        for why in refusals.get(k, [])[:2]:
            entry += f"\n    declined: {_truncate(why, 160)}"
        if batch and spent + len(entry) > _REJECT_BATCH_CHARS:
            _flush(batch)
            batch, spent = [], 0
        batch.append(entry)
        spent += len(entry)
    _flush(batch)
    return rejected


def _place_orphans(brain: Brain, cfg, sections: list[Section], matrix: list[list[float]],
                   keys: list[str], full: dict[str, str], sys_prompt: str,
                   corpus_keys: set[str], compact: dict[str, str] | None = None,
                   rounds: int = 4, max_offers: int = _MAX_OFFERS_PER_SOURCE) -> dict[str, str]:
    """Offer every uncited source to the section it is nearest; if that section declines it,
    offer it once more to the next-nearest. Then demand a reason for whatever no section took.

    Each offer gains the section at most one paragraph, so a round costs one short generation
    per section rather than one full re-emission. `tried` is what makes the rounds mean
    something: without it, round 2 asks the same section to reconsider the same source it has
    already refused, which is how 6h01m bought a single citation.

    `max_offers` is the brake. Routing a refused source onward corrects for embedding noise —
    "nearest section" is a cosine, not a judgement, and it puts a good source in front of the
    wrong reader often enough to be worth a second look. Routing it through ALL the sections
    is something else: it shops a weak source until one section is talked into it. The review
    wants the right sources cited, not every source cited. Two hearings, then the ledger.
    """
    rejected: dict[str, str] = {}
    refusals: dict[str, list[str]] = {}
    tried: dict[str, set[int]] = {}
    key_idx = {k: i for i, k in enumerate(keys)}
    n_sec = len(sections)

    def _sim(k: str, s: int) -> float:
        return matrix[s][key_idx[k]]

    for r in range(1, rounds + 1):
        unplaced = guards.disposition(_assemble(sections), corpus_keys, rejected).unplaced
        pending = [k for k in unplaced if k in key_idx]
        if not pending:
            break
        # Orphans nobody has looked at yet go first; among equals, the strongest affinity.
        # Alphabetical order let the same 36 keys win every round while 12 were never offered.
        pending.sort(key=lambda k: (len(tried.get(k, ())),
                                    -max(_sim(k, s) for s in range(n_sec))))

        by_sec: dict[int, list[str]] = {}
        for k in pending:
            seen = tried.setdefault(k, set())
            if len(seen) >= max_offers:
                continue      # it has had its hearings; the ledger takes it from here
            # nearest section that has not already refused it and still has room this round
            for s in sorted((s for s in range(n_sec) if s not in seen),
                            key=lambda s: _sim(k, s), reverse=True):
                if len(by_sec.setdefault(s, [])) < _MAX_ORPHANS_PER_OFFER:
                    by_sec[s].append(k)
                    break
        by_sec = {s: ks for s, ks in by_sec.items() if ks}
        if not by_sec:
            break

        print(f"  {_stamp()}[breadth] {len(pending)} source(s) undecided — offering to "
              f"{len(by_sec)} section(s) (round {r}/{rounds})...", flush=True)
        placed = 0
        for si, orphans in sorted(by_sec.items()):
            sec = sections[si]
            for k in orphans:
                tried[k].add(si)
            try:
                para, declined = _weave_orphans(brain, cfg, sec, orphans, full, sys_prompt)
            except Exception as e:  # noqa: BLE001
                print(f"  [warn] orphan placement in §{si + 1} failed ({e}).", file=sys.stderr)
                continue
            if para:
                sec.text = f"{sec.text.rstrip()}\n\n{para}"
            for k, why in declined.items():
                refusals.setdefault(k, []).append(f"§{si + 1} ({sec.heading}): {why}")
            took = [k for k in orphans if k not in declined and para
                    and k in guards.all_citekeys(para)]
            placed += len(took)
            print(f"    §{si + 1} offered {len(orphans)} · wove {len(took)} · "
                  f"declined {len(declined)}", flush=True)
        print(f"  {_stamp()}[breadth] round {r}: {placed} source(s) placed", flush=True)

    unplaced = guards.disposition(_assemble(sections), corpus_keys, rejected).unplaced
    if unplaced:
        print(f"  {_stamp()}[breadth] {len(unplaced)} source(s) still uncited — "
              f"requiring a reason for each...", flush=True)
        rejected.update(_reject_ledger(brain, cfg, sections, unplaced,
                                       compact or full, refusals))

    still = guards.disposition(_assemble(sections), corpus_keys, rejected).unplaced
    if still:
        # Not fatal — an unjustified omission is a reportable defect, not a reason to throw
        # the review away. It lands in the run log, the metrics line, and disposition.json.
        print(f"  [warn] {len(still)} source(s) neither cited nor justified: "
              f"{', '.join(sorted(still)[:8])}{' …' if len(still) > 8 else ''}",
              file=sys.stderr)
    return rejected


# ── 6. deterministic repair over the assembly ─────────────────────────────────

def _repair_assembly(brain: Brain, cfg, sections: list[Section], full: dict[str, str],
                     sys_prompt: str, corpus_keys: set[str],
                     rejected: dict[str, str] | None = None, rounds: int = 2) -> None:
    """Run the guard batteries over the whole review and re-draft only the sections at fault.

    Every section-scoped Finding carries its section index, so the repair is routed rather
    than parsed out of prose. Narrative-wide findings (unresolved keys, unplaced sources) are
    handled elsewhere — they have no single section to fix, and `_place_orphans` has already
    had its say about them.
    """
    for r in range(1, rounds + 1):
        narrative = _assemble(sections)
        findings = (guards.verifiability_battery(narrative, corpus_keys)
                    + guards.breadth_battery(narrative, corpus_keys, {}, rejected))
        grouped = guards.by_section(findings)
        if not grouped:
            break
        n = sum(len(v) for v in grouped.values())
        print(f"  {_stamp()}[guard] {n} section-scoped finding(s) across {len(grouped)} "
              f"section(s) — repairing (round {r}/{rounds})...", flush=True)
        for si, fs in sorted(grouped.items()):
            if si >= len(sections):
                continue
            sec = sections[si]
            print(f"    §{si + 1} {', '.join(sorted({f.kind for f in fs}))}", flush=True)
            try:
                sec.text = brain.coordinator(
                    _SECTION_REVISE_PROMPT.format(
                        topic=cfg.topic, focus=cfg.focus or "", heading=sec.heading,
                        claim=sec.claim, narrative=sec.text,
                        critique=guards.as_critique(fs, "Fix every one:"),
                        candidates=_candidate_block(sec, full,
                                                    budget=_REVISE_CANDIDATE_CHARS)),
                    sys_prompt, num_ctx=16384, think=False).strip()
            except Exception as e:  # noqa: BLE001
                print(f"  [warn] repair of §{si + 1} failed ({e}).", file=sys.stderr)


def _enforce_paragraph_citations(brain: Brain, narrative: str, digest: str,
                                 max_passes: int = 2) -> str:
    """Hard backstop after the critique loop: guarantee no paragraph is citation-free.

    The loop usually clears these, but if any survive we run up to `max_passes` focused
    revisions that only ground or merge the offending paragraphs. No-op (no LLM call) when
    every paragraph is already cited."""
    for _ in range(max_passes):
        uncited = _uncited_paragraphs(narrative)
        if not uncited:
            break
        offenders = "\n".join(f'- "{p}"' for p in uncited)
        print(f"  {_stamp()}[guard] grounding {len(uncited)} uncited paragraph(s)...",
              flush=True)
        try:
            narrative = brain.coordinator(
                _GROUND_PROMPT.format(digest=digest, narrative=narrative, offenders=offenders),
                SYNTH_SYS, num_ctx=16384, think=False)
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] citation enforcement failed ({e}); keeping current.",
                  file=sys.stderr)
            break
    return narrative


def synthesize(brain: Brain, corpus: list[Candidate], notes: list[dict], cfg,
               citekeys: dict[int, str], style_profile: str = "") -> tuple[str, dict[str, str]]:
    """Build the narrative section by section, and drive every curated source to a decision.

    Returns (narrative, rejected) where `rejected` maps citekey -> the reason that source was
    left uncited. An empty ledger with sources still uncited is a defect; it shows up in the
    metrics line rather than being quietly absorbed.

    No call here sees more evidence than its context can hold. That is the whole point: the
    previous one-shot synthesis handed a 31k-token digest to a 16k-token window and lost two
    thirds of the corpus without a word in the log.
    """
    compact = _compact_lines(corpus, notes, citekeys)
    full = _full_lines(corpus, notes, citekeys)
    corpus_keys = set(citekeys.values())
    keys = list(compact)

    sys_prompt = SYNTH_SYS
    if style_profile:
        sys_prompt = (sys_prompt.rstrip()
                      + f"\n\nWRITING STYLE\nMatch the following author's voice and "
                        f"prose style throughout:\n{style_profile}")

    print(f"  {_stamp()}Planning sections from {len(compact)} sources "
          f"(compact digest, ~{sum(map(len, compact.values())) // 4:,} tokens)...", flush=True)
    sections = _plan_sections(brain, cfg, compact)
    if not sections:
        raise SystemExit("  [error] section planning returned no sections; cannot synthesise.")
    for i, s in enumerate(sections, 1):
        print(f"    {i}. {s.heading}", flush=True)

    matrix = _shortlist(brain, sections, compact, full)

    prev_tail = ""
    for i, sec in enumerate(sections):
        print(f"\n  {_stamp()}Drafting §{i + 1}/{len(sections)}: {sec.heading}", flush=True)
        try:
            sec.text = _draft_section(brain, cfg, sections, i, full, sys_prompt, prev_tail)
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] drafting §{i + 1} failed ({e}); section dropped.", file=sys.stderr)
            continue
        sec.text = _polish_section(brain, cfg, sections, i, full, sys_prompt, corpus_keys)
        prev_tail = _tail_sentence(sec.text)

    sections = [s for s in sections if s.text.strip()]
    if not sections:
        raise SystemExit("  [error] every section failed to draft; cannot synthesise.")

    print(f"\n  {_stamp()}{guards.metrics(_assemble(sections), corpus_keys).line()}", flush=True)

    rejected = _place_orphans(brain, cfg, sections, matrix, keys, full,
                              sys_prompt, corpus_keys, compact)
    _repair_assembly(brain, cfg, sections, full, sys_prompt, corpus_keys, rejected)

    narrative = _assemble(sections)
    print(f"  {_stamp()}[polestar] {guards.metrics(narrative, corpus_keys, rejected).line()}",
          flush=True)
    return narrative, rejected


# ──────────────────────────────────────────────────────────────────────────
# 3. locate (POST-synthesis) — link the review's claims to cited sources
# ──────────────────────────────────────────────────────────────────────────
LOCATE_SYS = """\
You link a literature review's claims to where they appear in a cited source.
You are given (a) statements the review makes that cite this paper, and (b) the
paper's text with [p.N] page markers and section headings. Respond with ONLY a
JSON array:

[
  {"claim": "the review's claim, in your words",
   "location": "where it is supported, e.g. 'p.8, §3.2' or 'Results, p.8'",
   "quote": "a short verbatim quote (<=30 words) from the paper supporting it"}
]

Use ONLY the provided paper text for locations and quotes — do not invent. Omit
any statement you cannot locate in the text. Return [] if none can be located."""


def _cited_indices(narrative: str, citekeys: dict[int, str]) -> list[int]:
    """Return corpus indices whose [@citekey] tags appear in the narrative."""
    found = set(_all_citekeys(narrative))
    key_to_idx = {v: k for k, v in citekeys.items()}
    return [key_to_idx[k] for k in found if k in key_to_idx]


def _located_filename(citekey: str) -> str:
    """Filesystem-safe name for a per-paper located-claims cache file."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", citekey) or "paper"


def _claim_sentences(narrative: str, citekey: str) -> str:
    """Sentences in the narrative that cite this citekey (incl. grouped citations)."""
    return " ".join(
        s.strip() for s in re.split(r"(?<=[.!?])\s+", narrative)
        if citekey in _all_citekeys(s))


def _locate_prompt(c: Candidate, statements: str, body: str) -> str:
    return (f"Cited paper: {c.title} ({c.author_year()})\n\n"
            f"Statements the review makes citing this paper:\n{statements}\n\n"
            f"--- PAPER TEXT ---\n{body}\n--- END ---\n\nJSON array:")


def locate_claims(brain: Brain, narrative: str, corpus: list[Candidate],
                  notes: list[dict], cfg, paths, collection=None,
                  citekeys: dict[int, str] | None = None,
                  scope: str = "cited") -> dict[int, list]:
    """Locate the evidentiary support for the review's sources.

    scope="cited" (default): only sources the narrative cites — narrative-linked claims.
    scope="all": every curated source, so the annotated bibliography can be the full
    verifiable foundation. For a source the narrative does not cite, the statements to
    locate fall back to that source's own notes (relevance/findings/argument), grounding
    it in its own text rather than a narrative claim."""
    citekeys = citekeys or {}
    located_dir = paths.work / "located"
    located_dir.mkdir(parents=True, exist_ok=True)
    cited = _cited_indices(narrative, citekeys)
    targets = list(range(len(corpus))) if scope == "all" else cited
    print(f"  {_stamp()}{len(cited)} of {len(corpus)} sources cited; "
          f"locating claims for {len(targets)} source(s)...", flush=True)
    located: dict[int, list] = {}
    t_step = time.time()
    for i in targets:
        c = corpus[i]
        ck = citekeys.get(i) or f"{i:03d}"
        # Cache keyed by citekey, not corpus index: the corpus is re-gathered and
        # re-keyed between runs, so an index-keyed cache silently attaches the
        # wrong paper's passages to a reference. The citekey is the stable identity
        # the narrative actually cites with.
        fp = located_dir / f"{_located_filename(ck)}.json"
        if fp.exists():
            located[i] = json.loads(fp.read_text(encoding="utf-8"))
            continue
        statements = _claim_sentences(narrative, citekeys.get(i, ""))
        if not statements:
            n = notes[i] if i < len(notes) and notes[i] else {}
            statements = "; ".join(x for x in (n.get("relevance", ""),
                                               n.get("findings", ""),
                                               n.get("argument", "")) if x)
        label = f"{c.first_author_last} {c.year or ''}".strip()
        if collection is not None:
            if not _chroma.is_paper_indexed(collection, ck):
                print(f"  {_stamp()}indexing {label} for retrieval...", flush=True)
                _chroma.index_paper(collection, brain, ck, _paper_text(c))
            print(f"  {_stamp()}locating {label}  (embedding retrieval)", flush=True)
            items = _chroma.locate_direct(collection, brain, ck, statements)
        else:
            # Fallback: LLM-based locate when ChromaDB unavailable
            print(f"  {_stamp()}locating {label}  (LLM fallback)", flush=True)
            body = _paper_text(c)[:_LOCATE_FALLBACK_CHARS]
            try:
                raw = brain.coordinator(_locate_prompt(c, statements, body), LOCATE_SYS,
                                        think=False)
                items = _parse_json_list(raw)
            except Exception as e:  # noqa: BLE001
                print(f"  [warn] locate failed for {c.first_author_last}: {e}",
                      file=sys.stderr)
                items = []
        fp.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")
        located[i] = items
    print(f"  locate complete: {len(located)} papers  [{_fmt_dt(time.time() - t_step)}]")
    return located


# ──────────────────────────────────────────────────────────────────────────
# Annotated bibliography (cited sources only) + citation guard
# ──────────────────────────────────────────────────────────────────────────
def bibliography(corpus: list[Candidate], located: dict[int, list],
                 cited_indices: set[int] | None = None) -> str:
    """Annotated bibliography as markdown.

    When ``cited_indices`` is given, entries split into two tiers: **Cited in the review**
    (narrative-linked claims) and **Additional curated sources** (grounded via their own
    text). This makes the bibliography the full verifiable foundation for downstream writing
    while the narrative stays a curated synthesis. When ``cited_indices`` is None, every
    located entry renders as one list (legacy behaviour)."""
    def _entry(i: int) -> list[str]:
        c = corpus[i]
        lines = [f"**{c.full_citation()}**", ""]
        claims = [cl for cl in (located[i] or [])
                  if isinstance(cl, dict) and (cl.get("claim") or "").strip()]
        if claims:
            for cl in claims:
                claim = cl.get("claim", "").strip()
                loc = (cl.get("location") or "").strip()
                quote = (cl.get("quote") or "").strip()
                line = f"- {claim}"
                if loc:
                    line += f" — *{loc}*"
                if quote:
                    line += f': "{quote}"'
                lines.append(line)
        else:
            lines.append("- *(no supporting passage found in this source's full text "
                         "— verify the citation against the original)*")
        lines.append("")
        return lines

    def _in_order(idxs) -> list[int]:
        return sorted(idxs, key=lambda i: corpus[i].first_author_last.lower())

    keys = set(located.keys())
    out = ["## Annotated Bibliography", ""]
    if cited_indices is None:
        for i in _in_order(keys):
            out += _entry(i)
        return "\n".join(out)

    cited = keys & set(cited_indices)
    extra = keys - cited
    out += ["### Cited in the review", ""]
    for i in _in_order(cited):
        out += _entry(i)
    if extra:
        out += ["### Additional curated sources", ""]
        for i in _in_order(extra):
            out += _entry(i)
    return "\n".join(out)


def citation_check(narrative: str, citekeys: dict[int, str]) -> list[str]:
    """[@citekey] tags in the narrative that don't map to a corpus item."""
    known = set(citekeys.values())
    found = set(_all_citekeys(narrative))
    return sorted(found - known)


# ──────────────────────────────────────────────────────────────────────────
# BibTeX export
# ──────────────────────────────────────────────────────────────────────────
def _patch_bibtex_keys(bib_text: str, key_by_doi: dict[str, str],
                        key_by_title: dict[str, str]) -> str:
    """Align the export's citekeys with the ones used in the narrative.

    For Zotero items these already match (both come from the Better BibTeX key),
    so this is a no-op; it still rewrites the key for any source that fell back to
    a generated {last}{year} key, keeping refs.bib consistent with the .docx."""
    starts = [m.start() for m in re.finditer(r"^@", bib_text, re.MULTILINE)]
    if not starts:
        return bib_text
    blocks = [bib_text[starts[i]: starts[i + 1] if i + 1 < len(starts) else len(bib_text)]
              for i in range(len(starts))]
    result = []
    for block in blocks:
        m = re.match(r"@(\w+)\{([^,\s]+)", block)
        if not m:
            result.append(block)
            continue
        entry_type, zotero_key = m.group(1), m.group(2)
        new_key = None
        doi_m = re.search(r"\bdoi\s*=\s*\{([^}]+)\}", block, re.IGNORECASE)
        if doi_m:
            new_key = key_by_doi.get(norm_doi(doi_m.group(1).strip()))
        if not new_key:
            title_m = re.search(r"\btitle\s*=\s*\{((?:[^{}]|\{[^{}]*\})*)\}",
                                block, re.IGNORECASE)
            if title_m:
                raw = re.sub(r"[{}]", "", title_m.group(1))
                norm = re.sub(r"[^a-z0-9]+", " ", raw.lower()).strip()
                new_key = key_by_title.get(norm)
        if new_key and new_key != zotero_key:
            block = block.replace(f"@{entry_type}{{{zotero_key}",
                                  f"@{entry_type}{{{new_key}", 1)
        result.append(block)
    return "".join(result)


def _export_bibtex(cfg, gc, paths, citekeys: dict[int, str],
                   corpus: list[Candidate]) -> "Path | None":
    """Fetch BibTeX from Zotero, align citekeys with the narrative, write output/refs.bib."""
    if not gc.have_zotero:
        return None
    collection_key = cfg.zotero.get("collection_key", "")
    if not collection_key:
        return None
    from . import zotero as _zotero
    try:
        zc = _zotero.ZoteroClient(gc)
        bib_text = zc.collection_bibtex(collection_key)
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] BibTeX export failed ({e}); refs.bib not written.", file=sys.stderr)
        return None
    key_by_doi: dict[str, str] = {}
    key_by_title: dict[str, str] = {}
    for i, c in enumerate(corpus):
        ck = citekeys.get(i)
        if not ck:
            continue
        if c.doi_key:
            key_by_doi[c.doi_key] = ck
        if c.title_key:
            key_by_title[c.title_key] = ck
    bib_text = _patch_bibtex_keys(bib_text, key_by_doi, key_by_title)
    out = paths.output / "refs.bib"
    out.write_text(bib_text, encoding="utf-8")
    return out


def _write_disposition(paths, corpus: list[Candidate], citekeys: dict[int, str],
                       narrative: str, rejected: dict[str, str]) -> Path:
    """Persist what happened to every curated source. The polestar, auditable after the run.

    An unplaced source — neither cited nor rejected — is the defect the ledger exists to make
    impossible to miss. Silence used to look identical to a decision.
    """
    corpus_keys = set(citekeys.values())
    d = guards.disposition(narrative, corpus_keys, rejected)
    title_by_key = {citekeys[i]: c.title for i, c in enumerate(corpus) if i in citekeys}
    payload = {
        "metrics": guards.metrics(narrative, corpus_keys, rejected).__dict__,
        "cited": sorted(d.cited),
        "rejected": {k: rejected[k] for k in sorted(d.rejected)},
        "unplaced": {k: title_by_key.get(k, "") for k in sorted(d.unplaced)},
    }
    out = paths.work / "disposition.json"
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


# ──────────────────────────────────────────────────────────────────────────
# Orchestration
# ──────────────────────────────────────────────────────────────────────────
def run(directory: str = ".", brain_override: str | None = None,
        from_folder: bool = False, refresh_notes: bool = True) -> int:
    cfg = config.load_project(directory)
    gc = config.load_global()
    paths = config.project_paths(directory).ensure()
    brain = Brain(cfg.brain, gc, backend_override=brain_override)

    print(f"rabbitHole report — {cfg.project_name}")
    print(f"  brain: {brain.backend} "
          f"(coordinator={cfg.brain.coordinator_model}, worker={cfg.brain.worker_model})")
    print()

    # Train style profile if needed before anything else.
    if cfg.use_style:
        from . import style as _style
        if _style.needs_training(cfg.style_paper_keys):
            print("[style] Training style profile before synthesis…")
            result = _style.run(directory)
            if result != 0:
                print("[style] Training failed — continuing without style profile.",
                      file=sys.stderr)
            print()

    corpus = corpus_mod.build(cfg, gc, paths, from_folder=from_folder)
    if not corpus:
        print("\nNo usable sources with full text. Add PDFs to Zotero / ./pdfs/ "
              "and re-run.")
        return 1
    print(f"\nCorpus: {len(corpus)} sources with full text.")
    # Honour Zotero's Better BibTeX keys even when they aren't pinned to the Extra field
    # (the common case): source them from the collection's BibTeX export. No-op when the
    # keys were already captured at ingest, or Zotero is unavailable.
    if corpus_mod.backfill_citekeys(cfg, gc, paths, corpus):
        corpus_mod.persist(paths, corpus)
    citekeys = _make_citekeys(corpus)
    t0 = runlog.start()

    collection = None
    if _HAVE_CHROMA:
        try:
            collection = _chroma.get_collection(paths.work / "chroma")
            print(f"  ChromaDB ready at {paths.work / 'chroma'}")
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] ChromaDB unavailable ({e}) — locate will use head-truncation",
                  file=sys.stderr)
    else:
        print("  [info] chromadb not installed — run: pip install 'rabbithole[rag]'")

    print(f"\n{_stamp()}[1/3] Reading papers (notes for synthesis)...")
    notes = read_notes(brain, corpus, cfg, paths, collection=collection,
                       citekeys=citekeys, refresh_notes=refresh_notes)

    print(f"\n{_stamp()}[2/3] Synthesising the review...")
    style_profile = ""
    if cfg.use_style:
        from .style import load_style_profile
        style_profile = load_style_profile()
        if style_profile:
            print(f"  Style profile loaded ({len(style_profile)} chars).")
        else:
            print("  [note] use_style=true but no style_profile.md found; "
                  "run 'rabbitHole style' to train one.")
    narrative, rejected = synthesize(brain, corpus, notes, cfg, citekeys, style_profile)
    _write_disposition(paths, corpus, citekeys, narrative, rejected)

    print(f"\n{_stamp()}[3/3] Locating claims for the annotated bibliography "
          f"(full curated corpus)...")
    located = locate_claims(brain, narrative, corpus, notes, cfg, paths,
                            collection=collection, citekeys=citekeys, scope="all")

    biblio = bibliography(corpus, located,
                          cited_indices=set(_cited_indices(narrative, citekeys)))
    unmatched = citation_check(narrative, citekeys)
    if unmatched:
        print(f"\n[citation check] {len(unmatched)} citekey(s) not matched to a source: "
              f"{', '.join(f'[@{k}]' for k in unmatched[:8])}"
              + (" ..." if len(unmatched) > 8 else ""))

    out_md, out_docx = render.write_review(
        cfg, paths, brain.backend, narrative, biblio, corpus, unmatched,
        metrics_line=guards.metrics(narrative, set(citekeys.values()), rejected).line())

    bib_path = _export_bibtex(cfg, gc, paths, citekeys, corpus)

    elapsed = time.time() - t0
    print("\n" + "=" * 60)
    print(f" report complete  [{_fmt_dt(elapsed)}]")
    print("=" * 60)
    print(f"  Review (md)  : {out_md}")
    if out_docx:
        print(f"  Review (docx): {out_docx}")
    if bib_path:
        print(f"  BibTeX       : {bib_path}")
    print(f"  Sources read: {len(corpus)} | cited & annotated: {len(located)} "
          f"| brain: {brain.backend}")

    _notify_report_done(cfg, gc, paths, corpus, located, out_md, out_docx,
                        brain.backend, unmatched, elapsed)
    return 0


def _notify_report_done(cfg, gc, paths, corpus, located, out_md, out_docx,
                        backend, unmatched, elapsed: float) -> None:
    from . import notify
    lines = [
        f"Runtime: {_fmt_dt(elapsed)}",
        "",
        f"report complete for '{cfg.project_name}'.",
        "",
        f"Topic: {cfg.topic}",
        f"Focus: {cfg.focus or '(none)'}",
        "",
        "Results",
        f"  Sources in corpus: {len(corpus)}",
        f"  Cited & annotated: {len(located)}",
        f"  Brain: {backend}",
    ]
    if unmatched:
        lines.append(f"  Unmatched citekeys: {', '.join(f'[@{k}]' for k in unmatched[:8])}"
                     + (" ..." if len(unmatched) > 8 else ""))
    lines += [
        "",
        f"Review (md)  : {out_md}",
    ]
    if out_docx:
        lines.append(f"Review (docx): {out_docx}")
    notify.send_email(f"rabbitHole: report complete for '{cfg.project_name}'",
                      "\n".join(lines), gc)

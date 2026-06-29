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
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

from . import config, corpus as corpus_mod, render, runlog
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


def read_notes(brain: Brain, corpus: list[Candidate], cfg, paths,
               collection=None, citekeys: dict[int, str] | None = None,
               refresh_notes: bool = True) -> list[dict]:
    citekeys = citekeys or {}
    d = paths.annotations_dir
    d.mkdir(parents=True, exist_ok=True)
    notes: list[dict] = [None] * len(corpus)  # type: ignore
    done = 0
    refreshed = 0
    t_step = time.time()
    for i, c in enumerate(corpus):
        fp = d / f"{i:03d}.json"
        is_refresh = False
        if fp.exists():
            cached = json.loads(fp.read_text(encoding="utf-8"))
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
        ck = citekeys.get(i) or f"{i:03d}"
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
            raw = brain.coordinator(_read_prompt(c, cfg.topic, cfg.focus, text),
                                    READ_SYS)
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
    print(f"  notes ready: {done}/{len(corpus)}{extra}  "
          f"[{_fmt_dt(time.time() - t_step)}]")
    return notes


# ──────────────────────────────────────────────────────────────────────────
# 2. synthesize (REDUCE)
# ──────────────────────────────────────────────────────────────────────────
SYNTH_SYS = """\
You are writing the narrative section of a scholarly literature review.

STRUCTURE
- Organise around IDEAS, not sources. The unit of the review is an idea about the project's topic and focus, traceable to the source(s) that support it. Sources are evidence for ideas, never a catalogue to march through. You are NOT required to use every source you gathered — drop any that does not serve an idea worth making here. A tight argument on fewer sources beats a roll-call of all of them.
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

_LINT_SYS = """\
You are a copy-editor auditing a literature-review narrative for MECHANICAL defects
only. Respond with ONLY a numbered list of specific, actionable problems, one per
line, each quoting the offending text. If there are none, respond "OK"."""

_LINT_PROMPT = """\
Audit this narrative for mechanical defects only. Do NOT comment on argument,
coverage, or substance — a separate reviewer handles those.

Narrative:
{narrative}

Check:
1. Citation format — flag (a) any citation not in [@citekey] form (e.g. "(Smith, 2021)"
   or "Smith (2021)"); (b) any sentence where the citation opens or precedes the claim
   instead of following it. Quote each offending sentence.
2. Filler phrases — flag any of: "it is worth noting", "this highlights",
   "further work is needed", "a growing body of research", "underscores",
   "plays a crucial role", "rests on the demonstration that", "has been shown to",
   or similar content-free phrases.
3. Section headings — flag any "## " heading that joins multiple concepts with a
   comma, "and", or "/". Each heading must name exactly one idea.
4. Section order — flag any section that opens with a recent (post-2020) or preprint
   source before citing the foundational or well-cited work on that theme.
5. Uncited paragraphs — flag any body paragraph (prose under a "## " heading, not the
   heading itself) that contains no [@citekey] citation anywhere in it. Quote its first
   sentence. Every paragraph must cite at least one source.

Output: numbered list with quoted text; skip checks with no issues. If all pass, "OK"."""

_SUBSTANCE_SYS = """\
You are a demanding peer reviewer reading a literature-review narrative for the
QUALITY OF ITS ARGUMENT. Ignore formatting, citation style, and typography — a
copy-editor handles those. Respond with ONLY a numbered list of specific, actionable
problems, one per line, quoting the text you mean. If the narrative is genuinely
strong, respond "OK"."""

_SUBSTANCE_PROMPT = """\
Review topic: {topic}
Focus: {focus}

Evidence digest (ground truth — what the narrative draws on):
{digest}

Narrative under review:
{narrative}

Judge:
1. Annotated bibliography — flag any section that merely reports a source's findings
   instead of developing an idea about the project, or that runs a single paragraph per
   source. The test is whether ideas are woven into a claim about the topic/focus — NOT
   how many sources are cited. Name the theme a thin section should merge into. One
   short report-per-source section is the primary failure to catch.
2. Synthesis vs summary — flag any paragraph that walks through sources one at a time
   instead of connecting their ideas around a shared point.
3. Claim support — flag any claim not backed by the digest, or that overstates what
   its cited source actually shows. Quote the claim and name the mismatch.
4. Significance — flag any source the narrative DOES cite whose relevance to THIS
   topic/focus is asserted hollowly or left implicit. Every source you include should
   earn its place by advancing an idea; say where one does not.
5. Missed ideas — name any idea in the digest that genuinely bears on the project's
   topic/focus but the narrative never develops. Do NOT flag a source merely for being
   uncited: using every source is not required, and dropping one that serves no
   project-relevant idea is correct.
6. Specific gaps — flag any gap, tension, or future direction stated vaguely
   ("more research is needed", "warrants further study") rather than as a named,
   load-bearing gap.
7. Coherence — flag breaks in the through-line: sections that do not build on each
   other, or a theme raised and then dropped.
8. Unquantified findings — flag any claim stated only directionally ("improves",
   "increases", "is more effective") when the digest gives a magnitude for it. Quote
   the claim and name the number from the digest that should appear in it. Vague words
   ("significantly", "substantially") standing in for an available figure are defects.

Output: numbered list with quoted text; skip checks with no issues. If strong, "OK"."""

_REVISE_FROM_CRITIQUE_PROMPT = """\
Revise the narrative to fix every problem in the critique below. Preserve all correct
content; change only what the critique flags. Keep [@citekey] citation format throughout.

Review topic: {topic}
Focus: {focus}

Evidence digest:
{digest}

Current narrative:
{narrative}

Problems to fix:
{critique}

Output only the revised narrative — no preamble or explanation."""

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

# Matches a pandoc citation tag, e.g. [@schelling1971]. Used to verify every body
# paragraph carries at least one source — the "weave the argument" rule turned, without
# this guard, into citation-free transition/conclusion paragraphs.
_CITE_TAG_RE = re.compile(r"\[@[^\]\s]+\]")


def _is_ok(text: str) -> bool:
    return text.strip().upper().startswith("OK")


def _uncited_paragraphs(narrative: str, max_snippet: int = 160) -> list[str]:
    """Body paragraphs (heading lines stripped) that contain no [@citekey] citation.

    Returns a short snippet of each offender, for use as a critique item or in the
    grounding backstop. Headings, blank lines, and pure-heading blocks are ignored."""
    out: list[str] = []
    for block in re.split(r"\n\s*\n", narrative):
        prose = "\n".join(ln for ln in block.splitlines()
                          if not ln.lstrip().startswith("#")).strip()
        if not prose or _CITE_TAG_RE.search(prose):
            continue
        snippet = " ".join(prose.split())
        out.append(snippet[:max_snippet] + ("…" if len(snippet) > max_snippet else ""))
    return out


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
                SYNTH_SYS, num_ctx=16384)  # think on
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] citation enforcement failed ({e}); keeping current.",
                  file=sys.stderr)
            break
    return narrative


def _critique_revise_synthesis(brain: Brain, narrative: str, digest: str,
                                topic: str, focus: str,
                                rounds: int | None = None) -> str:
    """Iterate a two-pass critique→revise loop on the synthesis narrative.

    Each round runs a mechanical LINT pass (no reasoning) and a substantive
    PEER-REVIEW pass (reasoning on), then a single revise applying the union of
    both critiques. Stops early when both passes return "OK", or after `rounds`
    rounds (default: brain.cfg.critique_rounds)."""
    if rounds is None:
        rounds = max(1, int(getattr(brain.cfg, "critique_rounds", 2)))

    for r in range(1, rounds + 1):
        tag = f" (round {r}/{rounds})" if rounds > 1 else ""

        print(f"  {_stamp()}Critiquing synthesis — lint{tag}...", flush=True)
        try:
            lint = brain.coordinator(
                _LINT_PROMPT.format(narrative=narrative),
                _LINT_SYS, num_ctx=16384, think=False)
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] lint critique failed ({e}); skipping.", file=sys.stderr)
            lint = "OK"

        print(f"  {_stamp()}Critiquing synthesis — peer review{tag}...", flush=True)
        try:
            substance = brain.coordinator(
                _SUBSTANCE_PROMPT.format(
                    topic=topic, focus=focus, digest=digest, narrative=narrative),
                _SUBSTANCE_SYS, num_ctx=16384)  # think on (coordinator default)
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] peer-review critique failed ({e}); skipping.", file=sys.stderr)
            substance = "OK"

        # Deterministic guard: no body paragraph may be citation-free. Folded in here
        # so the reviser fixes it alongside everything else, and so a clean lint can't
        # let an uncited paragraph slip through to early-exit.
        uncited = _uncited_paragraphs(narrative)
        if uncited:
            print(f"  {_stamp()}[guard] {len(uncited)} paragraph(s) lack a citation "
                  f"— forcing revision.", flush=True)

        if _is_ok(lint) and _is_ok(substance) and not uncited:
            print(f"  {_stamp()}Critique clean — no further revision needed.", flush=True)
            break

        parts = []
        if not _is_ok(lint):
            parts.append("MECHANICAL:\n" + lint.strip())
        if uncited:
            listing = "\n".join(f'- "{p}"' for p in uncited)
            parts.append(
                "UNCITED PARAGRAPHS — every paragraph must cite at least one source. "
                "Ground each of these in the source(s) for its ideas (in [@citekey] form "
                "from the digest) or merge it into an adjacent cited paragraph; do not "
                "leave a transition- or conclusion-only paragraph:\n" + listing)
        if not _is_ok(substance):
            parts.append("SUBSTANTIVE:\n" + substance.strip())
        critique = "\n\n".join(parts)

        print(f"  {_stamp()}Revising synthesis{tag}...", flush=True)
        try:
            narrative = brain.coordinator(
                _REVISE_FROM_CRITIQUE_PROMPT.format(
                    topic=topic, focus=focus, digest=digest,
                    narrative=narrative, critique=critique),
                SYNTH_SYS, num_ctx=16384)  # think on
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] synthesis revision failed ({e}); keeping current.",
                  file=sys.stderr)
            break

    # Hard backstop: if the loop ran out of rounds with any paragraph still
    # citation-free, force-ground them before returning.
    return _enforce_paragraph_citations(brain, narrative, digest)


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


def synthesize(brain: Brain, corpus: list[Candidate], notes: list[dict], cfg,
               citekeys: dict[int, str], style_profile: str = "") -> str:
    digest = _digest(corpus, notes, citekeys)
    n = len(corpus)
    target_sections = max(3, min(8, round(n / 4)))
    prompt = (f"Review topic: {cfg.topic}\nFocus: {cfg.focus}\n"
              f"Number of sources: {n}\n\n"
              f"Evidence digest (one line per source):\n{digest}\n\n"
              f"Write the thematic narrative review now. Organise it around ideas about "
              f"the project, not around the {n} sources — you need not use every source; "
              f"drop any that serves no project-relevant idea. Aim for about "
              f"{target_sections} thematic sections; each develops a few related ideas "
              f"across at least three paragraphs that ACCRETE — every paragraph brings in "
              f"new sources and ties them to the ideas already raised, so every paragraph "
              f"cites at least one source — weaving toward what those ideas mean for the "
              f"project's focus.")
    sys_prompt = SYNTH_SYS
    if style_profile:
        sys_prompt = (sys_prompt.rstrip()
                      + f"\n\nWRITING STYLE\nMatch the following author's voice and "
                        f"prose style throughout:\n{style_profile}")
    print(f"  {_stamp()}Synthesising narrative (coordinator)...", flush=True)
    narrative = brain.coordinator(prompt, sys_prompt, num_ctx=16384)
    return _critique_revise_synthesis(brain, narrative, digest, cfg.topic, cfg.focus or "")


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


def _all_citekeys(text: str) -> list[str]:
    """Every individual citekey, splitting grouped citations like [@a; @b; @c].

    The naive [@([^\\]]+)] capture treats a grouped bracket as one key, so any source
    cited only inside a multi-citation bracket would be dropped from locate and the
    bibliography. Split on ';' and strip the leading '@' to recover each key."""
    keys: list[str] = []
    for grp in re.findall(r"\[@([^\]]+)\]", text):
        for part in grp.split(";"):
            k = part.strip().lstrip("@").strip()
            if k:
                keys.append(k)
    return keys


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
                  citekeys: dict[int, str] | None = None) -> dict[int, list]:
    citekeys = citekeys or {}
    located_dir = paths.work / "located"
    located_dir.mkdir(parents=True, exist_ok=True)
    cited = _cited_indices(narrative, citekeys)
    print(f"  {_stamp()}{len(cited)} of {len(corpus)} sources are cited "
          f"— locating their claims...", flush=True)
    located: dict[int, list] = {}
    t_step = time.time()
    for i in cited:
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
                raw = brain.coordinator(_locate_prompt(c, statements, body), LOCATE_SYS)
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
def bibliography(corpus: list[Candidate], located: dict[int, list]) -> str:
    order = sorted(located.keys(), key=lambda i: corpus[i].first_author_last.lower())
    out = ["## Annotated Bibliography", ""]
    for i in order:
        c = corpus[i]
        out.append(f"**{c.full_citation()}**")
        out.append("")
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
                out.append(line)
        else:
            out.append("- *(no supporting passage found in this source's full text "
                       "— verify the citation against the original)*")
        out.append("")
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
        from .style import STYLE_PROFILE_PATH, _load_existing_meta
        existing_meta = _load_existing_meta()
        existing_keys = set(existing_meta.get("paper_keys", []))
        confirmed_keys = set(cfg.style_paper_keys or [])
        needs_training = (not STYLE_PROFILE_PATH.exists()
                          or (confirmed_keys and not confirmed_keys.issubset(existing_keys)))
        if needs_training:
            print("[style] Training style profile before synthesis…")
            from . import style as _style
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
    narrative = synthesize(brain, corpus, notes, cfg, citekeys, style_profile)

    print(f"\n{_stamp()}[3/3] Locating cited claims for the annotated bibliography...")
    located = locate_claims(brain, narrative, corpus, notes, cfg, paths,
                            collection=collection, citekeys=citekeys)

    biblio = bibliography(corpus, located)
    unmatched = citation_check(narrative, citekeys)
    if unmatched:
        print(f"\n[citation check] {len(unmatched)} citekey(s) not matched to a source: "
              f"{', '.join(f'[@{k}]' for k in unmatched[:8])}"
              + (" ..." if len(unmatched) > 8 else ""))

    out_md, out_docx = render.write_review(
        cfg, paths, brain.backend, narrative, biblio, corpus, unmatched)

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

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
from collections import defaultdict
from pathlib import Path

from . import config, corpus as corpus_mod, render
from .brain import Brain
from .models import Candidate, norm_doi
from .pdfs import page_marked_text


def _make_citekeys(corpus: list[Candidate]) -> dict[int, str]:
    """Map corpus index → {last}{year} BibTeX citekey, with a/b/c disambiguation."""
    def _base(c: Candidate) -> str:
        last = re.sub(r"[^a-z0-9]", "", c.first_author_last.lower())
        return f"{last}{c.year}" if c.year else f"{last}nd"

    groups: dict[str, list[int]] = defaultdict(list)
    for i, c in enumerate(corpus):
        groups[_base(c)].append(i)

    keys: dict[int, str] = {}
    for base, indices in groups.items():
        if len(indices) == 1:
            keys[indices[0]] = base
        else:
            for j, i in enumerate(indices):
                keys[i] = base + "abcdefghijklmnopqrstuvwxyz"[j]
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


def _fmt_dt(secs: float) -> str:
    s = int(secs)
    h, r = divmod(s, 3600)
    m, s = divmod(r, 60)
    return f"{h}h {m}m {s}s" if h else (f"{m}m {s}s" if m else f"{s}s")


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
  "findings": "key findings (1-2 sentences)",
  "limitations": "stated or evident limitations (1 sentence, or empty)",
  "relevance": "why this paper matters for the review — what it specifically contributes or enables (1 sentence)",
  "gaps": "a genuine, load-bearing gap WITHIN the paper's own scope that a reader would expect it to cover but it does not. Leave empty if the only 'gaps' are topics outside the paper's discipline (1 sentence, or empty)",
  "themes": ["3-6 short theme tags"]
}
Base everything strictly on the provided text. Do not invent.
Write each field in plain English; paraphrase rather than copying technical phrases verbatim."""


def _read_prompt(c: Candidate, topic: str, focus: str, body: str) -> str:
    return (f"Review topic: {topic}\nFocus: {focus}\n\n"
            f"Paper: {c.title} ({c.author_year()})\nVenue: {c.venue}\n\n"
            f"--- PAPER TEXT ---\n{body}\n--- END ---\n\nJSON:")


def _condense(brain: Brain, text: str) -> str:
    """Map step for long papers: summarise chunks, then concatenate."""
    chunks = _chunk(text, _MAP_CHUNK_CHARS)
    sys = ("Extract the key points (argument, methods, findings, notable quotes "
           "with page markers) from this section. Be concise.")
    jobs = [(sys, f"Section:\n{ch}\n\nKey points:") for ch in chunks]
    parts = brain.worker_map(jobs, num_ctx=8192)
    return "\n\n".join(parts)


def read_notes(brain: Brain, corpus: list[Candidate], cfg, paths,
               collection=None) -> list[dict]:
    d = paths.annotations_dir
    d.mkdir(parents=True, exist_ok=True)
    notes: list[dict] = [None] * len(corpus)  # type: ignore
    done = 0
    t_step = time.time()
    for i, c in enumerate(corpus):
        fp = d / f"{i:03d}.json"
        if fp.exists():
            notes[i] = json.loads(fp.read_text(encoding="utf-8"))
            done += 1
            continue
        label = f"{c.first_author_last} {c.year or ''}".strip()
        print(f"  [{i + 1}/{len(corpus)}] {label}", end="", flush=True)
        text = _paper_text(c)
        # Index full page-marked text in ChromaDB before condensing for notes
        if collection is not None and not _chroma.is_paper_indexed(collection, i):
            n_chunks = _chroma.index_paper(collection, brain, i, text)
            print(f"  ({n_chunks} chunks indexed)", end="", flush=True)
        print(flush=True)
        if len(text) > _DIRECT_CHARS:
            if collection is not None:
                queries = [
                    f"{cfg.topic} {cfg.focus} main argument contribution hypothesis",
                    "methodology research design data collection analysis",
                    "results findings outcomes evidence limitations",
                ]
                text = _chroma.query_paper_multi(collection, brain, i, queries,
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
            fp.write_text(json.dumps(note, indent=2, ensure_ascii=False),
                          encoding="utf-8")  # write only on success -> resumable
            done += 1
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] read failed for {c.first_author_last} {c.year or ''}: {e} "
                  f"(will retry on next run)", file=sys.stderr)
            note = {"argument": "", "methods": "", "findings": "", "limitations": "",
                    "relevance": "", "themes": [], "_paper": c.author_year()}
        notes[i] = note
    print(f"  notes ready: {done}/{len(corpus)}  [{_fmt_dt(time.time() - t_step)}]")
    return notes


# ──────────────────────────────────────────────────────────────────────────
# 2. synthesize (REDUCE)
# ──────────────────────────────────────────────────────────────────────────
SYNTH_SYS = """\
You are writing the narrative section of a scholarly literature review.

STRUCTURE
- Organise into thematic sections that tell a coherent story. Fit sections to the material; do not use a fixed template.
- SYNTHESISE across sources — compare, contrast, and connect them. Do NOT summarise one paper at a time.
- Each "## " heading names ONE idea in <=6 words. Never join concepts with commas or "and" (avoid "Dimensionality, Complexity, and Temporal Evolution"). If a section spans several ideas, split it or pick the single organising idea.
- Open each section with established, well-cited work (high citation counts in the digest) before recent work or preprints; prefer the peer-reviewed article over a preprint when both support a claim.

EXPLAIN WHY IT MATTERS (the priority)
- For every claim, make plain why it matters to THIS review's topic and focus. Use the Relevance note in the digest to connect each source to the project's goal.
- State significance positively: what a source contributes or enables. Only name a gap when it is genuine and load-bearing for the topic. Do NOT note that a source omits a field outside its discipline (e.g. a musicology paper "did not use agent-based modeling") — that is obvious and adds nothing.
- Name gaps, tensions, and directions specifically; never "further work is needed".

CITATIONS
- State each finding as a claim in its own right, then attach the source in square brackets immediately after: "Modest tolerance thresholds produce exaggerated segregation [@schelling1971]."
- Never make a citation the grammatical subject or agent of a sentence. Do NOT write "[@schelling1971] showed that...". Rewrite so the claim leads and the citation follows.
- Use the citekey EXACTLY as given in the digest (the [@key] tag beside each source). Do not invent or alter citekeys.

LANGUAGE
- Paraphrase; do not lift technical phrases verbatim from the sources. A reader should not need the original papers' vocabulary to follow your argument.
- When a field-specific term is genuinely needed — no plain equivalent carries the same precision — introduce it once with a brief gloss in parentheses: "...using principal component analysis (a technique that compresses many correlated variables into a smaller, uncorrelated set)..."
- Prefer concrete, active verbs. "The study found that participants who received X showed Y" beats "an association between X and Y was observed".
- Write for an intelligent reader who is not already expert in this exact sub-field. They can follow careful reasoning but should not be expected to know domain acronyms or insider shorthand on sight.

STYLE
- Be tight. One main idea per sentence; at most one subordinate clause. Prefer plain verbs over nominalisations.
- Keep paragraphs to 3-5 sentences. Target 700-1000 words total.
- Cut filler: "it is worth noting", "this highlights", "rests on the demonstration that", "underscores that", "a growing body of research". Every sentence adds a new fact or connection.
- Use "## " headings for themes. Do not write a bibliography (that is added separately).

Write only the narrative review."""

_SYNTH_CRITIQUE_SYS = """\
You audit a literature review narrative for quality problems. Respond with ONLY a
numbered list of specific, actionable problems, one per line. If no problems, respond "OK"."""

_SYNTH_CRITIQUE_PROMPT = """\
Review topic: {topic}
Focus: {focus}

Evidence digest (ground truth — what the narrative should cover):
{digest}

Narrative to audit:
{narrative}

Check:
1. Citation format — flag: (a) any citation that does not use [@citekey] format
   (e.g., "(Smith, 2021)" or "Smith (2021)" are wrong); (b) any sentence where
   a citation bracket opens or precedes the claim rather than following it. All
   citations must be [@citekey] and follow their claim. Quote each offending sentence.
2. Filler phrases — flag any of: "it is worth noting", "this highlights",
   "further work is needed", "a growing body of research", "underscores",
   "plays a crucial role", "rests on the demonstration that", "has been shown to",
   or similar content-free phrases.
3. Section headings — flag any heading that joins multiple concepts with commas,
   "and", or "/". Each heading must name exactly one idea.
4. Coverage — list any source from the digest that has a non-empty argument or
   findings line but is never cited in the narrative by [@citekey]. Only flag
   sources whose content is substantively relevant to the topic.
5. Vague gap statements — flag any mention of gaps, limitations, or future
   directions that is not a specific, named gap or tension. "More research is
   needed" and "this area warrants further study" are always too vague.
6. Section order — flag any thematic section that opens with a recent (post-2020)
   or preprint source before citing the foundational or well-cited work on that theme.

Output: numbered list of problems with quoted text. Skip checks with no issues.
If all checks pass, respond "OK"."""

_SYNTH_REVISE_PROMPT = """\
Revise the narrative to fix every problem in the critique below.
Preserve all correct content; only change what the critique flags.

Review topic: {topic}
Focus: {focus}

Evidence digest:
{digest}

Current narrative:
{narrative}

Problems to fix:
{critique}

Output only the revised narrative — no preamble or explanation."""


def _critique_revise_synthesis(brain: Brain, narrative: str, digest: str,
                                topic: str, focus: str) -> str:
    """One critique→revise cycle on the synthesis narrative."""
    print("  Critiquing synthesis...", flush=True)
    try:
        critique = brain.coordinator(
            _SYNTH_CRITIQUE_PROMPT.format(
                topic=topic, focus=focus, digest=digest, narrative=narrative),
            _SYNTH_CRITIQUE_SYS, num_ctx=16384)
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] synthesis critique failed ({e}); keeping original.",
              file=sys.stderr)
        return narrative
    if critique.strip().upper().startswith("OK"):
        return narrative
    print("  Revising synthesis...", flush=True)
    try:
        revised = brain.coordinator(
            _SYNTH_REVISE_PROMPT.format(
                topic=topic, focus=focus, digest=digest,
                narrative=narrative, critique=critique),
            SYNTH_SYS, num_ctx=16384)
        return revised
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] synthesis revision failed ({e}); keeping original.",
              file=sys.stderr)
        return narrative


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
               citekeys: dict[int, str]) -> str:
    digest = _digest(corpus, notes, citekeys)
    prompt = (f"Review topic: {cfg.topic}\nFocus: {cfg.focus}\n"
              f"Number of sources: {len(corpus)}\n\n"
              f"Evidence digest (one line per source):\n{digest}\n\n"
              f"Write the thematic narrative review now.")
    print("  Synthesising narrative (coordinator)...", flush=True)
    narrative = brain.coordinator(prompt, SYNTH_SYS, num_ctx=16384)
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


def _cited_indices(narrative: str, citekeys: dict[int, str]) -> list[int]:
    """Return corpus indices whose [@citekey] tags appear in the narrative."""
    found = set(re.findall(r"\[@([^\]]+)\]", narrative))
    key_to_idx = {v: k for k, v in citekeys.items()}
    return [key_to_idx[k] for k in found if k in key_to_idx]


def _claim_sentences(narrative: str, citekey: str) -> str:
    """Sentences in the narrative that cite this citekey."""
    tag = f"[@{citekey}]"
    return " ".join(
        s.strip() for s in re.split(r"(?<=[.!?])\s+", narrative) if tag in s)


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
    print(f"  {len(cited)} of {len(corpus)} sources are cited — locating their claims...",
          flush=True)
    located: dict[int, list] = {}
    t_step = time.time()
    for i in cited:
        c = corpus[i]
        fp = located_dir / f"{i:03d}.json"
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
            if not _chroma.is_paper_indexed(collection, i):
                print(f"  indexing {label} for retrieval...", flush=True)
                _chroma.index_paper(collection, brain, i, _paper_text(c))
            print(f"  locating {label}  (embedding retrieval)", flush=True)
            items = _chroma.locate_direct(collection, brain, i, statements)
        else:
            # Fallback: LLM-based locate when ChromaDB unavailable
            print(f"  locating {label}  (LLM fallback)", flush=True)
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
            out.append("- *(cited in the review; specific passages not located)*")
        out.append("")
    return "\n".join(out)


def citation_check(narrative: str, citekeys: dict[int, str]) -> list[str]:
    """[@citekey] tags in the narrative that don't map to a corpus item."""
    known = set(citekeys.values())
    found = set(re.findall(r"\[@([^\]]+)\]", narrative))
    return sorted(found - known)


# ──────────────────────────────────────────────────────────────────────────
# BibTeX export
# ──────────────────────────────────────────────────────────────────────────
def _patch_bibtex_keys(bib_text: str, key_by_doi: dict[str, str],
                        key_by_title: dict[str, str]) -> str:
    """Replace Zotero-generated citekeys with our {last}{year} keys."""
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
    """Fetch BibTeX from Zotero, patch citekeys to match ours, write output/refs.bib."""
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
        from_folder: bool = False) -> int:
    cfg = config.load_project(directory)
    gc = config.load_global()
    paths = config.project_paths(directory).ensure()
    brain = Brain(cfg.brain, gc, backend_override=brain_override)

    # Version stamp so re-init (new focus) never overwrites the previous review.
    cfg_file = config.latest_project_file(directory)
    cfg_version = config._project_number(cfg_file.name) if cfg_file else 1

    print(f"rabbitHole report — {cfg.project_name}")
    print(f"  brain: {brain.backend} "
          f"(coordinator={cfg.brain.coordinator_model}, worker={cfg.brain.worker_model})")
    print()

    corpus = corpus_mod.build(cfg, gc, paths, from_folder=from_folder)
    if not corpus:
        print("\nNo usable sources with full text. Add PDFs to Zotero / ./pdfs/ "
              "and re-run.")
        return 1
    print(f"\nCorpus: {len(corpus)} sources with full text.")
    citekeys = _make_citekeys(corpus)
    t0 = time.time()

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

    print("\n[1/3] Reading papers (notes for synthesis)...")
    notes = read_notes(brain, corpus, cfg, paths, collection=collection)

    print("\n[2/3] Synthesising the review...")
    narrative = synthesize(brain, corpus, notes, cfg, citekeys)

    print("\n[3/3] Locating cited claims for the annotated bibliography...")
    located = locate_claims(brain, narrative, corpus, notes, cfg, paths,
                            collection=collection, citekeys=citekeys)

    biblio = bibliography(corpus, located)
    unmatched = citation_check(narrative, citekeys)
    if unmatched:
        print(f"\n[citation check] {len(unmatched)} citekey(s) not matched to a source: "
              f"{', '.join(f'[@{k}]' for k in unmatched[:8])}"
              + (" ..." if len(unmatched) > 8 else ""))

    out_md, out_docx = render.write_review(
        cfg, paths, brain.backend, narrative, biblio, corpus, unmatched,
        cfg_version=cfg_version)

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

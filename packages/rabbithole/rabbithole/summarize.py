"""report — read the corpus, synthesize the review, then locate the cited claims.

Pipeline (annotation is a POST-synthesis product, not an independent per-paper step):
  1. read (MAP)    : per-paper notes for synthesis. Sequential, written to disk one
                     paper at a time -> a slow run is resumable and shows progress
                     (work/annotations/NNN.json).
  2. synthesize    : the coordinator (27B) writes the thematic narrative with plain
                     author-year in-text citations, from a digest of the notes.
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

from . import config, corpus as corpus_mod, render
from .brain import Brain
from .models import Candidate
from .pdfs import page_marked_text

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
  "relevance": "how it relates to the review topic (1 sentence)",
  "themes": ["3-6 short theme tags"]
}
Base everything strictly on the provided text. Do not invent."""


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
                # codePixie pattern: 3 embed calls (fast) replace N LLM chunk summaries
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
Requirements:
- Organise into thematic sections that tell a coherent story about the state of knowledge. Design the sections to fit the material; do not use a rigid template.
- SYNTHESISE across sources — compare, contrast, and connect them. Do NOT summarise one paper at a time.
- Anchor the narrative in the established scholarly conversation. Open each thematic section by situating it within the foundational literature: cite the most influential or well-established works (indicated by high citation counts in the digest) before moving to recent developments or preprints.
- When multiple sources support the same claim, prefer the peer-reviewed journal article over a preprint or grey literature source.
- Use author-year in-text citations exactly as given in the digest, e.g. (Smith & Jones, 2021).
- Always use full parenthetical citations: (Author, Year) or (Author et al., Year). Never use narrative form such as "Smith (2021) showed...".
- Explicitly identify gaps, tensions/disagreements, and emerging directions.
- Use Markdown headings (##) for themes. Do not write a bibliography (that is added separately).
- Be concise. Target 800-1200 words total. Every sentence must add new information or connection; cut anything that restates what the previous sentence already said.
- Prefer precise, specific claims over hedged generalities. Avoid throat-clearing phrases like "it is worth noting", "this highlights the importance of", or "a growing body of research".
Write only the narrative review."""


def _digest(corpus: list[Candidate], notes: list[dict]) -> str:
    lines = []
    for c, a in zip(corpus, notes):
        themes = ", ".join(a.get("themes", []))
        cites = f" [{c.cited_by_count} citations]" if c.cited_by_count else ""
        lines.append(
            f"- ({c.author_year()}){cites} {a.get('argument','')} "
            f"Findings: {a.get('findings','')} Themes: {themes}".strip())
    return "\n".join(lines)


def synthesize(brain: Brain, corpus: list[Candidate], notes: list[dict], cfg) -> str:
    digest = _digest(corpus, notes)
    prompt = (f"Review topic: {cfg.topic}\nFocus: {cfg.focus}\n"
              f"Number of sources: {len(corpus)}\n\n"
              f"Evidence digest (one line per source):\n{digest}\n\n"
              f"Write the thematic narrative review now.")
    print("  Synthesising narrative (coordinator)...", flush=True)
    return brain.coordinator(prompt, SYNTH_SYS, num_ctx=16384)


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


def _narrative_cite_keys(narrative: str) -> set[tuple[str, str]]:
    keys = set()
    for m in re.finditer(r"\(([^()]*?\d{4}[a-z]?)\)", narrative):
        cite = m.group(1)
        ym = re.search(r"(\d{4})", cite)
        if not ym:
            continue
        name = re.split(r"\bet al\b|&|,|;", cite)[0].strip().lower()
        last = name.split()[-1] if name.split() else name
        if last and not last.isdigit():  # skip bare (2025) — not an author-year cite
            keys.add((last, ym.group(1)))
    return keys


def _cited_indices(narrative: str, corpus: list[Candidate]) -> list[int]:
    keys = _narrative_cite_keys(narrative)
    lasts = {k[0] for k in keys}
    out = []
    for i, c in enumerate(corpus):
        last = c.first_author_last.lower()
        yr = str(c.year) if c.year else ""
        if (last, yr) in keys or (not yr and last in lasts):
            out.append(i)
    return out


def _claim_sentences(narrative: str, c: Candidate) -> str:
    last = c.first_author_last
    yr = str(c.year) if c.year else ""
    sents = re.split(r"(?<=[.!?])\s+", narrative)
    hits = [s.strip() for s in sents if last in s and (not yr or yr in s)]
    return " ".join(hits)


def _locate_prompt(c: Candidate, statements: str, body: str) -> str:
    return (f"Cited paper: {c.title} ({c.author_year()})\n\n"
            f"Statements the review makes citing this paper:\n{statements}\n\n"
            f"--- PAPER TEXT ---\n{body}\n--- END ---\n\nJSON array:")


def locate_claims(brain: Brain, narrative: str, corpus: list[Candidate],
                  notes: list[dict], cfg, paths, collection=None) -> dict[int, list]:
    located_dir = paths.work / "located"
    located_dir.mkdir(parents=True, exist_ok=True)
    cited = _cited_indices(narrative, corpus)
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
        statements = _claim_sentences(narrative, c)
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


def citation_check(narrative: str, corpus: list[Candidate]) -> list[str]:
    """In-text cites that don't map to a corpus item (hallucination guard)."""
    known_years = {(c.first_author_last.lower(), str(c.year)) for c in corpus}
    known_last = {c.first_author_last.lower() for c in corpus}
    unmatched = []
    for m in re.finditer(r"\(([^()]*?\d{4}[a-z]?)\)", narrative):
        cite = m.group(1)
        ym = re.search(r"(\d{4})", cite)
        if not ym:
            continue
        year = ym.group(1)
        name = re.split(r"\bet al\b|&|,|;", cite)[0].strip().lower()
        last = name.split()[-1] if name.split() else name
        if last.isdigit():  # bare (2025) — not an author-year cite
            continue
        if (last, year) in known_years or last in known_last:
            continue
        unmatched.append(cite.strip())
    return sorted(set(unmatched))


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
    narrative = synthesize(brain, corpus, notes, cfg)

    print("\n[3/3] Locating cited claims for the annotated bibliography...")
    located = locate_claims(brain, narrative, corpus, notes, cfg, paths,
                            collection=collection)

    biblio = bibliography(corpus, located)
    unmatched = citation_check(narrative, corpus)
    if unmatched:
        print(f"\n[citation check] {len(unmatched)} in-text cite(s) not matched "
              f"to a source: {', '.join(unmatched[:8])}"
              + (" ..." if len(unmatched) > 8 else ""))

    out_md, out_docx = render.write_review(
        cfg, paths, brain.backend, narrative, biblio, corpus, unmatched,
        cfg_version=cfg_version)

    elapsed = time.time() - t0
    print("\n" + "=" * 60)
    print(f" report complete  [{_fmt_dt(elapsed)}]")
    print("=" * 60)
    print(f"  Review (md)  : {out_md}")
    if out_docx:
        print(f"  Review (docx): {out_docx}")
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
        lines.append(f"  Unmatched citations: {', '.join(unmatched[:8])}"
                     + (" ..." if len(unmatched) > 8 else ""))
    lines += [
        "",
        f"Review (md)  : {out_md}",
    ]
    if out_docx:
        lines.append(f"Review (docx): {out_docx}")
    notify.send_email(f"rabbitHole: report complete for '{cfg.project_name}'",
                      "\n".join(lines), gc)

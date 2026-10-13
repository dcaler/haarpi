"""`haarpi bench` — which local model should be the coordinator, and which the worker?

The same fixed, real inputs are replayed through each candidate and scored by the same
deterministic code, so the models differ and nothing else does. Read-only on every project:
fixtures and results live under ``~/haarpi-bench/`` (or ``$HAARPI_BENCH``).

  haarpi bench prepare              build the fixtures once (needs the embedder: a GPU task)
  haarpi bench run --coordinator M  every coordinator task with M as coordinator
  haarpi bench run --worker M       the worker task with M as worker
  haarpi bench run --embedders      the embedder side check (mxbai vs bge-m3)
  haarpi bench report               one HTML table of every result so far

The tasks (see PLAN_brainbench.html): A1 fact extraction, A2 topic scoring, A3 theme naming
(rabbitHole); B1 comment sorting, B2 paper tiers, B3 dispute verdicts (haarpi's planner); C1
section drafting (raconteur); W1 long-paper condensing (worker); E1 embedder separation.
A4 (narrative paragraph redline) was dropped: no recorded markup carries the corpus, notes and
evidence it would need to replay faithfully.

Each runner lane points `OLLAMA_URL` at its own card's ollama; the harness passes that URL to
every brain it builds, including the planner's (which does not read OLLAMA_URL itself yet).
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace

BENCH_ROOT = Path(os.environ.get("HAARPI_BENCH") or Path.home() / "haarpi-bench")
DIGIPROS = Path("/media/lucullus/Cale_Professional/Current_Work_Projects/260208_DigiPros")
EMBED = "mxbai-embed-large"
BASELINE_COORDINATOR = "qwen3.6:27b-16k"
BASELINE_WORKER = "llama3.1:8b"

# A1: the 11 papers that once came back empty, and 9 the review leans on.
A1_PAPERS = ["voinovModelling2016", "anzolaCapturing2021", "zuccottiExploring2023",
             "cerquetiSegregation2022", "venkateshTechnology2008", "sarkerMachine2021",
             "dwivediArtificial2021", "fongsurvey2003", "gearinoWhats2026",
             "lampertiagentbased2019", "conteManifesto2012",
             "baillieAlternative2021", "pasinFactoidbased2015", "bradleyTexts2005",
             "stoneProsopography1971", "edmondsBootstrapping2010", "dahlstromUsing2014",
             "müllerStandardised2014", "hallVisualizing2016", "shangResampling2015"]
W1_PAPERS = ["dwivediArtificial2021", "voinovModelling2016", "cerquetiSegregation2022"]
W1_CHUNKS = 4                       # per paper: bounded, so a slow worker cannot run all day

# B3: a dispute on a real fact. "source" = the sentence says what the fact says (the fact is
# disputed); "paper" = the sentence misstates it; None = not a dispute at all.
_B3_FAITHFUL = {
    "baillieAlternative2021#1": "Baillie distinguishes prosopographical indexes, which catalogue "
                                "references to persons, from secondary prosopographies, which "
                                "collate material for exploration and queries",
    "edmondsBootstrapping2010#1": "Edmonds argues that hopes for general, reliable simulation "
                                  "models of social phenomena are ill-founded, with no short-cut "
                                  "to them",
    "dahlstromUsing2014#2": "Logical-scientific communication aims at abstract truths valid across "
                            "situations, whereas narrative communication offers specific cases "
                            "from which people generalise",
    "stoneProsopography1971#4": "Prosopography is limited by the quantity and quality of its data: "
                                "biographical study of many persons is possible only for "
                                "well-documented groups",
    "shangResampling2015#1": "Shang presents two new resampling techniques for visualising the "
                             "distribution of descriptive statistics of functional data",
}
_B3_MISSTATED = {
    "baillieAlternative2021#1": "Baillie treats prosopographical indexes and secondary "
                                "prosopographies as the same kind of resource",
    "edmondsBootstrapping2010#1": "Edmonds argues that general, reliable simulation models of "
                                  "social phenomena are within easy reach",
    "dahlstromUsing2014#2": "Narrative communication aims at abstract truths valid across "
                            "situations, whereas logical-scientific communication works from "
                            "specific cases",
    "stoneProsopography1971#4": "Prosopography works equally well for poorly documented groups",
    "shangResampling2015#1": "Shang reviews existing resampling techniques without proposing new "
                             "ones",
}

# E1: 30 search-placed fact/quote pairs, judged by hand (Claude, 2026-10-13): 1 = the quote
# supports the fact, 0 = it does not. Keyed by fact id; sampled with random.seed(7).
_E1_LABELS = [0, 0, 1, 1, 1, 1, 1, 0, 0, 1, 1, 1, 0, 0, 1, 1, 0, 0, 0, 1, 1, 1, 0, 0, 1, 1,
              0, 1, 0, 1]

# C1: two literature-reading sections, beats from the DigiPros brief.
C1_SECTIONS = {
    "Background": [
        "Digital prosopography and the factoid model: sources' assertions about persons, "
        "structured as data.",
        "Record incompleteness and survival bias limit what collective biography can claim.",
        "Agent-based models generate individual life histories that can be read as "
        "prosopographical records."],
    "Related Work on Validating Agent Behaviour": [
        "Process-level validation of agent-based models, beyond matching aggregate patterns.",
        "Stochastic variation across runs, and the replication and robustness checks it needs.",
        "Documentation protocols (ODD) that make model behaviour inspectable."],
}
C1_BAND = (450, 700)


def _say(msg: str) -> None:
    print(f"[bench {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _url() -> str:
    return os.environ.get("OLLAMA_URL", "http://localhost:11434")


def _slug(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9.]+", "_", model)


# ── prepare ─────────────────────────────────────────────────────────────────────

def prepare(project: Path = DIGIPROS, out: Path | None = None) -> Path:
    """Freeze every input a candidate will see. Reads the project; writes only `out`."""
    from rabbithole import chroma, config as rhconfig
    from rabbithole.brain import Brain as RHBrain
    from rabbithole.models import Candidate
    from rabbithole.summarize import _MAP_CHUNK_CHARS, _chunk, _paper_text, reading_text

    from . import comment_kinds as ck

    out = Path(out or BENCH_ROOT / "fixtures")
    out.mkdir(parents=True, exist_ok=True)
    lit = project / "litReview"
    cfg = rhconfig.load_project(str(project))
    gc = rhconfig.load_global()
    gc.ollama_url = _url()
    brain = RHBrain(rhconfig.BrainConfig(embed_model=EMBED), gc)
    blob = json.loads((lit / "work" / "facts.json").read_text(encoding="utf-8"))
    facts = blob["facts"]
    by_ak = {}
    for f in facts:
        m = re.search(r"/items/([A-Z0-9]+)", f.get("link") or "")
        if m:
            by_ak[m.group(1)] = f["citekey"]
    corpus, records = {}, {}
    for d in json.loads((lit / "work" / "corpus.json").read_text(encoding="utf-8")):
        c = Candidate.from_dict(d)
        if by_ak.get(c.attachment_key):
            corpus[by_ak[c.attachment_key]] = c
            records[by_ak[c.attachment_key]] = d
    try:
        collection = chroma.get_collection(lit / "work" / "chroma")
    except Exception as e:  # noqa: BLE001
        _say(f"no chroma collection ({e}) — long papers read condensed instead")
        collection = None

    # A1 / W1
    a1 = []
    for key in A1_PAPERS:
        c = corpus.get(key)
        if c is None:
            _say(f"A1: {key} not in the corpus — skipped")
            continue
        full = _paper_text(c)
        reading = reading_text(brain, c, cfg, collection, key, text=full)
        a1.append({"citekey": key, "record": records[key], "label": c.author_year(),
                   "full": full, "reading": reading})
        _say(f"A1: {c.author_year()} — {len(reading):,} chars read of {len(full):,}")
    _dump(out / "a1.json", {"topic": cfg.topic, "focus": cfg.focus, "papers": a1})
    w1 = [{"citekey": p["citekey"], "chunks": _chunk(p["full"], _MAP_CHUNK_CHARS)[:W1_CHUNKS]}
          for p in a1 if p["citekey"] in W1_PAPERS]
    _dump(out / "w1.json", {"papers": w1})

    # A2: the A1 papers' current facts, with the scores they hold now
    keys = {p["citekey"] for p in a1}
    _dump(out / "a2.json", {"topics": blob.get("topics") or [],
                            "facts": [{k: f[k] for k in ("id", "citekey", "text", "scores")}
                                      for f in facts if f["citekey"] in keys and f.get("scores")]})

    # A3: the current themes of two topics, as fixed groups
    by_id = {f["id"]: f for f in facts}
    groups = []
    for th in blob.get("themes", []):
        if th.get("item") in (1, 6):
            fs = [by_id[i] for i in th.get("fact_ids", []) if i in by_id]
            if fs:
                groups.append({"topic": blob["topics"][th["item"] - 1]["name"],
                               "facts": [{"citekey": f["citekey"], "text": f["text"]}
                                         for f in fs]})
    _dump(out / "a3.json", {"groups": groups})

    # B1 / B2 / B3
    fill = lambda t: t.replace("X", "archival silences").replace("Y", "the factoid model") \
        .replace("T7", "T2").replace("…", "process-level validation only")
    b1 = [{"text": fill(t), "kind": k.name, "fact_base": fb}
          for fb, vocab in ((False, ck.LITREVIEW_NARRATIVE), (True, ck.LITREVIEW_FACTS))
          for k in vocab.kinds for t in k.triggers]
    b2 = [{"text": fill(t), "tier": k.name} for k in ck.PAPER_MANUSCRIPT.kinds
          if k.name != "dispute" for t in k.triggers]
    b3 = []
    for fid in _B3_FAITHFUL:
        f = by_id.get(fid)
        if f is None:
            continue
        base = {"fact": {"id": fid, "citekey": f["citekey"], "text": f["text"],
                         "page": f.get("page"), "quote": f.get("quote", "")}}
        cite = f" [@{f['citekey']}]."
        b3 += [dict(base, sentence=_B3_FAITHFUL[fid] + cite,
                    comment="I don't think the source says this", expect="source"),
               dict(base, sentence=_B3_MISSTATED[fid] + cite,
                    comment="check this against the source", expect="paper"),
               dict(base, sentence=_B3_FAITHFUL[fid] + cite,
                    comment="reword this, it's clunky", expect=None)]
    _dump(out / "b.json", {"b1": b1, "b2": b2, "b3": b3})

    # C1: each section's facts, chosen once (with the embedder) and then fixed
    from raconteur import factbase
    from raconteur.context import load_bib_keys, load_bib_summary
    from raconteur.paper import _bib_block
    fb = factbase.from_blob(blob, lit / "work" / "facts.json")
    picker = factbase.SectionFacts(fb, _haarpi_brain("unused", "unused", embed=EMBED))
    sections = {}
    for heading, beats in C1_SECTIONS.items():
        outline = "\n".join(f"- {b}" for b in beats)
        chosen = picker.for_section(heading, outline)
        sections[heading] = {"outline": outline,
                             "facts": [vars(f) for f in chosen]}
    _dump(out / "c1.json", {"title": "Reading agent-based model output as prosopography",
                            "topic": cfg.topic, "focus": cfg.focus,
                            "digest": factbase.digest(fb),
                            "bib_section": _bib_block(load_bib_summary(project, "litReview")),
                            "bib_keys": sorted(load_bib_keys(project, "litReview")),
                            "sections": sections})

    # E1: the hand-judged pairs, by the same sample that was judged
    import random
    located = [f for f in facts if not f["verified"] and f.get("support") is not None
               and f.get("quote")]
    random.seed(7)
    sample = random.sample(located, 30)
    _dump(out / "e1.json", {"pairs": [{"id": f["id"], "fact": f["text"], "quote": f["quote"],
                                       "label": lab} for f, lab in zip(sample, _E1_LABELS)]})
    _say(f"fixtures written: {out}")
    return out


def _dump(fp: Path, obj) -> None:
    fp.write_text(json.dumps(obj, indent=1, ensure_ascii=False), encoding="utf-8")


def _load(name: str) -> dict:
    return json.loads((BENCH_ROOT / "fixtures" / name).read_text(encoding="utf-8"))


def _haarpi_brain(coordinator: str, worker: str, embed: str = ""):
    from .brain import Brain
    return Brain(_url(), coordinator, worker, embed_model=embed, tool="bench")


# ── the tasks ───────────────────────────────────────────────────────────────────

_CONTENT = re.compile(r"[a-z][a-z-]{3,}")
_STOP = frozenset("that this with from were which their these those such than also into over "
                  "between about have been more most when where while there they them".split())


def _content(text: str) -> set[str]:
    return {w for w in _CONTENT.findall((text or "").lower()) if w not in _STOP}


def _rh_brain(coordinator: str, worker: str = BASELINE_WORKER):
    from rabbithole import config as rhconfig
    from rabbithole.brain import Brain as RHBrain
    gc = rhconfig.load_global()
    gc.ollama_url = _url()
    return RHBrain(rhconfig.BrainConfig(coordinator_model=coordinator, worker_model=worker,
                                        embed_model=EMBED), gc)


def task_a1(model: str, work: Path) -> dict:
    """Fact extraction: the real `facts.extract`, fed the frozen reading text."""
    from rabbithole import facts as F, summarize
    from rabbithole.facts import find_quote, page_texts
    from rabbithole.models import Candidate
    fx = _load("a1.json")
    brain = _rh_brain(model)
    cfg = SimpleNamespace(topic=fx["topic"], focus=fx["focus"])
    papers = fx["papers"]
    texts = {p["citekey"]: p for p in papers}
    corpus = [Candidate.from_dict(p["record"]) for p in papers]
    citekeys = {i: p["citekey"] for i, p in enumerate(papers)}
    by_key = {id(c): p for c, p in zip(corpus, papers)}
    orig_text, orig_read = summarize._paper_text, summarize.reading_text
    summarize._paper_text = lambda c: by_key[id(c)]["full"]
    summarize.reading_text = lambda b, c, cfg_, coll, ck, text=None: texts[ck]["reading"]
    paths = SimpleNamespace(work=work / "a1")
    paths.work.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    try:
        got = F.extract(brain, corpus, cfg, paths, None, citekeys)
    finally:
        summarize._paper_text, summarize.reading_text = orig_text, orig_read
    secs = time.time() - t0
    n = verb = adds = badnum = empty = 0
    for p in papers:
        fs = got.get(p["citekey"]) or []
        empty += not fs
        pages = page_texts(p["full"])
        whole = " ".join(pages.values())
        for f in fs:
            n += 1
            verb += bool(f.verified)
            fw = _content(f.text)
            if fw and len(fw - _content(f.quote)) / len(fw) > 0.5:
                adds += 1
            nums = set(re.findall(r"\d+(?:\.\d+)?", f.text)) - {str(y) for y in range(1500, 2100)}
            if any(x not in whole for x in nums):
                badnum += 1
    return {"papers": len(papers), "empty_papers": empty, "facts": n,
            "facts_per_paper": round(n / max(1, len(papers)), 2),
            "verbatim_pct": _pct(verb, n), "adds_beyond_quote_pct": _pct(adds, n),
            "number_not_on_page_pct": _pct(badnum, n),
            "secs_per_paper": round(secs / max(1, len(papers)), 1)}


def task_a2(model: str, work: Path) -> dict:
    """Topic scoring of a fixed fact set, twice: agreement with today's scores and itself."""
    from rabbithole import facts as F
    fx = _load("a2.json")
    topics = [F.Topic(t["name"], t.get("serves") or [], t.get("covers", "")) for t in fx["topics"]]
    base = {f["id"]: f["scores"] for f in fx["facts"]}
    runs, t0 = [], time.time()
    for r in (1, 2):
        by_ck: dict[str, list] = {}
        for f in fx["facts"]:
            by_ck.setdefault(f["citekey"], []).append(F.Fact(f["id"], f["citekey"], f["text"]))
        cks = sorted(by_ck)
        corpus = [SimpleNamespace(author_year=lambda ck=ck: ck) for ck in cks]
        paths = SimpleNamespace(work=work / f"a2_run{r}")
        F.score(_rh_brain(model), by_ck, topics, corpus, dict(enumerate(cks)), paths)
        runs.append({f.id: f.scores for fs in by_ck.values() for f in fs})
    secs = time.time() - t0
    return {"facts": len(base),
            "exact_vs_baseline_pct": _agree(runs[0], base, 0),
            "within1_vs_baseline_pct": _agree(runs[0], base, 1),
            "exact_self_pct": _agree(runs[0], runs[1], 0),
            "secs_per_run": round(secs / 2, 1)}


def _agree(a: dict, b: dict, tol: int) -> float:
    pairs = [(x, y) for k in a if k in b for x, y in zip(a[k], b[k])]
    return _pct(sum(abs(x - y) <= tol for x, y in pairs), len(pairs))


def task_a3(model: str, work: Path) -> dict:
    """Theme names and synthesis for fixed groups: bullets dropped for citing outside the theme."""
    from rabbithole import facts as F
    fx = _load("a3.json")
    brain = _rh_brain(model)
    kept = dropped = dup = failed = 0
    names: dict[str, list[str]] = {}
    t0 = time.time()
    for g in fx["groups"]:
        lines = "\n".join(f"- [@{f['citekey']}] {f['text']}" for f in g["facts"])
        taken = "; ".join(names.get(g["topic"], [])) or "(none yet)"
        try:
            raw = brain.coordinator(F._THEME_PROMPT.format(item=g["topic"], taken=taken,
                                                           facts=lines),
                                    F._THEME_SYS, think=False, num_ctx=16384)
            obj = json.loads(re.search(r"\{.*\}", raw or "", re.S).group(0))
        except Exception:  # noqa: BLE001
            failed += 1
            continue
        name = " ".join(str(obj.get("name") or "").split())
        dup += any(F._same_name(name, x) for x in names.get(g["topic"], []))
        names.setdefault(g["topic"], []).append(name)
        allowed = {f["citekey"] for f in g["facts"]}
        bullets = [str(b) for b in (obj.get("synthesis") or []) if str(b).strip()]
        ok = [b for b in bullets if F._synthesis_ok(b, allowed)]
        kept += len(ok)
        dropped += len(bullets) - len(ok)
    return {"groups": len(fx["groups"]), "failed_replies": failed, "duplicate_names": dup,
            "bullets_kept": kept, "bullets_dropped_pct": _pct(dropped, kept + dropped),
            "secs": round(time.time() - t0, 1)}


def _planner_cfg(model: str) -> dict:
    return {"ollama": {"url": _url(), "coordinator": model, "worker": BASELINE_WORKER}}


def task_b(model: str, work: Path) -> dict:
    """The planner's three judgements, on phrasings whose right answer is published."""
    from . import planner
    fx = _load("b.json")
    cfg = _planner_cfg(model)
    t0 = time.time()
    b1 = [planner.decompose([{"text": c["text"]}], cfg, fact_base=c["fact_base"],
                            questions=["the factoid model", "process-level validation"])
          for c in fx["b1"]]
    b1_ok = sum(bool(t) and t[0]["need"] == c["kind"] for t, c in zip(b1, fx["b1"]))
    b1_miss = [f"{c['text']!r}: {c['kind']} → {t[0]['need'] if t else '?'}"
               for t, c in zip(b1, fx["b1"]) if not (t and t[0]["need"] == c["kind"])]
    b2_ok, b2_miss = 0, []
    for c in fx["b2"]:
        try:
            tier = planner.classify("paper", {"unresolved": [{"author": "DCR", "text": c["text"]}],
                                              "reviewer_changes": 0}, cfg)["tier"]
        except Exception:  # noqa: BLE001
            tier = "?"
        b2_ok += tier == c["tier"]
        if tier != c["tier"]:
            b2_miss.append(f"{c['text']!r}: {c['tier']} → {tier}")
    b3_ok, b3_miss = 0, []
    for c in fx["b3"]:
        f = SimpleNamespace(**c["fact"])
        got = planner._dispute_verdict(c["comment"], c["sentence"], f, cfg)
        b3_ok += got == c["expect"]
        if got != c["expect"]:
            b3_miss.append(f"{c['fact']['id']} / {c['comment']!r}: {c['expect']} → {got}")
    return {"b1_pct": _pct(b1_ok, len(fx["b1"])), "b2_pct": _pct(b2_ok, len(fx["b2"])),
            "b3_pct": _pct(b3_ok, len(fx["b3"])), "secs": round(time.time() - t0, 1),
            "misses": b1_miss + b2_miss + b3_miss}


def task_c1(model: str, work: Path) -> dict:
    """Drafting two literature sections from fixed facts, through raconteur's own loop."""
    from raconteur import factbase, paper
    from raconteur.brain import Brain as RBrain
    from raconteur.config import GlobalConfig as RGC
    fx = _load("c1.json")
    g = RGC()
    g.ollama_url, g.coordinator_model, g.worker_model = _url(), model, BASELINE_WORKER
    brain = RBrain(g)
    known = set(fx["bib_keys"])
    out, t0 = {}, time.time()
    findings = traced = cited = offbib = numbad = words_out = 0
    for heading, sec in fx["sections"].items():
        facts = [factbase.Fact(**f) for f in sec["facts"]]
        ctx = paper._context_for_section(heading, fx["digest"] + "\n\n"
                                         + factbase.section_block(facts), "", "", {}, "")
        text = brain.coordinator(paper._DRAFT_SECTION_PROMPT.format(
            heading=heading, title=fx["title"], topic=fx["topic"], focus=fx["focus"],
            venue_section="", style_section="", analysis="", section_outline=sec["outline"],
            context_section=ctx, bib_section=fx["bib_section"], words_low=C1_BAND[0],
            words_high=C1_BAND[1], para_low=paper.guards.PARAGRAPH_BAND[0],
            para_high=paper.guards.PARAGRAPH_BAND[1], para_words=paper.guards.WORDS_PER_PARAGRAPH,
            figure_start=1), system=paper._SYSTEM, num_ctx=16384)
        for n in (1, 2):
            text = paper._critique_revise(brain, heading, text, sec["outline"], "", n, C1_BAND)
        text = paper._guard_repair(brain, heading, text, sec["outline"], fx["bib_section"],
                                   known, False, band=C1_BAND, bullets=3,
                                   extra=paper._number_guard(facts))
        text = factbase.strip_ids(text)
        left = paper._guard_section(text, heading, known, False, band=C1_BAND, bullets=3) \
            + factbase.number_findings(text, facts)
        rows = factbase.trace(text, facts)
        findings += len(left)
        numbad += sum(f.kind == "fact-number" for f in left)
        cited += len(rows)
        traced += sum(r["fact"] is not None for r in rows)
        offbib += sum(1 for r in rows for k in r["citekeys"] if k not in known)
        words = len(re.findall(r"\b\w+\b", re.sub(r"\[@[^\]]+\]", "", text)))
        words_out += not (C1_BAND[0] <= words <= C1_BAND[1])
        out[heading] = text
    (work / "c1_sections.md").write_text("\n\n".join(f"## {h}\n\n{t}" for h, t in out.items()),
                                         encoding="utf-8")
    return {"sections": len(out), "guard_findings_left": findings,
            "number_mismatches": numbad, "cited_sentences": cited,
            "traced_to_fact_pct": _pct(traced, cited), "off_bibliography_citations": offbib,
            "sections_outside_band": words_out, "secs": round(time.time() - t0, 1)}


def task_w1(model: str, work: Path) -> dict:
    """Worker: condensing long papers' chunks, keeping every reported quantity."""
    from rabbithole.summarize import _condense
    fx = _load("w1.json")
    brain = _rh_brain(BASELINE_COORDINATOR, worker=model)
    chunks = [c for p in fx["papers"] for c in p["chunks"]]
    t0 = time.time()
    outs = []
    for c in chunks:
        try:
            outs.append(_condense(brain, c))
        except Exception:  # noqa: BLE001
            outs.append("")
    secs = time.time() - t0
    num = lambda s: set(re.findall(r"\d+(?:\.\d+)+%?|\d+%", s or ""))   # decimals, percentages
    kept = sum(len(num(c) & num(o)) for c, o in zip(chunks, outs))
    total = sum(len(num(c)) for c in chunks)
    return {"chunks": len(chunks), "empty_outputs": sum(not o.strip() for o in outs),
            "quantities_kept_pct": _pct(kept, total),
            "mean_output_chars": round(sum(map(len, outs)) / max(1, len(outs))),
            "secs_per_chunk": round(secs / max(1, len(chunks)), 1)}


def task_e1(work: Path) -> dict:
    """How well each embedder's fact-quote cosine separates the hand-judged pairs (AUC)."""
    fx = _load("e1.json")
    out = {}
    for emb in (EMBED, "bge-m3"):
        b = _haarpi_brain("unused", "unused", embed=emb)
        scores = []
        for p in fx["pairs"]:
            try:
                scores.append(_cos(b.embed(p["fact"]), b.embed(p["quote"])))
            except Exception:  # noqa: BLE001
                scores.append(0.0)
        out[emb] = {"auc": _auc(scores, [p["label"] for p in fx["pairs"]])}
    return out


def _cos(a, b) -> float:
    d = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return sum(x * y for x, y in zip(a, b)) / d if d else 0.0


def _auc(scores: list[float], labels: list[int]) -> float | None:
    pos = [s for s, l in zip(scores, labels) if l]
    neg = [s for s, l in zip(scores, labels) if not l]
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return round(wins / (len(pos) * len(neg)), 3)


def _pct(a: int, b: int) -> float | None:
    return round(100 * a / b, 1) if b else None


COORDINATOR_TASKS = {"A1": task_a1, "A2": task_a2, "A3": task_a3, "B": task_b, "C1": task_c1}


def run(coordinator: str = "", worker: str = "", embedders: bool = False,
        tasks: list[str] | None = None) -> Path:
    if embedders:
        label, todo = "embedders", {"E1": lambda m, w: task_e1(w)}
    elif worker:
        label, todo = f"worker_{_slug(worker)}", {"W1": task_w1}
    else:
        label = f"coordinator_{_slug(coordinator)}"
        todo = {k: v for k, v in COORDINATOR_TASKS.items() if not tasks or k in tasks}
    model = worker or coordinator
    work = BENCH_ROOT / "work" / label
    work.mkdir(parents=True, exist_ok=True)
    fp = BENCH_ROOT / "results" / f"{label}.json"
    fp.parent.mkdir(parents=True, exist_ok=True)
    results = json.loads(fp.read_text()) if fp.exists() else {}
    results.update({"label": label, "model": model, "url": _url()})
    for name, fn in todo.items():
        _say(f"{label}: {name} …")
        t0 = time.time()
        try:
            results[name] = fn(model, work)
        except Exception as e:  # noqa: BLE001 — one task's failure is a result, not a stop
            results[name] = {"error": f"{type(e).__name__}: {e}"}
        _say(f"{label}: {name} done in {time.time() - t0:.0f}s → {results[name]}")
        _dump(fp, results)                   # after every task: a crash keeps what finished
    return fp


# ── report ──────────────────────────────────────────────────────────────────────

_COLS = [("A1", "verbatim_pct", "A1 verbatim %"), ("A1", "adds_beyond_quote_pct", "A1 adds-beyond %"),
         ("A1", "empty_papers", "A1 empty"), ("A1", "number_not_on_page_pct", "A1 bad numbers %"),
         ("A1", "secs_per_paper", "A1 s/paper"), ("A2", "within1_vs_baseline_pct", "A2 ±1 vs base %"),
         ("A2", "exact_self_pct", "A2 self %"), ("A3", "bullets_dropped_pct", "A3 dropped %"),
         ("B", "b1_pct", "B1 %"), ("B", "b2_pct", "B2 %"), ("B", "b3_pct", "B3 %"),
         ("C1", "guard_findings_left", "C1 findings"), ("C1", "traced_to_fact_pct", "C1 traced %"),
         ("C1", "number_mismatches", "C1 bad numbers"), ("C1", "secs", "C1 s")]
_WCOLS = [("W1", "empty_outputs", "empty"), ("W1", "quantities_kept_pct", "quantities kept %"),
          ("W1", "secs_per_chunk", "s/chunk")]


def report(out: Path | None = None) -> Path:
    import html
    rows = [json.loads(p.read_text()) for p in sorted((BENCH_ROOT / "results").glob("*.json"))]
    out = Path(out or BENCH_ROOT / "BENCH_report.html")

    def table(rs, cols):
        h = "<tr><th>model</th>" + "".join(f"<th>{c[2]}</th>" for c in cols) + "</tr>"
        for r in rs:
            cells = []
            for task, key, _ in cols:
                v = (r.get(task) or {}).get(key, (r.get(task) or {}).get("error", "—"))
                cells.append(f"<td>{html.escape(str(v))[:60]}</td>")
            h += f"<tr><td><code>{html.escape(r['model'])}</code></td>{''.join(cells)}</tr>"
        return f"<table>{h}</table>"
    coord = [r for r in rows if r["label"].startswith("coordinator_")]
    work = [r for r in rows if r["label"].startswith("worker_")]
    emb = next((r for r in rows if r["label"] == "embedders"), {})
    misses = "".join(f"<h3><code>{html.escape(r['model'])}</code></h3><ul>"
                     + "".join(f"<li>{html.escape(m)}</li>" for m in (r.get("B") or {}).get("misses", []))
                     + "</ul>" for r in coord)
    body = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Brain bake-off</title>
<style>:root{{--bg:#fbfaf7;--fg:#1e1e1e;--rule:#ddd;--mut:#666}}
@media (prefers-color-scheme:dark){{:root{{--bg:#161616;--fg:#e8e8e8;--rule:#333;--mut:#9a9a9a}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;margin:0 auto;
max-width:1200px;padding:20px 16px}}table{{border-collapse:collapse;width:100%;font-size:.9em}}
th,td{{border-bottom:1px solid var(--rule);padding:5px 7px;text-align:left}}th{{color:var(--mut)}}
.wrap{{overflow-x:auto}}</style></head><body>
<h1>Brain bake-off — results</h1><p style="color:var(--mut)">Baseline: coordinator
<code>{BASELINE_COORDINATOR}</code>, worker <code>{BASELINE_WORKER}</code>. Rule: see PLAN_brainbench.html §5.</p>
<h2>Coordinators</h2><div class="wrap">{table(coord, _COLS)}</div>
<h2>Workers</h2><div class="wrap">{table(work, _WCOLS)}</div>
<h2>Embedders (E1, AUC on 30 hand-judged pairs)</h2>
<pre>{html.escape(json.dumps(emb.get('E1', {}), indent=1))}</pre>
<h2>Planner misses (B1–B3)</h2>{misses}</body></html>"""
    out.write_text(body, encoding="utf-8")
    return out


def main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="haarpi bench")
    sub = ap.add_subparsers(dest="verb", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--project", default=str(DIGIPROS))
    r = sub.add_parser("run")
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("--coordinator")
    g.add_argument("--worker")
    g.add_argument("--embedders", action="store_true")
    r.add_argument("--tasks", nargs="*")
    sub.add_parser("report")
    a = ap.parse_args(argv)
    if a.verb == "prepare":
        prepare(Path(a.project))
    elif a.verb == "run":
        print(run(a.coordinator or "", a.worker or "", a.embedders, a.tasks))
    else:
        print(report())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

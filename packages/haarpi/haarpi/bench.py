"""`haarpi bench` — which local model should be the coordinator, and which the worker?

The same fixed, real inputs are replayed through each candidate and scored by the same
deterministic code, so the models differ and nothing else does. Read-only on every project:
fixtures and results live under ``~/haarpi-bench/`` (or ``$HAARPI_BENCH``).

  haarpi bench prepare              build the fixtures once (needs the embedder: a GPU task)
  haarpi bench run --coordinator M  every coordinator task with M as coordinator
  haarpi bench run --worker M       the worker task with M as worker
  haarpi bench run --embedders      the embedder side check (mxbai vs bge-m3)
  haarpi bench report               one HTML table of every result so far

X1, the extraction bake-off (PLAN_extraction_bench.html, 2026-11-02), compares two fact
prompts across models on 15 FirmPathways papers and is judged blind by a person:

  haarpi bench prepare --x1                      freeze the X1 fixture (needs the embedder)
  haarpi bench run --coordinator M --tasks X1 --prompt p0|p1 [--think]
  haarpi bench judge                             write the blind page EVAL_extraction.html
  haarpi bench unblind EVAL_extraction_DCR.json  score the judgements by prompt and model

X2, paper cards (PLAN_paper_cards.html, 2026-11-05), lives in `bench_x2`; its verbs are
`prepare --x2`, `run --tasks X2E`, `pair --x2`, `run --tasks X2J X2V`, `judge --x2` and
`unblind --x2`.

The tasks (see PLAN_brainbench.html): A1 fact extraction, A2 topic scoring, A3 theme naming
(rabbitHole); B1 comment sorting, B2 paper tiers, B3 dispute verdicts (haarpi's planner); C1
section drafting (raconteur); W1 long-paper condensing (worker); E1 embedder separation.
A4 (narrative paragraph redline) was dropped: no recorded markup carries the corpus, notes and
evidence it would need to replay faithfully.

Each runner lane points `OLLAMA_URL` at its own card's ollama; the harness passes that URL to
every brain it builds; the planner and razzle also read OLLAMA_URL themselves (brain.ollama_url).
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
FIRMPATHWAYS = Path("/media/lucullus/Cale_Professional/Current_Work_Projects/260525_FirmPathways")
HAARPI_DIR = Path(__file__).resolve().parents[3]          # where PLAN_/EVAL_ pages live
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
    # "…" stands for what the reviewer would write there: a scope after "should only mean", a
    # pasted reference after "these references:" (a bare label there is not an ingest ask).
    def fill(t: str) -> str:
        t = t.replace("X", "archival silences").replace("Y", "the factoid model").replace("T7", "T2")
        if t.startswith("these references"):
            return t.replace("…", "Bradley, J. & Short, H. (2005). Texts into databases. "
                                  "Literary and Linguistic Computing 20(1).")
        return t.replace("…", "process-level validation only")
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


def prepare_x1(project: Path = FIRMPATHWAYS, out: Path | None = None) -> Path:
    """Freeze X1: the 15 papers of the review page's current spot-check (drawn at random before
    this bake-off was planned, so not picked to flatter either prompt), each with its reading
    text as extraction sees it, its title and abstract, plus the review's questions and topics."""
    from rabbithole import chroma, config as rhconfig
    from rabbithole.brain import Brain as RHBrain
    from rabbithole.models import Candidate
    from rabbithole.summarize import _paper_text, reading_text

    out = Path(out or BENCH_ROOT / "fixtures")
    out.mkdir(parents=True, exist_ok=True)
    lit = project / "litReview"
    cfg = rhconfig.load_project(str(project))
    gc = rhconfig.load_global()
    gc.ollama_url = _url()
    brain = RHBrain(rhconfig.BrainConfig(embed_model=EMBED), gc)
    pages = sorted((lit / "output").glob("[0-9]" * 6 + "_*_review_ra.html"))
    if not pages:
        raise SystemExit("X1: no review page in litReview/output to take the spot-check from")
    data = json.loads(re.search(r"const D = (.*);\n", pages[-1].read_text(encoding="utf-8"))
                      .group(1).replace("<\\/", "</"))
    keys = list(dict.fromkeys(f["citekey"] for f in data["spot"]))
    records = {d.get("citekey"): d for d in json.loads(
        (lit / "work" / "corpus.json").read_text(encoding="utf-8"))}
    try:
        collection = chroma.get_collection(lit / "work" / "chroma")
    except Exception as e:  # noqa: BLE001
        _say(f"no chroma collection ({e}) — long papers read condensed instead")
        collection = None
    papers = []
    for key in keys:
        d = records.get(key)
        if d is None:
            _say(f"X1: {key} not in the corpus — skipped")
            continue
        c = Candidate.from_dict(d)
        full = _paper_text(c)
        reading = reading_text(brain, c, cfg, collection, key, text=full)
        papers.append({"citekey": key, "record": d, "label": c.author_year(),
                       "title": re.sub(r"<[^>]+>", "", c.title or ""),
                       "abstract": c.abstract or "", "full": full, "reading": reading})
        _say(f"X1: {c.author_year()} — {len(reading):,} chars read of {len(full):,}")
    fp = out / "x1.json"
    _dump(fp, {"source": pages[-1].name, "questions": list(cfg.questions or []),
               "topics": list(cfg.topics or []), "focus": cfg.focus, "papers": papers})
    return fp


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


_UNRESOLVED = re.compile(r"\b(this (study|paper|article|work|model|approach|method|framework|"
                         r"analysis|research)|the (current|present|proposed) \w+|the study|"
                         r"the authors|we|our|us)\b", re.I)
_DEFINITION = re.compile(r"\b(is|are) (defined|conceptuali[sz]ed|understood) as\b", re.I)


def x1_metrics(papers: list[dict], got: dict[str, list], nothing: dict[str, str],
               secs: float) -> dict:
    """The automatic half of X1's scoring, from the facts each paper yielded."""
    from rabbithole.facts import page_texts
    n = verb = copy = unres = defin = badnum = linked = 0
    for p in papers:
        whole = " ".join(page_texts(p["full"]).values())
        for f in got.get(p["citekey"]) or []:
            n += 1
            verb += bool(f.verified)
            fw, qw = _content(f.text), _content(f.quote)
            if fw and qw and len(fw & qw) / len(fw | qw) >= 0.6:
                copy += 1
            unres += bool(_UNRESOLVED.search(f.text))
            defin += bool(_DEFINITION.search(f.text))
            linked += bool(getattr(f, "bears_on", None))
            nums = set(re.findall(r"\d+(?:\.\d+)?", f.text)) - {str(y) for y in range(1500, 2100)}
            badnum += any(x not in whole for x in nums)
    empty = sum(1 for p in papers if not got.get(p["citekey"]))
    return {"papers": len(papers), "facts": n,
            "facts_per_paper": round(n / max(1, len(papers)), 2),
            "empty_papers": empty, "nothing_papers": len(nothing),
            "verbatim_pct": _pct(verb, n), "copyback_pct": _pct(copy, n),
            "unresolved_pct": _pct(unres, n), "definition_pct": _pct(defin, n),
            "number_not_on_page_pct": _pct(badnum, n), "topic_linked_pct": _pct(linked, n),
            "secs_per_paper": round(secs / max(1, len(papers)), 1)}


def task_x1(model: str, work: Path, prompt: str = "p0", think: bool = False) -> dict:
    """Fact extraction on the X1 fixture, through the real `facts.extract`, with prompt P0 or P1.
    Returns the metrics and every paper's facts, which `judge` lays out blind."""
    import shutil
    from rabbithole import facts as F, summarize
    from rabbithole.models import Candidate
    fx = _load("x1.json")
    brain = _rh_brain(model)
    cfg = SimpleNamespace(topic="", focus=fx.get("focus") or "", questions=fx["questions"],
                          topics=fx["topics"], fact_prompt=prompt, fact_think=think)
    papers = fx["papers"]
    texts = {p["citekey"]: p for p in papers}
    corpus = [Candidate.from_dict(p["record"]) for p in papers]
    citekeys = {i: p["citekey"] for i, p in enumerate(papers)}
    by_key = {id(c): p for c, p in zip(corpus, papers)}
    orig_text, orig_read = summarize._paper_text, summarize.reading_text
    summarize._paper_text = lambda c: by_key[id(c)]["full"]
    summarize.reading_text = lambda b, c, cfg_, coll, ck, text=None: texts[ck]["reading"]
    paths = SimpleNamespace(work=work / f"x1_{prompt}_{'think' if think else 'nothink'}")
    shutil.rmtree(paths.work, ignore_errors=True)        # never a cached answer from a last run
    paths.work.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    try:
        got = F.extract(brain, corpus, cfg, paths, None, citekeys)
    finally:
        summarize._paper_text, summarize.reading_text = orig_text, orig_read
    secs = time.time() - t0
    from rabbithole.summarize import _located_filename
    nothing = {}
    for p in papers:
        for fp in (paths.work / "facts").glob(f"{_located_filename(p['citekey'])}.*json"):
            b = json.loads(fp.read_text(encoding="utf-8"))
            if b.get("nothing") and not b.get("facts"):
                nothing[p["citekey"]] = b["nothing"]
    out = x1_metrics(papers, got, nothing, secs)
    out["prompt"], out["think"] = prompt, think
    out["sets"] = {p["citekey"]: {"facts": [{"text": f.text, "quote": f.quote, "page": f.page}
                                            for f in got.get(p["citekey"]) or []],
                                  "nothing": nothing.get(p["citekey"], "")}
                   for p in papers}
    return out


def _pct(a: int, b: int) -> float | None:
    return round(100 * a / b, 1) if b else None


COORDINATOR_TASKS = {"A1": task_a1, "A2": task_a2, "A3": task_a3, "B": task_b, "C1": task_c1}


def run(coordinator: str = "", worker: str = "", embedders: bool = False,
        tasks: list[str] | None = None, prompt: str = "p0", think: bool = False) -> Path:
    if tasks and "X1" in tasks:
        # One results file per model x prompt x thinking: each is one cell of the X1 grid.
        label = f"x1_{_slug(coordinator)}_{prompt}_{'think' if think else 'nothink'}"
        todo = {"X1": lambda m, w: task_x1(m, w, prompt, think)}
    elif embedders:
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


# ── X1: the blind page, and unblinding ─────────────────────────────────────────

BLIND_KEY = "x1_blind_key.json"
_LETTERS = "ABCDEFGHIJKLMNOP"


def judge(out: Path | None = None) -> Path:
    """Lay every X1 run's fact set out per paper, shuffled and lettered, with nothing that names
    the prompt or the model. Only the claim and its quote are shown: P1's extra fields would give
    it away. (A P1 "nothing for this review" still shows as an empty set with its reason — P0
    never returns one, so that one set is not blind; the page says so.) The key stays in the
    bench folder, never in the page."""
    import random
    fx = _load("x1.json")
    runs = sorted((BENCH_ROOT / "results").glob("x1_*.json"))
    runs = [r for r in runs if r.name != "x1_judged.json"]
    results = {r.stem: json.loads(r.read_text(encoding="utf-8")) for r in runs}
    results = {k: v for k, v in results.items() if "sets" in (v.get("X1") or {})}
    if not results:
        raise SystemExit("judge: no finished X1 runs in the results folder")
    key, papers = {}, []
    for p in fx["papers"]:
        labels = sorted(results)
        random.Random(f"x1-{p['citekey']}").shuffle(labels)
        sets = []
        for i, lab in enumerate(labels):
            st = results[lab]["X1"]["sets"].get(p["citekey"]) or {"facts": [], "nothing": ""}
            sets.append({"label": _LETTERS[i], "facts": st["facts"], "nothing": st["nothing"]})
            key.setdefault(p["citekey"], {})[_LETTERS[i]] = lab
        papers.append({"ck": p["citekey"], "label": p["label"], "title": p["title"],
                       "abstract": p["abstract"], "sets": sets})
    _dump(BENCH_ROOT / BLIND_KEY, key)
    out = Path(out or HAARPI_DIR / "EVAL_extraction.html")
    blob = json.dumps({"papers": papers, "n_runs": len(results)},
                      ensure_ascii=False).replace("</", "<\\/")
    out.write_text(_JUDGE_PAGE.replace("__DATA__", blob), encoding="utf-8")
    return out


def unblind(judged: Path) -> dict:
    """Score the judgements per run and per prompt. A set scores 1 when every claim is one you
    would cite and every claim is understandable on its own; "some" and "partly" count half.
    A fact flagged wrong costs its set the faithfulness point outright."""
    key = _load_bench(BLIND_KEY)
    marks = json.loads(Path(judged).read_text(encoding="utf-8"))
    val = {"all": 1.0, "some": 0.5, "none": 0.0, "yes": 1.0, "partly": 0.5, "no": 0.0}
    per: dict[str, dict] = {}
    for ck, sets in marks.items():
        for letter, m in sets.items():
            lab = (key.get(ck) or {}).get(letter)
            if not lab:
                continue
            r = per.setdefault(lab, {"sets": 0, "cite": 0.0, "understand": 0.0, "both": 0,
                                     "wrong_facts": 0, "faithful_sets": 0})
            r["sets"] += 1
            c, u = val.get(m.get("cite"), 0.0), val.get(m.get("understand"), 0.0)
            r["cite"] += c
            r["understand"] += u
            wrong = len(m.get("wrong") or [])
            r["wrong_facts"] += wrong
            r["faithful_sets"] += wrong == 0
            r["both"] += (c == 1.0 and u == 1.0 and wrong == 0)
    table = {lab: {"sets": r["sets"], "cite_pct": _pct(round(r["cite"] * 10), r["sets"] * 10),
                   "understand_pct": _pct(round(r["understand"] * 10), r["sets"] * 10),
                   "citable_clear_faithful_pct": _pct(r["both"], r["sets"]),
                   "faithful_sets_pct": _pct(r["faithful_sets"], r["sets"]),
                   "wrong_facts": r["wrong_facts"]} for lab, r in sorted(per.items())}
    pooled: dict[str, list] = {}
    for lab, r in per.items():
        pooled.setdefault(lab.rsplit("_", 2)[-2], []).append(r)
    by_prompt = {pr: {"sets": sum(r["sets"] for r in rs),
                      "citable_clear_faithful_pct": _pct(sum(r["both"] for r in rs),
                                                         sum(r["sets"] for r in rs))}
                 for pr, rs in pooled.items()}
    out = {"runs": table, "by_prompt": by_prompt}
    _dump(BENCH_ROOT / "results" / "x1_judged.json", out)
    return out


def _load_bench(name: str) -> dict:
    return json.loads((BENCH_ROOT / name).read_text(encoding="utf-8"))


_JUDGE_PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Extraction judging</title>
<style>:root{--bg:#fbfaf7;--fg:#1e1e1e;--mut:#666;--rule:#ddd;--card:#fff;--acc:#3b5bdb;--bad:#c92a2a}
@media (prefers-color-scheme:dark){:root{--bg:#161616;--fg:#e8e8e8;--mut:#9a9a9a;--rule:#333;--card:#1f1f1f;--acc:#8ea2ff;--bad:#ff8787}}
body{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;max-width:960px;margin:0 auto;padding:20px 16px 80px}
h2{font-size:1.1em;margin:1.6em 0 .2em}.mut{color:var(--mut);font-size:.9em}
.set{background:var(--card);border:1px solid var(--rule);border-radius:6px;padding:8px 12px;margin:.6em 0}
.set h3{margin:.1em 0 .4em;font-size:1em}.fact{margin:.4em 0}.q{color:var(--mut);font-size:.88em;margin-left:1.4em}
.ask{margin:.3em 0;display:flex;flex-wrap:wrap;gap:.5em;align-items:center}
.ask button{border:1px solid var(--rule);background:var(--bg);color:var(--fg);border-radius:4px;padding:2px 9px;cursor:pointer}
.ask button.on{background:var(--acc);color:#fff;border-color:var(--acc)}
.done{border-left:4px solid var(--acc)}.wrongbox{accent-color:var(--bad)}
#bar{position:fixed;left:0;right:0;bottom:0;background:var(--card);border-top:1px solid var(--rule);padding:8px 16px;display:flex;gap:1em;align-items:center}
details{margin:.2em 0}</style></head><body>
<h1>Extraction bake-off: blind judging</h1>
<p class="mut">For each paper: its title and abstract, then each run's set of facts, lettered at random (the letters
mean different runs on different papers). Judge each set on two questions, and tick any fact that is wrong or misleading
against its quote. A set with no facts says why; that one cannot be blind. Your marks are kept in this browser while you
work; press <b>Save</b> to download <code>EVAL_extraction_DCR.json</code> into the HAARPi folder.</p>
<div id="app"></div>
<div id="bar"><span id="prog"></span><button id="save">Save</button></div>
<script>
const D = __DATA__;
const KEY = "x1-judging";
let S = {}; try { S = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) { S = {}; }
const keep = () => { try { localStorage.setItem(KEY, JSON.stringify(S)); } catch (e) {} prog(); };
const el = (t, a = {}, ...k) => { const e = document.createElement(t); for (const [x, v] of Object.entries(a)) { if (x.startsWith("on")) e.addEventListener(x.slice(2), v); else if (x === "class") e.className = v; else e[x] = v; } for (const c of k) if (c != null) e.append(c); return e; };
const mark = (ck, L) => ((S[ck] = S[ck] || {})[L] = S[ck][L] || {wrong: []});
function ask(ck, L, field, opts, label, box) {
  const row = el("div", {class: "ask"}, el("span", {}, label));
  opts.forEach(o => { const b = el("button", {onclick: () => { mark(ck, L)[field] = o; paint(); keep(); }}, o); row.append(b);
    const paint = () => { row.querySelectorAll("button").forEach(x => x.classList.toggle("on", (S[ck]?.[L]?.[field]) === x.textContent));
      box.classList.toggle("done", !!(S[ck]?.[L]?.cite && S[ck]?.[L]?.understand)); }; b.paint = paint; });
  setTimeout(() => row.querySelectorAll("button").forEach(b => b.paint()), 0);
  return row;
}
const app = document.getElementById("app");
D.papers.forEach((p, pi) => {
  app.append(el("h2", {}, `${pi + 1}. ${p.label}`), el("div", {}, el("i", {}, p.title)),
    p.abstract ? el("details", {}, el("summary", {class: "mut"}, "abstract"), el("div", {class: "mut"}, p.abstract)) : el("div", {class: "mut"}, "(no abstract)"));
  p.sets.forEach(s => {
    const box = el("div", {class: "set"}, el("h3", {}, "Set " + s.label));
    if (!s.facts.length) box.append(el("div", {class: "mut"}, "No facts: " + (s.nothing || "(none returned)")));
    s.facts.forEach((f, i) => {
      const cb = el("input", {type: "checkbox", class: "wrongbox", title: "wrong or misleading",
        checked: (S[p.ck]?.[s.label]?.wrong || []).includes(i),
        onchange: e => { const m = mark(p.ck, s.label); m.wrong = m.wrong.filter(x => x !== i); if (e.target.checked) m.wrong.push(i); keep(); }});
      box.append(el("div", {class: "fact"}, el("label", {}, cb, " ", `${i + 1}. ${f.text}`)),
        f.quote ? el("div", {class: "q"}, `“${f.quote}”` + (f.page ? ` (p.${f.page})` : "")) : null);
    });
    box.append(ask(p.ck, s.label, "cite", ["all", "some", "none"], "Would you cite this paper for these?", box),
      ask(p.ck, s.label, "understand", ["yes", "partly", "no"], "Understandable without the paper?", box));
    app.append(box);
  });
});
function prog() {
  const total = D.papers.reduce((n, p) => n + p.sets.length, 0);
  const done = D.papers.reduce((n, p) => n + p.sets.filter(s => S[p.ck]?.[s.label]?.cite && S[p.ck]?.[s.label]?.understand).length, 0);
  document.getElementById("prog").textContent = `${done} of ${total} sets judged`;
}
prog();
document.getElementById("save").onclick = () => {
  const a = el("a", {href: URL.createObjectURL(new Blob([JSON.stringify(S, null, 1)], {type: "application/json"})), download: "EVAL_extraction_DCR.json"});
  document.body.append(a); a.click(); a.remove();
};
</script></body></html>"""


# ── report ──────────────────────────────────────────────────────────────────────

_COLS = [("A1", "verbatim_pct", "A1 verbatim %"), ("A1", "adds_beyond_quote_pct", "A1 adds-beyond %"),
         ("A1", "empty_papers", "A1 empty"), ("A1", "number_not_on_page_pct", "A1 bad numbers %"),
         ("A1", "secs_per_paper", "A1 s/paper"), ("A2", "within1_vs_baseline_pct", "A2 ±1 vs base %"),
         ("A2", "exact_self_pct", "A2 self %"), ("A3", "bullets_dropped_pct", "A3 dropped %"),
         ("B", "b1_pct", "B1 %"), ("B", "b2_pct", "B2 %"), ("B", "b3_pct", "B3 %"),
         ("C1", "guard_findings_left", "C1 findings"), ("C1", "traced_to_fact_pct", "C1 traced %"),
         ("C1", "number_mismatches", "C1 bad numbers"), ("C1", "secs", "C1 s")]
_XCOLS = [("X1", "prompt", "prompt"), ("X1", "think", "think"), ("X1", "facts_per_paper", "facts/paper"),
          ("X1", "nothing_papers", "nothing"), ("X1", "verbatim_pct", "verbatim %"),
          ("X1", "copyback_pct", "copy-back %"), ("X1", "unresolved_pct", "unresolved %"),
          ("X1", "definition_pct", "definitions %"), ("X1", "number_not_on_page_pct", "bad numbers %"),
          ("X1", "topic_linked_pct", "topic-linked %"), ("X1", "secs_per_paper", "s/paper")]
_WCOLS = [("W1", "empty_outputs", "empty"), ("W1", "quantities_kept_pct", "quantities kept %"),
          ("W1", "secs_per_chunk", "s/chunk")]


def report(out: Path | None = None) -> Path:
    import html
    rows = [json.loads(p.read_text()) for p in sorted((BENCH_ROOT / "results").glob("*.json"))
            if p.name != "x1_judged.json"]
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
    x1 = [r for r in rows if r.get("label", "").startswith("x1_")]
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
<h2>X1 · extraction (automatic half; the judged half is in results/x1_judged.json)</h2>
<div class="wrap">{table(x1, _XCOLS)}</div>
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
    p.add_argument("--project", default="")
    p.add_argument("--x1", action="store_true", help="freeze the X1 extraction fixture only")
    p.add_argument("--x2", action="store_true", help="freeze the X2 paper-card fixture only")
    pr = sub.add_parser("pair", help="X2: pair candidates, rotate roles, plant canaries")
    pr.add_argument("--x2", action="store_true", required=True)
    r = sub.add_parser("run")
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("--coordinator")
    g.add_argument("--worker")
    g.add_argument("--embedders", action="store_true")
    r.add_argument("--tasks", nargs="*")
    r.add_argument("--prompt", choices=["p0", "p1"], default="p0", help="X1: the fact prompt")
    r.add_argument("--think", action="store_true", help="X1: thinking on for extraction")
    sub.add_parser("report")
    j = sub.add_parser("judge", help="X1 (or --x2): write the blind judging page")
    j.add_argument("--x2", action="store_true")
    u = sub.add_parser("unblind", help="X1 (or --x2): score the saved judgements")
    u.add_argument("--x2", action="store_true")
    u.add_argument("judged")
    a = ap.parse_args(argv)
    if a.verb == "prepare":
        if a.x2:
            from . import bench_x2
            print(bench_x2.prepare_x2(Path(a.project or FIRMPATHWAYS)))
        elif a.x1:
            print(prepare_x1(Path(a.project or FIRMPATHWAYS)))
        else:
            prepare(Path(a.project or DIGIPROS))
    elif a.verb == "pair":
        from . import bench_x2
        print(bench_x2.pair_x2())
    elif a.verb == "run" and a.tasks and any(t.startswith("X2") for t in a.tasks):
        from . import bench_x2
        print(bench_x2.run_x2(a.coordinator or "", a.tasks))
    elif a.verb == "run":
        print(run(a.coordinator or "", a.worker or "", a.embedders, a.tasks,
                  prompt=a.prompt, think=a.think))
    elif a.verb == "judge":
        if a.x2:
            from . import bench_x2
            print(bench_x2.judge_x2())
        else:
            print(judge())
    elif a.verb == "unblind":
        if a.x2:
            from . import bench_x2
            print(json.dumps(bench_x2.unblind_x2(Path(a.judged)), indent=1))
        else:
            print(json.dumps(unblind(Path(a.judged)), indent=1))
    else:
        print(report())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

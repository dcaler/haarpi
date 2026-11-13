"""Claim grounding: give each paper in a project its card (PLAN_claim_grounding.html §2).

Bench FP's procedure, adopted 2026-11-13, one step per trundlr task:

  init                CPU   each paper's text and record into its card (once per paper)
  kind --model M      GPU   review or not (two models; a review only if both say so)
  extract [--part K --parts N]
                      both  llama3.3 states what each paper claims, quotes checked
  plant               CPU   this run's step-2 and cited canaries, from the project's claims
  reread --model M    GPU   each verifier judges each claim on its pages and copies the span
  locate              CPU   the spans found in the paper's words; step-4 canaries; 2b items
  confirm --model M   GPU   each verifier judges the claim against the OTHER one's passage
  own --model M       GPU   is it the paper's own, and in what role
  close               CPU   each claim confirmed or where it failed; what the canaries say slipped
  gate [--seed N]     CPU   the gate page (gate.py); `haarpi next` reads the saved answers

A claim is confirmed only if every check passes for both verifiers. The prompts are
`ground_prompts.py`, verbatim from the bench. Cards are kept once per paper for every project
(`cards.py`); what belongs to this project's run (its canaries and their answers, the
summary) is kept in `<litReview>/work/claims/`.
"""
from __future__ import annotations

import json
import random
import re
import time
from collections import Counter
from pathlib import Path

from . import canaries as K
from . import cards as C
from . import ground_prompts as P
from .facts import _PAGE_SPLIT, _passage_text, find_quote, locate_passage, page_texts

ROLES = ("finding", "proposal", "synthesis")
MIN_SHARE = 0.9             # a span is found when 90% of its words occur in order
MIN_TEXT = 2000             # characters: less is an abstract or a failed extraction
SAVE_EVERY = 10
CARD_BUDGET = 32000         # characters of paper text per card call (~8k tokens)
WINDOW = 24000              # a merge window, for a paper that will not fit
SECTION_CAP = 12000         # an introduction or conclusion longer than this is cut


def _say(msg: str) -> None:
    from .runlog import stamp
    print(f"{stamp()}{msg}", flush=True)


def _low(form: dict | None, k: str) -> str:
    return str((form or {}).get(k) or "").strip().lower()


def _think(model: str) -> bool:
    # gpt-oss always reasons; asking for it keeps the reasoning out of the answer.
    return model.startswith("gpt-oss")


def _obj(raw: str) -> dict:
    """The reply's first complete JSON object, or {}."""
    text = re.sub(r"```(?:json)?", "", raw or "")
    dec, i = json.JSONDecoder(), 0
    while (i := text.find("{", i)) != -1:
        try:
            v, _ = dec.raw_decode(text, i)
            if isinstance(v, dict):
                return v
        except json.JSONDecodeError:
            pass
        i += 1
    return {}


def brain_for(cfg: C.CardsConfig, model: str):
    from . import config as rhconfig
    from .brain import Brain
    gc = rhconfig.load_global()
    return Brain(rhconfig.BrainConfig(coordinator_model=model), gc)


def ask(brain, model: str, prompt: str, system: str, ctx: int | None = None) -> dict:
    """One model call; an empty or malformed reply is asked once more (X3: gemma returned {})."""
    form: dict = {}
    for _ in range(2):
        try:
            kw = {"num_ctx": ctx} if ctx else {}
            form = _obj(brain.coordinator(prompt, system, think=_think(model), **kw))
        except Exception as e:  # noqa: BLE001
            form = {"error": f"{type(e).__name__}: {e}"}
        if form and "error" not in form:
            return form
    return form


# ── the project, its papers and its run ─────────────────────────────────────────

class Run:
    """One project's grounding run: its papers' cards, and its own files under work/claims/."""

    def __init__(self, project_dir: str | Path = ".", cfg: C.CardsConfig | None = None):
        from . import config as rhconfig
        self.project = Path(project_dir).resolve()
        self.paths = rhconfig.project_paths(self.project).ensure()
        self.cfg = cfg or C.load_config()
        self.dir = Path(self.paths.work) / "claims"
        self.dir.mkdir(parents=True, exist_ok=True)

    def file(self, name: str, model: str | None = None) -> Path:
        return self.dir / (f"{name}_{C.slug(model)}.json" if model else f"{name}.json")

    def load(self, name: str, model: str | None = None, default=None):
        return C._load(self.file(name, model), default if default is not None else {})

    def dump(self, name: str, obj, model: str | None = None) -> None:
        C._dump(self.file(name, model), obj)

    def citekeys(self) -> list[str]:
        return self.load("papers", default={}).get("citekeys") or []

    def card(self, ck: str) -> C.Card:
        return C.card(self.cfg, ck)

    def cards(self) -> list[C.Card]:
        return [self.card(ck) for ck in self.citekeys()]

    def claims(self) -> dict[str, dict]:
        """Every claim on this project's cards, by id, with its paper."""
        out = {}
        for cd in self.cards():
            for c in cd.claims():
                out[c["id"]] = dict(c, paper=cd.citekey)
        return out

    def kinds(self) -> dict[str, str]:
        out = {}
        for cd in self.cards():
            votes = [_low(cd.step("kind", m), "kind") for m in self.cfg.kind_models]
            out[cd.citekey] = "review" if votes and all(v == "review" for v in votes) else "not review"
        return out


# ── init: each paper's text and record into its card ───────────────────────────

def init(project_dir=".", cfg=None) -> Run:
    """Each paper in the built corpus with text gets a card directory (an existing card is left
    as it is). Writes the run's paper list: the papers every later step works on."""
    from . import config as rhconfig
    from .facts import page_link, zotero_select
    from .models import Candidate
    from .summarize import _paper_text
    run = Run(project_dir, cfg)
    gc = rhconfig.load_global()
    recs = json.loads(Path(run.paths.corpus_json).read_text(encoding="utf-8"))
    keys, skipped, new = [], [], 0
    for d in recs:
        c = Candidate.from_dict(d)
        ck = d.get("citekey") or ""
        if not ck:
            skipped.append({"label": c.author_year(), "why": "no citekey"})
            continue
        cd = run.card(ck)
        if cd.meta() and cd.text():
            keys.append(ck)
            continue
        try:
            full = _paper_text(c)
        except Exception as e:  # noqa: BLE001
            full, why = "", f"{type(e).__name__}: {e}"
        else:
            why = "no text"
        if len(full or "") < MIN_TEXT:
            skipped.append({"citekey": ck, "label": c.author_year(), "why": why})
            continue
        from .pdfs import _TEXT_VERSION
        cd.write_text(full)
        cd.write_meta({"citekey": ck, "label": c.author_year(),
                       "title": re.sub(r"<[^>]+>", "", c.title or ""),
                       "authors": [getattr(a, "display", "") or getattr(a, "family", "") or str(a)
                                   for a in (c.authors or [])[:6]],
                       "abstract": c.abstract or "", "pdf": page_link(c, None, gc),
                       "zotero": zotero_select(c.zotero_key, gc), "text_version": _TEXT_VERSION})
        keys.append(ck)
        new += 1
    run.dump("papers", {"citekeys": sorted(keys), "skipped": skipped})
    _say(f"cards init: {len(keys)} papers ({new} new cards, {len(keys) - new} already carded), "
         f"{len(skipped)} without usable text")
    return run


# ── kind ────────────────────────────────────────────────────────────────────────

def kind(project_dir=".", model: str = "", cfg=None) -> None:
    run = Run(project_dir, cfg)
    if model not in run.cfg.kind_models:
        raise SystemExit(f"--model must be one of {list(run.cfg.kind_models)}")
    brain = None
    for cd in run.cards():
        if cd.step("kind", model):
            continue
        brain = brain or brain_for(run.cfg, model)
        m = cd.meta()
        opening = (card_read(cd.text()).get("text") or cd.text())[:6000]
        form = ask(brain, model, f"TITLE: {m['title']}\n\nABSTRACT: {m.get('abstract') or '-'}"
                   f"\n\nOPENING:\n{opening}", P.KIND_SYS, ctx=8192)
        cd.write_step("kind", form, model)
        _say(f"cards kind {model}: {m['label']} -> {_low(form, 'kind')}")


# ── extract ─────────────────────────────────────────────────────────────────────
# Papers state their contributions in the abstract, the introduction and the conclusion. A
# card reads those whole, then results passages up to the budget. A paper whose introduction or
# conclusion cannot be found, or that will not fit, is read in windows and merged ("merge").

_KEY = (r"abstract|introduction|conclusions?|concluding remarks|discussion|results|findings|"
        r"summary|references|bibliography|literature cited|acknowledge?ments?|methods?|"
        r"methodology|data|model|literature review|background|theory|appendix")
_HEAD = re.compile(rf"^\s*((?:\d+|[IVX]+)(?:\.\d+)*\.?\s+)?({_KEY})\b(.{{0,40}})$", re.I)


def _section_kind(word: str) -> str:
    w = word.lower()
    if w.startswith("conclu") or w == "summary":
        return "conclusion"
    if w in ("references", "bibliography", "literature cited"):
        return "references"
    if w.startswith("acknowledg") or w == "appendix":
        return "end"
    return {"abstract": "abstract", "introduction": "introduction", "discussion": "discussion",
            "results": "results", "findings": "results"}.get(w, "other")


def find_sections(marked: str) -> list[tuple[str, int, str]]:
    """Headings in the text, as (kind, offset, heading line)."""
    out, pos = [], 0
    for line in marked.splitlines(keepends=True):
        m = _HEAD.match(line.rstrip("\n"))
        if m:
            word, rest = m.group(2), (m.group(3) or "").strip()
            ok = (word[0].isupper() and not re.search(r"[.;:,]$", line.strip())
                  and not re.fullmatch(r"[\d\-–.]+", rest)
                  and not re.search(r"[.;,]\s+\w", rest)
                  and (not rest or rest[0].isupper() or rest[0] in "&-–:" or
                       rest.split()[0].lower() in ("and", "of", "to", "in", "on")))
            if ok:
                out.append((_section_kind(word), pos, line.strip()))
        pos += len(line)
    return out


def _marker_before(marked: str, at: int) -> str:
    last = None
    for m in _PAGE_SPLIT.finditer(marked, 0, at):
        last = m
    return f"[p.{last.group(1)}]\n" if last else ""


def _slice(marked: str, a: int, b: int, cap: int) -> str:
    piece = marked[a:min(b, a + cap)]
    return _marker_before(marked, a + 1) + piece if not piece.lstrip().startswith("[p.") else piece


def card_read(marked: str, budget: int = CARD_BUDGET) -> dict:
    """{"mode": "whole" | "focused" | "merge", "text", "windows", "sections", "read", "of"}."""
    body = marked
    secs = find_sections(marked)
    refs = [o for k, o, _ in secs if k == "references" and o > len(marked) * 0.5]
    if refs:
        body = marked[:refs[0]]
    found = [(k, o, h) for k, o, h in secs if o < len(body)]
    base = {"sections": [h for _, _, h in found][:20], "of": len(marked)}
    if len(body) <= budget:
        return dict(base, mode="whole", text=body, windows=[], read=len(body))

    def span(kind_: str, last: bool = False) -> tuple[int, int] | None:
        hits = [i for i, (k, _, _) in enumerate(found) if k == kind_]
        if not hits:
            return None
        i = hits[-1] if last else hits[0]
        end = found[i + 1][1] if i + 1 < len(found) else len(body)
        return found[i][1], end
    intro = span("introduction")
    concl = span("conclusion", last=True) or span("discussion", last=True)
    if intro and concl and concl[0] > intro[0]:
        parts = [(intro, "introduction"), (concl, "conclusion")]
        disc = span("discussion", last=True)
        if disc and disc != concl and disc[0] > intro[0]:
            parts.append((disc, "discussion"))
        chunks = {lab: _slice(body, a, b, SECTION_CAP) for (a, b), lab in parts}
        used = sum(len(v) for v in chunks.values())
        res = span("results")
        if res and used < budget:
            chunks["results"] = _slice(body, res[0], res[1], budget - used)
        elif used < budget:
            room = budget - used
            a = max(min(intro[1], intro[0] + SECTION_CAP), concl[0] - room)
            if concl[0] - a > 2000:
                chunks["results"] = _slice(body, a, concl[0], room)
        order = sorted(chunks, key=lambda lab: dict(
            introduction=0, results=1, discussion=2, conclusion=3)[lab])
        text = "\n\n[…]\n\n".join(chunks[lab] for lab in order)[:budget]
        return dict(base, mode="focused", text=text, windows=[], read=len(text))
    wins, a = [], 0
    while a < len(body):
        b = min(len(body), a + WINDOW)
        if b < len(body):
            cut = body.rfind("[p.", a + WINDOW // 2, b)
            b = cut if cut > a else b
        wins.append(_slice(body, a, b, WINDOW))
        a = b
    return dict(base, mode="merge", text="", windows=wins, read=len(body))


def _page(v) -> int | None:
    try:
        return int(str(v).strip().lstrip("p.").strip())
    except (TypeError, ValueError):
        return None


def parse_card(raw: str, pages: dict[int, str], citekey: str) -> dict:
    """The claims in a reply, every quote checked verbatim against the page text."""
    blob = _obj(raw)
    claims = []
    for c in blob.get("contributions") or []:
        if not isinstance(c, dict) or not str(c.get("claim") or "").strip():
            continue
        quotes = []
        for q in (c.get("quotes") or [])[:3]:
            q = q if isinstance(q, dict) else {"text": str(q)}
            text = str(q.get("text") or "").strip()
            if text:
                hint = _page(q.get("page"))
                quotes.append({"text": text, "page": hint, "found": find_quote(text, pages, hint)})
        claims.append({"id": f"{citekey}#{len(claims) + 1}", "claim": str(c["claim"]).strip(),
                       "quotes": quotes, "verbatim": bool(quotes) and all(q["found"] for q in quotes)})
    try:
        count = int(blob.get("count"))
    except (TypeError, ValueError):
        count = None
    return {"count": count, "claims": claims}


def _card_raw(brain, model: str, meta: dict, marked: str) -> tuple[str, str]:
    head = f"PAPER: {meta['label']}: {meta['title']}\nABSTRACT: {meta.get('abstract') or '(none)'}\n\n"
    read = card_read(marked)
    if read["mode"] != "merge":
        return brain.coordinator(head + read["text"], P.CARD_SYS, think=_think(model)), read["mode"]
    cands = []
    for n, win in enumerate(read["windows"], 1):
        raw = brain.coordinator(head + win, P.CARD_SYS + P.WINDOW_NOTE.format(
            n=n, total=len(read["windows"])), think=_think(model))
        cands += [c for c in (_obj(raw).get("contributions") or []) if isinstance(c, dict)]
    listing = json.dumps({"candidates": cands}, ensure_ascii=False)
    return brain.coordinator(head + listing, P.MERGE_SYS, think=_think(model)), "merge"


def extract(project_dir=".", part: int = 0, parts: int = 1, cfg=None) -> None:
    run = Run(project_dir, cfg)
    model = run.cfg.extractor
    brain = None
    todo = [cd for i, cd in enumerate(run.cards()) if i % parts == part]
    for cd in todo:
        if cd.extracted_by(model):
            continue
        brain = brain or brain_for(run.cfg, model)
        meta, text = cd.meta(), cd.text()
        t0 = time.time()
        try:
            raw, mode = _card_raw(brain, model, meta, text)
            got = parse_card(raw, page_texts(text), cd.citekey)
            meta.update(claims=got["claims"], count=got["count"], mode=mode, error=None)
        except Exception as e:  # noqa: BLE001
            meta.update(claims=[], count=None, error=f"{type(e).__name__}: {e}")
        meta.update(extractor=model, extract_secs=round(time.time() - t0, 1))
        cd.write_meta(meta)
        _say(f"cards extract: {meta['label']} — {len(meta['claims'])} claims in {meta['extract_secs']}s")


# ── plant: this run's step-2 and cited canaries ─────────────────────────────────

def plant(project_dir=".", cfg=None) -> dict:
    run = Run(project_dir, cfg)
    st = list(run.claims().values())
    if not st:
        raise SystemExit("cards plant: no claims yet (run extract first)")
    n = K.how_many(len(st))
    s2 = K.plant(K.STEP2_KINDS, st, n, "S2")
    papers = {cd.citekey: {"text": cd.text(), "authors": (cd.meta() or {}).get("authors")}
              for cd in run.cards()}
    cited = K.plant_cited(papers, n, "OWN")
    run.dump("canaries", {"S2": {c["id"]: c for c in s2}, "cited": {c["id"]: c for c in cited}})
    _say(f"cards plant: {len(s2)} step-2 canaries {dict(Counter(c['canary'] for c in s2))}, "
         f"{len(cited)} cited, for {len(st)} claims")
    return {"S2": s2, "cited": cited}


# ── reread ──────────────────────────────────────────────────────────────────────

def _raw_pages(marked: str) -> dict[int, str]:
    parts = _PAGE_SPLIT.split(marked or "")
    return {int(parts[i]): parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}


def verify_pages(c: dict, full: str, cap: int = 24000) -> dict[int, str]:
    """The quoted pages and the pages either side: what a verifier reads."""
    raw = _raw_pages(full)
    want: list[int] = []
    for q in c.get("quotes") or []:
        p = q.get("found") or q.get("page")
        if p:
            for x in (p - 1, p, p + 1):
                if x in raw and x not in want:
                    want.append(x)
    if not want:
        want = sorted(raw)[:3]
    out, size = {}, 0
    for p in sorted(want):
        if size + len(raw[p]) > cap and out:
            break
        out[p] = raw[p]
        size += len(raw[p])
    return out


def _statement(c: dict) -> str:
    qs = "\n".join(f'  - "{q["text"]}" (p.{q.get("found") or q.get("page") or "?"})'
                   for q in c.get("quotes") or [])
    return f"claim: {c['claim']}\nquotes:\n{qs or '  (none)'}"


def reread_ok(form: dict | None) -> bool:
    return _low(form, "supported") == "yes" and _low(form, "scope") == "matches"


def _queue(real: list[str], planted: list[str]) -> list[str]:
    """The real items in order, each canary at a fixed random place among them."""
    q = list(real)
    for k in sorted(planted):
        q.insert(random.Random(k).randrange(len(q) + 1), k)
    return q


def reread(project_dir=".", model: str = "", cfg=None) -> None:
    run = Run(project_dir, cfg)
    if model not in run.cfg.verifiers:
        raise SystemExit(f"--model must be one of {list(run.cfg.verifiers)}")
    st, can = run.claims(), run.load("canaries").get("S2") or {}
    answers = {cd.citekey: cd.step("reread", model) for cd in run.cards()}
    can_ans = run.load("canary_reread", model)
    meta = {cd.citekey: cd.meta() for cd in run.cards()}
    text = {}
    brain, n, dirty = None, 0, set()
    for key in _queue(sorted(st), list(can)):
        c = can.get(key) or st[key]
        ck = c["paper"]
        done = can_ans if key in can else answers[ck]
        if key in done:
            continue
        brain = brain or brain_for(run.cfg, model)
        text.setdefault(ck, run.card(ck).text())
        m = meta[ck]
        shown = "\n\n".join(f"[p.{pg}]\n{t}" for pg, t in verify_pages(c, text[ck]).items())
        authors = "; ".join(map(str, m.get("authors") or []))
        done[key] = ask(brain, model, f"PAPER: {m['label']}: {m['title']} ({authors})\n\n"
                        f"STATEMENT\n{_statement(c)}\n\nPAGES\n{shown}", P.REREAD_SYS)
        dirty.add("canaries" if key in can else ck)
        n += 1
        if n % SAVE_EVERY == 0:
            _save_answers(run, "reread", model, answers, can_ans, dirty)
            _say(f"cards reread {model}: {n} answered")
    _save_answers(run, "reread", model, answers, can_ans, dirty)
    _say(f"cards reread {model}: done, {n} answered this run")


def _save_answers(run: Run, step: str, model: str, answers: dict, can_ans: dict, dirty: set) -> None:
    for ck in sorted(dirty - {"canaries"}):
        run.card(ck).write_step(step, answers[ck], model)
    if "canaries" in dirty:
        run.dump(f"canary_{step}", can_ans, model)
    dirty.clear()


# ── locate, and the step-4 canaries and 2b items ────────────────────────────────

_ELISION = re.compile(r"\s*(?:\.\s?\.\s?\.|…)\s*")


def locate_parts(span: str, full: str) -> dict:
    """Each part of an elided span on its own; keep the parts found, record the rest."""
    parts = [p for p in _ELISION.split(span) if len(p.split()) >= 3] or [span]
    passages, dropped, nearest = [], [], []
    for part in parts:
        r = locate_passage(part, full, min_share=MIN_SHARE)
        if r["found"]:
            for p in r["passages"]:
                if all((p["start"], p["end"]) != (q["start"], q["end"]) for q in passages):
                    passages.append(p)
        else:
            dropped.append(part)
            nearest += r["nearest"][:1]
    return {"passages": passages, "dropped": dropped, "nearest": nearest, "parts": len(parts)}


def _merge_context(full: str, ps: list[dict]) -> list[dict]:
    """The passages widened two sentences each side (where citations sit), overlaps merged."""
    wide = [K.context_passage(full, p["start"], p["end"]) for p in ps]
    merged: list[dict] = []
    for p in sorted(wide, key=lambda x: x["start"]):
        if merged and p["start"] <= merged[-1]["end"]:
            last = merged[-1]
            if p["end"] > last["end"]:
                last["end"] = p["end"]
                last["text"] = K.context_passage(full, last["start"], last["end"], 0)["text"]
        else:
            merged.append(p)
    return merged


def locate(project_dir=".", cfg=None) -> dict:
    run = Run(project_dir, cfg)
    V = run.cfg.verifiers
    passages_of: dict[str, list] = {}
    for cd in run.cards():
        full, loc = cd.text(), cd.step("located")
        spans, own = loc.get("spans") or {}, loc.get("own_items") or {}
        answers = {m: cd.step("reread", m) for m in V}
        for c in cd.claims():
            cid = c["id"]
            for m in V:
                key = f"{m}|{cid}"
                span = str((answers[m].get(cid) or {}).get("span") or "").strip()
                if key in spans and spans[key].get("span") == span:
                    continue
                spans[key] = {"verifier": m, "cid": cid, "span": span,
                              **(locate_parts(span, full) if span else
                                 {"passages": [], "dropped": [], "nearest": [], "parts": 0})}
            ps = [p for m in V for p in spans[f"{m}|{cid}"]["passages"]]
            if ps:
                own[cid] = _merge_context(full, ps)
                passages_of[cid] = ps
        cd.write_step("located", {"min_share": MIN_SHARE, "spans": spans, "own_items": own})
    st = list(run.claims().values())
    s4 = K.plant(K.STEP4_KINDS, st, K.how_many(len(st)), "S4", passages=passages_of)
    can = run.load("canaries")
    can["S4"] = {c["id"]: c for c in s4}
    run.dump("canaries", can)
    _say(f"cards locate: {len(passages_of)} of {len(st)} claims have a passage; "
         f"{len(s4)} step-4 canaries {dict(Counter(c['canary'] for c in s4))}")
    return {"located": len(passages_of), "S4": len(s4)}


# ── confirm and own ─────────────────────────────────────────────────────────────

def _render_passages(ps: list[dict]) -> str:
    return "\n\n".join(f"[p.{p.get('page') or '?'}] {p['text']}" for p in ps)


def _abstract_or_opening(meta: dict, full: str) -> str:
    ab = (meta.get("abstract") or "").strip()
    if not ab:
        ab = " ".join(re.sub(r"\[p\.\d+\]", " ", full or "")[:1500].split()) + " ..."
    return ab


def _context(meta: dict, full: str) -> str:
    return f"TITLE: {meta['title']}\nABSTRACT: {_abstract_or_opening(meta, full)}"


def _identity(meta: dict, full: str, kind_: str) -> str:
    authors = "; ".join(map(str, meta.get("authors") or []))
    return (f"TITLE: {meta['title']}\nAUTHORS: {authors}\nYEAR: {meta.get('label', '')}\n"
            f"KIND: {kind_}\nABSTRACT: {_abstract_or_opening(meta, full)}")


def confirm_ok(form: dict | None) -> bool:
    return (_low(form, "says") == "yes" and _low(form, "direction") in ("same", "n/a")
            and _low(form, "numbers") in ("agree", "none"))


def confirm(project_dir=".", model: str = "", cfg=None) -> None:
    """`model` judges the claim against the OTHER verifier's located passage, for every claim
    the other passed at step 2; and this run's step-4 canaries."""
    run = Run(project_dir, cfg)
    V = run.cfg.verifiers
    if model not in V:
        raise SystemExit(f"--model must be one of {list(V)}")
    other = V[1] if model == V[0] else V[0]
    can = run.load("canaries").get("S4") or {}
    can_ans = run.load("canary_confirm", model)
    brain, n = None, 0
    real = []
    for cd in run.cards():
        loc, rr = cd.step("located").get("spans") or {}, cd.step("reread", other)
        for c in cd.claims():
            key = f"{other}|{c['id']}"
            if (loc.get(key) or {}).get("passages") and reread_ok(rr.get(c["id"])):
                real.append((cd.citekey, key))
    order = _queue([f"{ck}\t{key}" for ck, key in real], list(can))
    answers, metas, texts, dirty = {}, {}, {}, set()
    for item in order:
        if item in can:
            c, ck, done = can[item], can[item]["paper"], can_ans
            claim, ps, key = c["claim"], c["passages"], item
        else:
            ck, key = item.split("\t")
            answers.setdefault(ck, run.card(ck).step("confirm", model))
            done = answers[ck]
            cid = key.split("|", 1)[1]
            claim = next(x["claim"] for x in run.card(ck).claims() if x["id"] == cid)
            ps = run.card(ck).step("located")["spans"][key]["passages"]
        if key in done:
            continue
        brain = brain or brain_for(run.cfg, model)
        metas.setdefault(ck, run.card(ck).meta())
        texts.setdefault(ck, run.card(ck).text())
        done[key] = ask(brain, model, f"{_context(metas[ck], texts[ck])}\n\nCLAIM\n{claim}\n\n"
                        f"PASSAGES\n{_render_passages(ps)}", P.CONFIRM_SYS, ctx=8192)
        dirty.add("canaries" if item in can else ck)
        n += 1
        if n % SAVE_EVERY == 0:
            _save_answers(run, "confirm", model, answers, can_ans, dirty)
            _say(f"cards confirm {model}: {n} answered")
    _save_answers(run, "confirm", model, answers, can_ans, dirty)
    _say(f"cards confirm {model}: done, {n} answered this run")


def own(project_dir=".", model: str = "", cfg=None) -> None:
    """Is each claim with a located passage the paper's own, and in what role; and this run's
    cited canaries."""
    run = Run(project_dir, cfg)
    if model not in run.cfg.verifiers:
        raise SystemExit(f"--model must be one of {list(run.cfg.verifiers)}")
    kinds = run.kinds()
    can = run.load("canaries").get("cited") or {}
    can_ans = run.load("canary_own", model)
    real = [f"{cd.citekey}\t{cid}" for cd in run.cards()
            for cid in sorted(cd.step("located").get("own_items") or {})]
    answers, metas, texts, dirty = {}, {}, {}, set()
    brain, n = None, 0
    for item in _queue(real, list(can)):
        if item in can:
            c = can[item]
            ck, done, key, claim, ps = c["paper"], can_ans, item, c["claim"], c["passages"]
        else:
            ck, key = item.split("\t")
            answers.setdefault(ck, run.card(ck).step("own", model))
            done = answers[ck]
            claim = next(x["claim"] for x in run.card(ck).claims() if x["id"] == key)
            ps = run.card(ck).step("located")["own_items"][key]
        if key in done:
            continue
        brain = brain or brain_for(run.cfg, model)
        metas.setdefault(ck, run.card(ck).meta())
        texts.setdefault(ck, run.card(ck).text())
        k = kinds.get(ck, "not review")
        sys_ = P.OWN_SYS_REVIEW if k == "review" else P.OWN_SYS_NOT_REVIEW
        done[key] = ask(brain, model, f"{_identity(metas[ck], texts[ck], k)}\n\nCLAIM\n{claim}\n\n"
                        f"PASSAGES\n{_render_passages(ps)}", sys_, ctx=8192)
        dirty.add("canaries" if item in can else ck)
        n += 1
        if n % SAVE_EVERY == 0:
            _save_answers(run, "own", model, answers, can_ans, dirty)
            _say(f"cards own {model}: {n} answered")
    _save_answers(run, "own", model, answers, can_ans, dirty)
    _say(f"cards own {model}: done, {n} answered this run")


# ── close ───────────────────────────────────────────────────────────────────────

def _why4(form: dict | None) -> str | None:
    if form is None:
        return "not run"
    if confirm_ok(form):
        return None
    bad = [k for k, ok in (("says", _low(form, "says") == "yes"),
                           ("direction", _low(form, "direction") in ("same", "n/a")),
                           ("numbers", _low(form, "numbers") in ("agree", "none"))) if not ok]
    return ", ".join(f"{k} = {_low(form, k) or 'missing'}" for k in bad) or "error"


def failed_at(cid: str, V: tuple, R: dict, loc: dict, Cf: dict, O: dict) -> str | None:
    """Where a claim fails, or None when every check passed for both verifiers."""
    for m in V:
        f = R[m].get(cid) or {}
        for k, good in (("supported", "yes"), ("scope", "matches")):
            if _low(f, k) != good:
                return f"step 2 ({m.split(':')[0]}): {k} = {_low(f, k) or 'missing'}"
    for m in V:
        lk = loc.get(f"{m}|{cid}") or {}
        if not lk.get("span"):
            return f"step 3 ({m.split(':')[0]}): no span"
        if not lk.get("passages"):
            return f"step 3 ({m.split(':')[0]}): span not found"
    for judge, finder in ((V[0], V[1]), (V[1], V[0])):
        w = _why4(Cf[judge].get(f"{finder}|{cid}"))
        if w:
            return f"step 4 ({judge.split(':')[0]} on {finder.split(':')[0]}'s passage): {w}"
    for m in V:
        f = O[m].get(cid)
        if f is None or _low(f, "own_finding") != "yes":
            src = str((f or {}).get("source") or "").strip()
            return (f"step 2b ({m.split(':')[0]}): own_finding = {_low(f, 'own_finding') or 'missing'}"
                    + (f"; source: {src}" if src else ""))
    return None


def _merged_passages(full: str, ps: list[dict]) -> list[dict]:
    """The two verifiers' passages, overlapping ones merged and re-read from the paper."""
    spans = sorted((p["start"], p["end"], p.get("page"), p.get("pages")) for p in ps)
    merged: list[list] = []
    for s, e, pg, pages in spans:
        if merged and s < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e, pg, pages])
    return [{"page": pg, "pages": pages or [pg, pg], "start": s, "end": e,
             "text": _passage_text(full[s:e])} for s, e, pg, pages in merged]


def close_card(cd: C.Card, V: tuple) -> dict:
    """The card's verdicts: each claim confirmed (with its role and passages) or where it failed.
    Belongs to the paper: written into the card, the same for every project."""
    full = cd.text()
    loc = cd.step("located").get("spans") or {}
    R = {m: cd.step("reread", m) for m in V}
    Cf = {m: cd.step("confirm", m) for m in V}
    O = {m: cd.step("own", m) for m in V}
    out = {}
    for c in cd.claims():
        cid = c["id"]
        why = failed_at(cid, V, R, loc, Cf, O)
        roles = [_low(O[m].get(cid), "role") for m in V]
        rec = {"claim": c["claim"], "confirmed": why is None,
               "role": roles[0] if roles[0] == roles[1] and roles[0] in ROLES else None}
        if why is None:
            rec["passages"] = _merged_passages(full, [p for m in V for p in loc[f"{m}|{cid}"]["passages"]])
        else:
            rec["failed_at"] = why
            if why.startswith("step 2b"):
                rec["source"] = {m: str((O[m].get(cid) or {}).get("source") or "").strip() for m in V}
        out[cid] = rec
    cd.write_step("verdict", {"verifiers": list(V), "claims": out})
    return out


def close(project_dir=".", cfg=None) -> dict:
    """Every card's verdicts, then this run's: what slipped past both verifiers, by kind and
    check, and whether any kind holds the release. Nothing re-checks the confirmed claims of a
    kind that slipped (decided 2026-11-13): a lexical direction check caught three claims on
    FirmPathways and all three were right, and a third model would add its own errors to mop up
    a small remainder the hold line and the gate sample already cover. The slip rates go to the
    gate; if its sample finds a real error of a kind that slipped past both, revisit this."""
    run = Run(project_dir, cfg)
    V = run.cfg.verifiers
    verdicts = {}
    for cd in run.cards():
        for cid, rec in close_card(cd, V).items():
            verdicts[cid] = dict(rec, paper=cd.citekey)
    can = run.load("canaries")
    S2, S4, cited = can.get("S2") or {}, can.get("S4") or {}, can.get("cited") or {}
    a2 = {m: run.load("canary_reread", m) for m in V}
    a4 = {m: run.load("canary_confirm", m) for m in V}
    ao = {m: run.load("canary_own", m) for m in V}
    slipped2 = {k for k in S2 if all(reread_ok(a2[m].get(k)) for m in V)}
    slipped4 = {k for k in S4 if all(confirm_ok(a4[m].get(k)) for m in V)}
    slippedo = {k for k in cited if all(_low(ao[m].get(k), "own_finding") == "yes" for m in V)}
    checks = {"step 2": K.slips({k: c["canary"] for k, c in S2.items()}, slipped2),
              "step 4": K.slips({k: c["canary"] for k, c in S4.items()}, slipped4),
              "step 2b": K.slips({k: "cited" for k in cited}, slippedo)}
    holds = sorted(f"{chk}: {kind_}" for chk, per in checks.items() for kind_, s in per.items() if s["holds"])
    confirmed = [cid for cid, r in verdicts.items() if r["confirmed"]]
    by_stage = Counter(r["failed_at"].split(" (")[0] for r in verdicts.values() if not r["confirmed"])
    summary = {
        "date": time.strftime("%Y-%m-%d"), "verifiers": list(V), "extractor": run.cfg.extractor,
        "claims": len(verdicts), "confirmed": len(confirmed),
        "papers": len(run.citekeys()),
        "papers_with_a_confirmed_claim": len({verdicts[c]["paper"] for c in confirmed}),
        "rejected_by_stage": dict(by_stage),
        "canaries": checks, "holds": holds,
        "roles": dict(Counter(verdicts[c]["role"] or "-" for c in confirmed))}
    run.dump("close", summary)
    _say(f"cards close: {len(confirmed)}/{len(verdicts)} confirmed on "
         f"{summary['papers_with_a_confirmed_claim']}/{summary['papers']} papers; rejected "
         f"{dict(by_stage)}; "
         + (f"HOLD ({'; '.join(holds)})" if holds else "no kind holds the release"))
    return summary


STEPS = ("init", "kind", "extract", "plant", "reread", "locate", "confirm", "own", "close")


def _gate(directory, seed):
    from . import gate
    return gate.build(directory, seed=seed)


def main(step: str, directory: str = ".", model: str | None = None, part: int = 0, parts: int = 1,
         seed: int | None = None) -> int:
    if model in ("1", "2"):                 # the configured model, as a queued task names it
        cfg = C.load_config()
        model = (cfg.kind_models if step == "kind" else cfg.verifiers)[int(model) - 1]
    fn = {"init": lambda: init(directory), "kind": lambda: kind(directory, model or ""),
          "extract": lambda: extract(directory, part, parts), "plant": lambda: plant(directory),
          "reread": lambda: reread(directory, model or ""), "locate": lambda: locate(directory),
          "confirm": lambda: confirm(directory, model or ""), "own": lambda: own(directory, model or ""),
          "close": lambda: close(directory), "gate": lambda: _gate(directory, seed)}[step]
    from . import runlog
    runlog.start()
    fn()
    return 0

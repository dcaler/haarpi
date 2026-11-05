"""Bench X2: paper cards (PLAN_paper_cards.html, 2026-11-05). Experiment code, not pushed.

Several local models each build a *paper card* (the paper's own contributions, each with its
domain, method, conditions and verbatim quotes) for 16 FirmPathways papers. Every model then
judges other families' pairs ("same contribution?") and verifies other families' contributions
against the text, with planted canaries. A person assesses the statements and a sample of the
pairs once, paper by paper, and `unblind` scores every model and every 2- or 3-model lineup.

  haarpi bench prepare --x2                         freeze the 16-paper fixture (embedder)
  haarpi bench run --coordinator M --tasks X2E      M builds its cards
  haarpi bench pair --x2                            pair candidates, rotate roles, plant canaries
  haarpi bench run --coordinator M --tasks X2J X2V  M judges and verifies its assigned items
  haarpi bench judge --x2                           write EVAL_cards.html
  haarpi bench unblind --x2 EVAL_cards_DCR.json     score it
"""

from __future__ import annotations

import itertools
import json
import random
import re
import time
from pathlib import Path
from types import SimpleNamespace

from . import bench as B

# ── the lineup ──────────────────────────────────────────────────────────────────

FAMILIES = {
    "qwen3.6:27b-16k": "qwen",
    "gemma4:31b-it-q4_K_M": "gemma",
    "granite4.2:30b": "granite",
    "gpt-oss:20b": "gpt-oss",
    "olmo-3.1:32b-instruct": "olmo",
    "llama3.3:latest": "llama",
}

# Chosen with Cale, 2026-11-05: by stratum, not by the mind map's score (that score is built on
# the old facts and ranks topic vocabulary, so the funding papers sat in the outer band).
# Page order interleaves the strata, so stopping early still leaves a spread.
X2_PAPERS = [
    ("kennedyeffects2024", "funding"), ("schotStrategic2008", "systems"),
    ("mccarthyManufacturing2004", "landscapes"), ("devisserEmpirical2014", "cross-domain"),
    ("dekkerEffects", "funding"), ("geelssectoral2004", "systems"),
    ("kauffmanTechnological1995", "landscapes"), ("bosmanProgressive2018", "cross-domain"),
    ("gaoInnovationFirmSurvival2023", "funding"), ("hekkertFunctions2007", "systems"),
    ("levinthalLearning1996", "landscapes"), ("aletiAnalysing2017", "cross-domain"),
    ("livieratosUnlocking2026", "funding"), ("peltoniemiBusiness2005", "systems"),
    ("frenkenfitness2006", "landscapes"), ("mazzucatoFinancing2018", "funding"),
]

PAIR_COS = 0.70           # a candidate pair: nearest 2 in each other card at or above this
DUP_COS = 0.85            # two contributions in ONE card this close are a parsimony miss
CANARY_SHARE = 0.05
JUDGES_PER_PAIR = 2
VERIFIERS_PER_ITEM = 2

FIXTURE = "x2.json"
ASSIGN = "x2_assign.json"
KEY = "x2_blind_key.json"


def family(model: str) -> str:
    return FAMILIES.get(model) or re.split(r"[:\d.]", model, maxsplit=1)[0]


def _think(model: str) -> bool:
    # gpt-oss always reasons; asking for it keeps the reasoning out of the answer. The rest
    # run without a scratchpad, as qwen3.6 did in X1.
    return model.startswith("gpt-oss")


# ── what a card reads ───────────────────────────────────────────────────────────
# Papers state their contributions in the abstract, the introduction and the conclusion. A
# card reads those whole (the abstract from the Zotero record), then results passages up to the
# budget. A paper whose introduction or conclusion cannot be found, or that will not fit, is read
# in windows and merged instead ("merge"): never a partial read chosen by retrieval.

CARD_BUDGET = 32000          # characters of paper text per card call (~8k tokens): one card
WINDOW = 24000               # a merge window
SECTION_CAP = 12000          # an introduction or conclusion longer than this is cut at the cap

_KEY = (r"abstract|introduction|conclusions?|concluding remarks|discussion|results|findings|"
        r"summary|references|bibliography|literature cited|acknowledge?ments?|methods?|"
        r"methodology|data|model|literature review|background|theory|appendix")
_HEAD = re.compile(rf"^\s*((?:\d+|[IVX]+)(?:\.\d+)*\.?\s+)?({_KEY})\b(.{{0,40}})$", re.I)


def _kind(word: str) -> str:
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
    """Headings in the text, as (kind, offset, heading line). A heading is short, capitalised,
    ends without punctuation, and is not a table label ("Model 2")."""
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
                out.append((_kind(word), pos, line.strip()))
        pos += len(line)
    return out


def _marker_before(marked: str, at: int) -> str:
    from rabbithole.facts import _PAGE_SPLIT
    last = None
    for m in _PAGE_SPLIT.finditer(marked, 0, at):
        last = m
    return f"[p.{last.group(1)}]\n" if last else ""


def _slice(marked: str, a: int, b: int, cap: int) -> str:
    """Text from a to b (at most `cap` characters), opened with the page it starts on."""
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

    def span(kind: str, last: bool = False) -> tuple[int, int] | None:
        hits = [i for i, (k, _, _) in enumerate(found) if k == kind]
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
            # No results heading: the pages leading into the conclusion carry the findings.
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
        if b < len(body):                         # break at a page marker near the end
            cut = body.rfind("[p.", a + WINDOW // 2, b)
            b = cut if cut > a else b
        wins.append(_slice(body, a, b, WINDOW))
        a = b
    return dict(base, mode="merge", text="", windows=wins, read=len(body))


# ── prepare ─────────────────────────────────────────────────────────────────────

def prepare_x2(project: Path = B.FIRMPATHWAYS, out: Path | None = None) -> Path:
    """Freeze the 16 papers: the card read (see `card_read`; no retrieval, no model), title,
    authors, abstract, stratum, and the Zotero links. A card belongs to the paper, not to one
    project, so nothing here depends on FirmPathways' questions."""
    from rabbithole import config as rhconfig
    from rabbithole import facts as F
    from rabbithole.models import Candidate
    from rabbithole.summarize import _paper_text

    out = Path(out or B.BENCH_ROOT / "fixtures")
    out.mkdir(parents=True, exist_ok=True)
    lit = project / "litReview"
    gc = rhconfig.load_global()
    records = {d.get("citekey"): d for d in json.loads(
        (lit / "work" / "corpus.json").read_text(encoding="utf-8"))}
    papers = []
    for key, stratum in X2_PAPERS:
        d = records.get(key)
        if d is None:
            B._say(f"X2: {key} not in the corpus — skipped")
            continue
        c = Candidate.from_dict(d)
        full = _paper_text(c)
        read = card_read(full)
        papers.append({"citekey": key, "stratum": stratum, "record": d,
                       "label": c.author_year(), "title": re.sub(r"<[^>]+>", "", c.title or ""),
                       "authors": [getattr(a, "display", "") or getattr(a, "family", "") or str(a)
                                   for a in (c.authors or [])[:6]],
                       "abstract": c.abstract or "", "full": full, "read": read,
                       "pdf": F.page_link(c, None, gc),
                       "zotero": F.zotero_select(c.zotero_key, gc)})
        B._say(f"X2: {c.author_year()} — {read['mode']}, {read['read']:,} of {len(full):,} chars"
               + (f", {len(read['windows'])} windows" if read["windows"] else ""))
    fp = out / FIXTURE
    B._dump(fp, {"project": str(project), "papers": papers})
    return fp


# ── prompts and forms ───────────────────────────────────────────────────────────

CARD_SYS = """You read one academic paper and state what it establishes: its contributions. A
contribution is something this paper itself puts forward and could be cited for: a finding, a
result, a model or method it introduces, a dataset, a framework, or an argument it makes.

NOT contributions: background; definitions or concepts taken from other work; findings the paper
cites from others; the paper's aims or plans; settings, parameters or software versions; a single
statistic on its own (put it under the contribution it supports instead).

First decide how many distinct contributions the paper makes. Do not split one finding into
several, and do not pad. A short paper may make one.

For each contribution give:
- "claim": one or two sentences. Name the subject explicitly (never "this study", "the model",
  "we"). State what the paper shows, at the reach it shows it: no wider (do not generalise beyond
  its domain or conditions) and no narrower.
- "domain": the system, population or setting studied.
- "method": how the paper establishes it (for example "agent-based simulation calibrated to
  programme data", "survey of 312 firms", "formal model", "literature review").
- "conditions": limits under which it holds, as the paper states them, or "none stated".
- "quotes": 1 to 3 passages copied exactly from the text (8 to 40 words each) that support it,
  each with its page number from the [p.N] markers.
- "support": up to 3 specifics (numbers, sub-results), each with an exact quote and its page.

Reply with JSON only:
{"count": N, "contributions": [{"claim": "", "domain": "", "method": "", "conditions": "",
 "quotes": [{"text": "", "page": 0}], "support": [{"text": "", "quote": "", "page": 0}]}]}"""

WINDOW_NOTE = """

This is part {n} of {total} of the paper. List the contributions evident in this part. Another
pass will merge the parts, so do not worry about the whole paper; the count is for this part."""

MERGE_SYS = """You are given the candidate contributions found in each part of one academic
paper, and its abstract. Write the paper's card: merge candidates that are the same contribution,
drop any that are background, cited findings, aims or settings, and keep the rest. Do not invent:
every claim must come from the candidates, and every quote must be copied exactly from a
candidate's quotes, with its page. Then state how many distinct contributions the paper makes.

Reply with JSON only, in the same form as the candidates:
{"count": N, "contributions": [{"claim": "", "domain": "", "method": "", "conditions": "",
 "quotes": [{"text": "", "page": 0}], "support": [{"text": "", "quote": "", "page": 0}]}]}"""

JUDGE_SYS = """Two statements were extracted independently from the same paper. Decide whether
they assert the same contribution. Ignore wording and style; compare only content. Answer every
field.

Reply with JSON only:
{"finding": "same" | "overlapping" | "different",
 "direction": "same" | "opposite" | "n/a",
 "domain": "same" | "A narrower" | "B narrower" | "different",
 "conditions": "same" | "A narrower" | "B narrower" | "different",
 "numbers": "agree" | "disagree" | "none",
 "reason": "one sentence"}"""

VERIFY_SYS = """You check one statement against the paper it claims to come from. Read the pages
given. Answer strictly: "cant_tell" is allowed and counts against the statement.

- "supported": do the pages support the claim as stated?
- "own_finding": is it this paper's own finding, not one the paper cites from other work?
- "scope": does the claim keep the reach the paper shows ("matches"), claim more ("overreaches")
  or less ("underreaches")? Check the domain and conditions too.
- "span": copy, exactly, the passage that best supports it.

Reply with JSON only:
{"supported": "yes" | "no" | "cant_tell",
 "own_finding": "yes" | "no" | "cant_tell",
 "scope": "matches" | "overreaches" | "underreaches" | "cant_tell",
 "span": "",
 "reason": "one sentence"}"""


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


def _page(v) -> int | None:
    try:
        return int(str(v).strip().lstrip("p.").strip())
    except (TypeError, ValueError):
        return None


def parse_card(raw: str, pages: dict[int, str], prefix: str) -> dict:
    """A card from the reply, every quote checked verbatim against the page text."""
    from rabbithole.facts import find_quote
    blob = _obj(raw)
    contribs = []
    for i, c in enumerate(blob.get("contributions") or []):
        if not isinstance(c, dict) or not str(c.get("claim") or "").strip():
            continue
        quotes = []
        for q in (c.get("quotes") or [])[:3]:
            q = q if isinstance(q, dict) else {"text": str(q)}
            text = str(q.get("text") or "").strip()
            if text:
                hint = _page(q.get("page"))
                quotes.append({"text": text, "page": hint,
                               "found": find_quote(text, pages, hint)})
        support = []
        for s in (c.get("support") or [])[:3]:
            if isinstance(s, dict) and str(s.get("text") or "").strip():
                qt = str(s.get("quote") or "").strip()
                support.append({"text": str(s["text"]).strip(), "quote": qt,
                                "page": _page(s.get("page")),
                                "found": find_quote(qt, pages, _page(s.get("page")))
                                if qt else None})
        contribs.append({"id": f"{prefix}|{len(contribs)}",
                         "claim": str(c["claim"]).strip(),
                         "domain": str(c.get("domain") or "").strip(),
                         "method": str(c.get("method") or "").strip(),
                         "conditions": str(c.get("conditions") or "").strip(),
                         "quotes": quotes, "support": support,
                         "verbatim": bool(quotes) and all(q["found"] for q in quotes)})
    try:
        count = int(blob.get("count"))
    except (TypeError, ValueError):
        count = None
    return {"count": count, "contributions": contribs}


def _render(c: dict, tag: str = "") -> str:
    qs = "\n".join(f'  - "{q["text"]}" (p.{q.get("found") or q.get("page") or "?"})'
                   for q in c.get("quotes") or [])
    return (f"{tag}claim: {c['claim']}\n{tag}domain: {c.get('domain') or '-'}\n"
            f"{tag}method: {c.get('method') or '-'}\n{tag}conditions: {c.get('conditions') or '-'}\n"
            f"{tag}quotes:\n{qs or '  (none)'}")


def judge_rule(form: dict) -> bool:
    """SAME only when every field agrees: all green."""
    norm = {k: str(form.get(k) or "").strip().lower() for k in
            ("finding", "direction", "domain", "conditions", "numbers")}
    return (norm["finding"] == "same" and norm["direction"] in ("same", "n/a")
            and norm["domain"] == "same" and norm["conditions"] == "same"
            and norm["numbers"] != "disagree")


def verify_rule(form: dict, pages: dict[int, str]) -> bool:
    """PASS only on yes, yes, matches, and a span found verbatim on the pages shown."""
    from rabbithole.facts import find_quote
    f = {k: str(form.get(k) or "").strip().lower() for k in ("supported", "own_finding", "scope")}
    span = str(form.get("span") or "").strip()
    return (f["supported"] == "yes" and f["own_finding"] == "yes" and f["scope"] == "matches"
            and bool(span) and find_quote(span, pages) is not None)


def _raw_pages(marked: str) -> dict[int, str]:
    from rabbithole.facts import _PAGE_SPLIT
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


# ── canaries ────────────────────────────────────────────────────────────────────

_DIRECTION = [("increases", "decreases"), ("increase", "decrease"), ("increased", "decreased"),
              ("higher", "lower"), ("more", "less"), ("raises", "lowers"),
              ("positive", "negative"), ("improves", "worsens"), ("reduces", "increases"),
              ("greater", "smaller"), ("outweighs", "is outweighed by"),
              ("benefits", "harms"), ("faster", "slower"), ("larger", "smaller")]
_NUM = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)(?!\w|\.\d)")


def _bump(n: str) -> str:
    if "." in n:
        a, b = n.split(".")
        return f"{int(a) + 7}.{b}"
    v = int(n)
    return str(v + 3 if v < 10 else v * 2 + 1)


def mutate(c: dict, kind: str, others: list[dict]) -> dict | None:
    """A planted bad copy of `c`, or None when this kind cannot be made from it."""
    m = json.loads(json.dumps(c))
    m["canary"] = kind
    if kind == "number":
        nums = [x for x in _NUM.findall(c["claim"]) if not 1500 <= float(x) <= 2100]
        if not nums:
            return None
        m["claim"] = c["claim"].replace(nums[0], _bump(nums[0]), 1)
        return m
    if kind == "direction":
        for a, b in _DIRECTION:
            for x, y in ((a, b), (b, a)):
                pat = re.compile(rf"\b{re.escape(x)}\b", re.I)
                if pat.search(c["claim"]):
                    m["claim"] = pat.sub(y, c["claim"], count=1)
                    return m
        return None
    pool = [o for o in others if o.get("domain") and o["domain"] != c.get("domain")]
    if not pool:
        return None
    o = random.Random(f"{c['id']}-{kind}").choice(pool)
    if kind == "domain":
        m["domain"] = o["domain"]
        return m
    if kind == "foreign":
        for k in ("claim", "domain", "method", "conditions"):
            m[k] = o.get(k, "")
        return m
    return None


# ── pairing and role rotation ───────────────────────────────────────────────────

def _cards() -> dict[str, dict]:
    """model -> its X2E result, for every finished extraction run."""
    out = {}
    for fp in sorted((B.BENCH_ROOT / "results").glob("x2e_*.json")):
        r = json.loads(fp.read_text(encoding="utf-8"))
        if "cards" in (r.get("X2E") or {}):
            out[r["model"]] = r["X2E"]
    return out


def _least(load: dict[str, int], fams: list[str], k: int, seed: str) -> list[str]:
    order = sorted(fams, key=lambda f: (load[f], random.Random(seed + f).random()))
    pick = order[:k]
    for f in pick:
        load[f] += 1
    return pick


def build_assignment(cards: dict[str, dict], cos, k_near: int = 2) -> dict:
    """Candidate pairs (each contribution's nearest k in every other family's card, cos ≥
    PAIR_COS), within-card near-duplicates, judges and verifiers rotated over families not
    involved, and the canaries. `cos(a, b)` takes two contribution dicts."""
    models = sorted(cards)
    fam = {m: family(m) for m in models}
    fam_model = {fam[m]: m for m in models}
    papers = sorted({ck for m in models for ck in cards[m]["cards"]})
    contribs = {c["id"]: dict(c, model=m, paper=ck)
                for m in models for ck, card in cards[m]["cards"].items()
                for c in card.get("contributions") or []}
    pairs: dict[str, dict] = {}
    dups: dict[str, int] = {m: 0 for m in models}
    sims: list[float] = []
    for ck in papers:
        by_model = {m: (cards[m]["cards"].get(ck) or {}).get("contributions") or []
                    for m in models}
        for m in models:
            own = by_model[m]
            for a, b in itertools.combinations(own, 2):
                dups[m] += cos(a, b) >= DUP_COS
            for a in own:
                for m2 in models:
                    if fam[m2] == fam[m]:
                        continue
                    scored = sorted(((cos(a, b), b) for b in by_model[m2]),
                                    key=lambda t: -t[0])[:k_near]
                    for s, b in scored:
                        sims.append(round(s, 3))
                        if s >= PAIR_COS:
                            x, y = sorted((a["id"], b["id"]))
                            pairs.setdefault(f"{x}~{y}", {"a": x, "b": y, "paper": ck,
                                                          "cos": round(s, 3)})
    load = {f: 0 for f in fam_model}
    queues = {m: {"X2J": [], "X2V": []} for m in models}
    for pid, p in sorted(pairs.items()):
        free = [f for f in fam_model if f not in (fam[contribs[p["a"]]["model"]],
                                                  fam[contribs[p["b"]]["model"]])]
        p["judges"] = [fam_model[f] for f in _least(load, free, JUDGES_PER_PAIR, pid)]
        for j in p["judges"]:
            queues[j]["X2J"].append({"pid": pid})
    load = {f: 0 for f in fam_model}
    verify: dict[str, list[str]] = {}
    for cid, c in sorted(contribs.items()):
        free = [f for f in fam_model if f != fam[c["model"]]]
        verify[cid] = [fam_model[f] for f in _least(load, free, VERIFIERS_PER_ITEM, cid)]
        for v in verify[cid]:
            queues[v]["X2V"].append({"cid": cid})
    canaries: dict[str, dict] = {}
    for m in models:
        mine = [c for c in contribs.values() if fam[c["model"]] != fam[m]]
        rng = random.Random(f"canary-{m}")
        rng.shuffle(mine)
        for role, kinds in (("X2J", ("number", "direction", "domain")),
                            ("X2V", ("number", "direction", "domain", "foreign"))):
            want = max(2, round(CANARY_SHARE * len(queues[m][role])))
            made = 0
            for c in mine:
                if made >= want:
                    break
                others = [o for o in contribs.values() if o["paper"] != c["paper"]]
                # Rotate the kinds; a contribution with no number still makes another kind.
                bad = next((b for off in range(len(kinds))
                            if (b := mutate(c, kinds[(made + off) % len(kinds)], others))), None)
                if bad is None:
                    continue
                kid = f"canary|{m}|{role}|{made}"
                bad["id"] = kid
                canaries[kid] = bad
                item = {"pid": kid, "a": c["id"]} if role == "X2J" else {"cid": kid}
                queues[m][role].insert(rng.randrange(len(queues[m][role]) + 1), item)
                made += 1
    sims.sort()
    return {"models": models, "families": fam, "pairs": pairs, "verify": verify,
            "queues": queues, "canaries": canaries, "near_dups": dups,
            "cos_quantiles": {q: sims[int(q * (len(sims) - 1))] for q in (.1, .25, .5, .75, .9)}
            if sims else {}}


def pair_x2() -> Path:
    """Embed every claim, pair, rotate and plant canaries. Needs the embedder (a GPU task)."""
    cards = _cards()
    if len(cards) < 3:
        raise SystemExit(f"pair --x2: only {len(cards)} finished card runs; need at least 3")
    brain = B._rh_brain(B.BASELINE_COORDINATOR)
    texts = {c["id"]: f"{c['claim']} ({c.get('domain') or ''})"
             for r in cards.values() for card in r["cards"].values()
             for c in card.get("contributions") or []}
    ids = sorted(texts)
    vecs = dict(zip(ids, brain.embed_batch([texts[i] for i in ids])))
    out = build_assignment(cards, lambda a, b: B._cos(vecs[a["id"]], vecs[b["id"]]))
    fp = B.BENCH_ROOT / "fixtures" / ASSIGN
    B._dump(fp, out)
    B._say(f"X2 pair: {len(out['pairs'])} pairs, {len(out['verify'])} contributions, "
           f"{len(out['canaries'])} canaries; cos quantiles {out['cos_quantiles']}")
    return fp


# ── the runs ────────────────────────────────────────────────────────────────────

def build_card_raw(brain, model: str, p: dict) -> str:
    """One call for a whole or focused read; for a merge read, one call per window and a merge
    call over the candidates (quotes are checked against the full text afterwards either way)."""
    head = f"PAPER: {p['label']}: {p['title']}\nABSTRACT: {p['abstract'] or '(none)'}\n\n"
    read = p["read"]
    if read["mode"] != "merge":
        return brain.coordinator(head + read["text"], CARD_SYS, think=_think(model))
    cands = []
    for n, win in enumerate(read["windows"], 1):
        raw = brain.coordinator(head + win, CARD_SYS + WINDOW_NOTE.format(
            n=n, total=len(read["windows"])), think=_think(model))
        cands += [c for c in (_obj(raw).get("contributions") or []) if isinstance(c, dict)]
    listing = json.dumps({"candidates": cands}, ensure_ascii=False)
    return brain.coordinator(head + listing, MERGE_SYS, think=_think(model))


def task_x2e(model: str, work: Path) -> dict:
    from rabbithole.facts import page_texts
    fx = B._load(FIXTURE)
    brain = B._rh_brain(model)
    slug = B._slug(model)
    cards, secs = {}, {}
    for p in fx["papers"]:
        t0 = time.time()
        try:
            raw = build_card_raw(brain, model, p)
            card = parse_card(raw, page_texts(p["full"]), f"{slug}|{p['citekey']}")
            card["mode"] = p["read"]["mode"]
        except Exception as e:  # noqa: BLE001 — one paper's failure is a result
            card = {"count": None, "contributions": [], "error": f"{type(e).__name__}: {e}"}
        secs[p["citekey"]] = round(time.time() - t0, 1)
        cards[p["citekey"]] = card
        B._say(f"X2E {model}: {p['label']} — {len(card['contributions'])} contributions "
               f"(stated {card['count']}) in {secs[p['citekey']]}s")
    cs = [c for card in cards.values() for c in card["contributions"]]
    return {"family": family(model), "cards": cards, "secs": secs,
            "papers": len(cards), "contributions": len(cs),
            "per_paper": round(len(cs) / max(1, len(cards)), 2),
            "verbatim_pct": B._pct(sum(c["verbatim"] for c in cs), len(cs)),
            "empty_papers": sum(1 for card in cards.values() if not card["contributions"]),
            "count_matches_list_pct": B._pct(sum(1 for card in cards.values()
                                                 if card["count"] == len(card["contributions"])),
                                             len(cards)),
            "secs_per_paper": round(sum(secs.values()) / max(1, len(secs)), 1)}


def _all_contribs() -> dict[str, dict]:
    out = {}
    for m, r in _cards().items():
        for ck, card in r["cards"].items():
            for c in card.get("contributions") or []:
                out[c["id"]] = dict(c, model=m, paper=ck)
    return out


def task_x2j(model: str, work: Path) -> dict:
    asg = B._load(ASSIGN)
    contribs = _all_contribs()
    brain = B._rh_brain(model)
    out = {}
    for item in asg["queues"].get(model, {}).get("X2J", []):
        pid = item["pid"]
        if pid.startswith("canary|"):
            a, b = contribs[item["a"]], asg["canaries"][pid]
        else:
            a, b = contribs[asg["pairs"][pid]["a"]], contribs[asg["pairs"][pid]["b"]]
        if random.Random(pid).random() < 0.5:          # no position bias toward A
            a, b = b, a
        try:
            raw = brain.coordinator(f"{_render(a, 'A ')}\n\n{_render(b, 'B ')}", JUDGE_SYS,
                                    num_ctx=8192, think=_think(model))
            form = _obj(raw)
        except Exception as e:  # noqa: BLE001
            form = {"error": f"{type(e).__name__}: {e}"}
        out[pid] = {"form": form, "same": judge_rule(form)}
    can = {k: v for k, v in out.items() if k.startswith("canary|")}
    return {"family": family(model), "verdicts": out, "items": len(out),
            "same_pct": B._pct(sum(v["same"] for v in out.values()), len(out)),
            "canaries": len(can), "canaries_caught": sum(not v["same"] for v in can.values())}


def task_x2v(model: str, work: Path) -> dict:
    asg = B._load(ASSIGN)
    fx = {p["citekey"]: p for p in B._load(FIXTURE)["papers"]}
    contribs = _all_contribs()
    brain = B._rh_brain(model)
    out = {}
    for item in asg["queues"].get(model, {}).get("X2V", []):
        cid = item["cid"]
        c = asg["canaries"].get(cid) or contribs[cid]
        paper = fx[c["paper"]]
        pages = verify_pages(c, paper["full"])
        shown = "\n\n".join(f"[p.{p}]\n{t}" for p, t in pages.items())
        try:
            raw = brain.coordinator(f"PAPER: {paper['label']}: {paper['title']}\n\n"
                                    f"STATEMENT\n{_render(c)}\n\nPAGES\n{shown}", VERIFY_SYS,
                                    think=_think(model))
            form = _obj(raw)
        except Exception as e:  # noqa: BLE001
            form = {"error": f"{type(e).__name__}: {e}"}
        from rabbithole.facts import page_texts
        out[cid] = {"form": form, "pass": verify_rule(form, page_texts(
            "".join(f"[p.{p}]\n{t}\n" for p, t in pages.items())))}
    can = {k: v for k, v in out.items() if k.startswith("canary|")}
    return {"family": family(model), "verdicts": out, "items": len(out),
            "pass_pct": B._pct(sum(v["pass"] for v in out.values()), len(out)),
            "canaries": len(can), "canaries_caught": sum(not v["pass"] for v in can.values())}


def run_x2(model: str, tasks: list[str]) -> Path:
    """One results file per model and phase: x2e_<model> for cards, x2r_<model> for roles."""
    label = f"x2e_{B._slug(model)}" if "X2E" in tasks else f"x2r_{B._slug(model)}"
    fns = {"X2E": task_x2e, "X2J": task_x2j, "X2V": task_x2v}
    work = B.BENCH_ROOT / "work" / label
    work.mkdir(parents=True, exist_ok=True)
    fp = B.BENCH_ROOT / "results" / f"{label}.json"
    fp.parent.mkdir(parents=True, exist_ok=True)
    results = json.loads(fp.read_text()) if fp.exists() else {}
    results.update({"label": label, "model": model, "url": B._url()})
    for name in [t for t in tasks if t in fns]:
        B._say(f"{label}: {name} …")
        t0 = time.time()
        try:
            results[name] = fns[name](model, work)
        except Exception as e:  # noqa: BLE001 — a failed task is a result, not a stop
            results[name] = {"error": f"{type(e).__name__}: {e}"}
        results[name]["wall_secs"] = round(time.time() - t0, 1)
        summary = {k: v for k, v in results[name].items() if k not in ("cards", "verdicts")}
        B._say(f"{label}: {name} done → {summary}")
        B._dump(fp, results)
    return fp


# ── consensus ───────────────────────────────────────────────────────────────────

def _roles() -> tuple[dict, dict]:
    """pid -> [same, same] from its judges; cid -> [pass, pass] from its verifiers."""
    js: dict[str, list[bool]] = {}
    vs: dict[str, list[bool]] = {}
    for fp in sorted((B.BENCH_ROOT / "results").glob("x2r_*.json")):
        r = json.loads(fp.read_text(encoding="utf-8"))
        for pid, v in ((r.get("X2J") or {}).get("verdicts") or {}).items():
            js.setdefault(pid, []).append(v["same"])
        for cid, v in ((r.get("X2V") or {}).get("verdicts") or {}).items():
            vs.setdefault(cid, []).append(v["pass"])
    return js, vs


def green_pairs(asg: dict, js: dict) -> set[tuple[str, str]]:
    """Pairs every assigned judge called SAME (a missing verdict is not a yes)."""
    out = set()
    for pid, p in asg["pairs"].items():
        got = js.get(pid) or []
        if len(got) >= len(p.get("judges") or [1, 1]) and all(got):
            out.add((p["a"], p["b"]))
            out.add((p["b"], p["a"]))
    return out


def verified(c: dict, vs: dict, asg: dict) -> bool:
    got = vs.get(c["id"]) or []
    return (c.get("verbatim", False) and len(got) >= len(asg["verify"].get(c["id"]) or [1, 1])
            and all(got))


def lineup_green(lineup: tuple[str, ...], contribs: dict, same: set, vs: dict, asg: dict,
                 need: int) -> set[str]:
    """Contributions of `lineup` that pass: verified, and matched (judged SAME) to a verified
    contribution of at least `need` other lineup models on the same paper."""
    out = set()
    for cid, c in contribs.items():
        if c["model"] not in lineup or not verified(c, vs, asg):
            continue
        agree = {contribs[o]["model"] for (x, o) in same
                 if x == cid and o in contribs and contribs[o]["model"] in lineup
                 and contribs[o]["model"] != c["model"] and verified(contribs[o], vs, asg)}
        if len(agree) >= need:
            out.add(cid)
    return out


def clusters(ids: list[str], same: set) -> dict[str, int]:
    parent = {i: i for i in ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for a, b in same:
        if a in parent and b in parent:
            parent[find(a)] = find(b)
    roots = {r: n for n, r in enumerate(sorted({find(i) for i in ids}))}
    return {i: roots[find(i)] for i in ids}


# ── the page ────────────────────────────────────────────────────────────────────

def judge_x2(out: Path | None = None, pairs_per_paper: int = 5) -> Path:
    """EVAL_cards.html: paper by paper, every statement blind in page order (Part 1), then a
    sample of judged pairs, verdicts hidden (Part 2). PROVISIONAL Part 2: task 1513 settles it
    from the real judge outputs. The key stays in the bench folder."""
    fx = B._load(FIXTURE)
    contribs = _all_contribs()
    asg = B._load(ASSIGN)
    js, _ = _roles()
    key: dict = {"statements": {}, "pairs": {}}
    papers = []
    for p in fx["papers"]:
        ck = p["citekey"]
        mine = [c for c in contribs.values() if c["paper"] == ck]
        rng = random.Random(f"x2-{ck}")
        rng.shuffle(mine)
        mine.sort(key=lambda c: min([q.get("found") or q.get("page") or 999
                                     for q in c["quotes"]] or [999]))
        sts = []
        for n, c in enumerate(mine, 1):
            sid = f"s{n}"
            key["statements"].setdefault(ck, {})[sid] = c["id"]
            sts.append({"sid": sid, "claim": c["claim"], "domain": c["domain"],
                        "method": c["method"], "conditions": c["conditions"],
                        "quotes": [{"text": q["text"], "page": q.get("found") or q.get("page")}
                                   for q in c["quotes"]]})
        judged = [(pid, pr) for pid, pr in asg["pairs"].items()
                  if pr["paper"] == ck and js.get(pid)]
        merged = [x for x in judged if all(js[x[0]])]
        split = sorted((x for x in judged if not all(js[x[0]])), key=lambda x: -x[1]["cos"])
        rng.shuffle(merged)
        take = merged[:3] + split[:max(2, pairs_per_paper - len(merged[:3]))]
        take = take[:pairs_per_paper]
        rng.shuffle(take)
        prs = []
        for n, (pid, pr) in enumerate(take, 1):
            bid = f"p{n}"
            key["pairs"].setdefault(ck, {})[bid] = pid
            a, b = contribs[pr["a"]], contribs[pr["b"]]
            prs.append({"pid": bid, "a": {k: a[k] for k in ("claim", "domain", "conditions")},
                        "b": {k: b[k] for k in ("claim", "domain", "conditions")}})
        papers.append({"ck": ck, "label": p["label"], "title": p["title"],
                       "authors": p.get("authors") or [], "abstract": p["abstract"],
                       "pdf": p.get("pdf") or "", "zotero": p.get("zotero") or "",
                       "statements": sts, "pairs": prs})
    B._dump(B.BENCH_ROOT / KEY, key)
    out = Path(out or B.HAARPI_DIR / "EVAL_cards.html")
    blob = json.dumps({"papers": papers}, ensure_ascii=False).replace("</", "<\\/")
    out.write_text(_PAGE.replace("__DATA__", blob), encoding="utf-8")
    return out


# ── unblinding ──────────────────────────────────────────────────────────────────

def unblind_x2(judged: Path) -> dict:
    """Score every model, every judge and verifier, and every 2- and 3-model lineup."""
    key = B._load_bench(KEY)
    marks = json.loads(Path(judged).read_text(encoding="utf-8"))
    asg = B._load(ASSIGN)
    contribs = _all_contribs()
    js, vs = _roles()
    cards = _cards()
    label = {}                                       # cid -> 1..4
    for ck, sts in (marks.get("statements") or {}).items():
        for sid, q in sts.items():
            cid = (key["statements"].get(ck) or {}).get(sid)
            if cid and str(q) in "1234":
                label[cid] = int(q)
    faithful = {c for c, q in label.items() if q in (1, 2)}
    main = {c for c, q in label.items() if q in (1, 3)}

    per_model = {}
    for m in asg["models"]:
        lab = [c for c in label if contribs.get(c, {}).get("model") == m]
        stated = [card.get("count") for card in cards[m]["cards"].values()
                  if card.get("count") is not None]
        per_model[m] = {"labelled": len(lab),
                        "faithful_pct": B._pct(sum(c in faithful for c in lab), len(lab)),
                        "main_pct": B._pct(sum(c in main for c in lab), len(lab)),
                        "keep_pct": B._pct(sum(label[c] == 1 for c in lab), len(lab)),
                        "verbatim_pct": cards[m].get("verbatim_pct"),
                        "stated_per_paper": round(sum(stated) / len(stated), 2) if stated else None,
                        "listed_per_paper": cards[m].get("per_paper"),
                        "near_dups": asg["near_dups"].get(m),
                        "secs_per_paper": cards[m].get("secs_per_paper")}

    # judges: against the person's pair answers, and against the canaries
    human_pairs = {}
    for ck, prs in (marks.get("pairs") or {}).items():
        for bid, ans in prs.items():
            pid = (key["pairs"].get(ck) or {}).get(bid)
            if pid:
                human_pairs[pid] = ans
    per_judge: dict[str, dict] = {}
    per_verifier: dict[str, dict] = {}
    for fp in sorted((B.BENCH_ROOT / "results").glob("x2r_*.json")):
        r = json.loads(fp.read_text(encoding="utf-8"))
        m = r["model"]
        jv = (r.get("X2J") or {}).get("verdicts") or {}
        fm = fs = n = 0
        for pid, ans in human_pairs.items():
            if pid in jv:
                n += 1
                fm += jv[pid]["same"] and ans != "same"
                fs += (not jv[pid]["same"]) and ans == "same"
        per_judge[m] = {"vs_person": n, "false_merges": fm, "false_splits": fs,
                        "canaries": (r.get("X2J") or {}).get("canaries"),
                        "canaries_caught": (r.get("X2J") or {}).get("canaries_caught")}
        vv = (r.get("X2V") or {}).get("verdicts") or {}
        fa = fr = n = 0
        for cid, q in label.items():
            if cid in vv:
                n += 1
                fa += vv[cid]["pass"] and cid not in faithful
                fr += (not vv[cid]["pass"]) and cid in faithful
        per_verifier[m] = {"vs_person": n, "false_accepts": fa, "false_rejects": fr,
                           "canaries": (r.get("X2V") or {}).get("canaries"),
                           "canaries_caught": (r.get("X2V") or {}).get("canaries_caught")}

    # lineups: precision of the all-green set, recall of the person's main contributions
    same = green_pairs(asg, js)
    cl = clusters(sorted(contribs), same)
    keep_clusters = {cl[c] for c in label if label[c] == 1 and c in cl}
    lineups = {}
    for k in (2, 3):
        for lineup in itertools.combinations(asg["models"], k):
            for rule, need in ((("unanimous", k - 1),) if k == 2 else
                               (("unanimous", 2), ("2-of-3", 1))):
                g = lineup_green(lineup, contribs, same, vs, asg, need)
                lab = [c for c in g if c in label]
                hit = {cl[c] for c in g} & keep_clusters
                lineups[f"{' + '.join(family(m) for m in lineup)} ({rule})"] = {
                    "green": len(g), "green_clusters": len({cl[c] for c in g}),
                    "labelled": len(lab),
                    "faithful_pct": B._pct(sum(c in faithful for c in lab), len(lab)),
                    "not_faithful": sum(c not in faithful for c in lab),
                    "keep_recall_pct": B._pct(len(hit), len(keep_clusters)),
                    "secs_per_paper": round(sum(cards[m].get("secs_per_paper") or 0
                                                for m in lineup), 1)}
    out = {"models": per_model, "judges": per_judge, "verifiers": per_verifier,
           "lineups": dict(sorted(lineups.items(),
                                  key=lambda kv: (kv[1]["not_faithful"],
                                                  -(kv[1]["keep_recall_pct"] or 0)))),
           "person": {"statements": len(label), "pairs": len(human_pairs),
                      "missing": {ck: t for ck, t in (marks.get("missing") or {}).items() if t}}}
    B._dump(B.BENCH_ROOT / "results" / "x2_judged.json", out)
    return out


_PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Paper cards assessment</title>
<style>:root{--bg:#fbfaf7;--fg:#1e1e1e;--mut:#666;--rule:#ddd;--card:#fff;--acc:#3b5bdb;--ok:#2b8a3e;--bad:#c92a2a}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#161616;--fg:#e8e8e8;--mut:#9a9a9a;--rule:#333;--card:#1f1f1f;--acc:#8ea2ff;--ok:#69db7c;--bad:#ff8787}}
:root[data-theme="dark"]{--bg:#161616;--fg:#e8e8e8;--mut:#9a9a9a;--rule:#333;--card:#1f1f1f;--acc:#8ea2ff;--ok:#69db7c;--bad:#ff8787}
body{background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;max-width:980px;margin:0 auto;padding:20px 16px 90px}
h1{font-size:1.4em}h2{font-size:1.15em;margin:0 0 .2em}h3{font-size:1em;margin:1.2em 0 .3em}
.mut{color:var(--mut);font-size:.9em}a{color:var(--acc)}
.paper{border:1px solid var(--rule);border-radius:8px;padding:14px;margin:1.2em 0;background:var(--card)}
.paper.done{border-left:5px solid var(--ok)}
.st,.pr{border-top:1px solid var(--rule);padding:8px 0}
.meta{color:var(--mut);font-size:.88em}.q{color:var(--mut);font-size:.86em;margin:.15em 0 0 1.2em}
.grid{display:grid;grid-template-columns:auto 1fr 1fr;gap:4px;max-width:520px;margin-top:6px;font-size:.85em;align-items:center}
.grid span{color:var(--mut);text-align:center}
button{border:1px solid var(--rule);background:var(--bg);color:var(--fg);border-radius:4px;padding:4px 8px;cursor:pointer;font:inherit}
button.on{background:var(--acc);color:#fff;border-color:var(--acc)}
.opts{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}
textarea{width:100%;box-sizing:border-box;background:var(--bg);color:var(--fg);border:1px solid var(--rule);border-radius:4px;font:inherit}
.ab{display:grid;grid-template-columns:1fr 1fr;gap:10px}@media (max-width:640px){.ab{grid-template-columns:1fr}}
.ab div{border:1px solid var(--rule);border-radius:6px;padding:6px 8px}
#bar{position:fixed;left:0;right:0;bottom:0;background:var(--card);border-top:1px solid var(--rule);padding:8px 16px;display:flex;gap:1em;align-items:center;flex-wrap:wrap}
details.help{background:var(--card);border:1px solid var(--rule);border-radius:8px;padding:8px 14px}</style></head><body>
<h1>Paper cards: one-time assessment</h1>
<details class="help" open><summary><b>Instructions</b></summary>
<p>Work paper by paper. <b>Read the paper closely first</b> (open the PDF from Zotero), then click through its
statements. Each statement is one model's account of something the paper establishes; models are hidden and the
statements are in page order. Click <b>one</b> box per statement.</p>
<p><b>Faithful</b> means all three hold: (1) it is the paper's own finding, not one it cites and not background;
(2) the quote, or the page it points to, supports it; (3) its reach is right: it claims no more than the paper shows,
and drops no condition that changes its meaning. If any fails, or it is only partly right, it is <b>not faithful</b>.
Judge whether it states the paper correctly, not whether the paper is right. <b>When torn, choose not faithful.</b></p>
<p><b>Main</b> means it is what anyone would cite the paper for, whatever the project. Settings, software versions,
single statistics and the paper's own definitions are minor, unless the paper exists to establish them. Do not judge
against FirmPathways.</p>
<p>Then <b>Part 2</b>: a few pairs of statements. Do A and B assert the same contribution?</p>
<p>Your answers save in this browser as you go. <b>Save</b> downloads <code>EVAL_cards_DCR.json</code>: put it in the
HAARPi folder.</p></details>
<div id="papers"></div>
<div id="bar"><button id="save">Save</button><span id="prog" class="mut"></span></div>
<script>
const D = __DATA__;
const KEY = "x2-cards";
let S; try { S = JSON.parse(localStorage.getItem(KEY) || "null"); } catch (e) { S = null; }
S = S || {statements: {}, pairs: {}, missing: {}};
const put = () => { try { localStorage.setItem(KEY, JSON.stringify(S)); } catch (e) {} };
// Re-render after a click without losing the reader's place on the page.
const click = f => () => { const y = window.scrollY; f(); put(); render(); window.scrollTo(0, y); };
const el = (t, a = {}, ...kids) => { const e = document.createElement(t);
  for (const [k, v] of Object.entries(a)) k === "class" ? e.className = v : e.setAttribute(k, v);
  for (const k of kids) e.append(k); return e; };
const pdf = (p, n) => p.pdf ? (p.pdf.startsWith("zotero://open-pdf") && n ? p.pdf + "?page=" + n : p.pdf) : "";
const CELLS = [["Faithful", "1", "Keep"], ["Faithful", "2", "True but minor"],
               ["Not faithful", "3", "Right idea, wrong statement"], ["Not faithful", "4", "Wrong and minor"]];
const PAIRS = ["same", "A narrower", "B narrower", "different", "contradict"];
function done(p) { const s = S.statements[p.ck] || {}, q = S.pairs[p.ck] || {};
  return p.statements.every(x => s[x.sid]) && p.pairs.every(x => q[x.pid]); }
function progress() { let n = 0, t = 0;
  for (const p of D.papers) { t += p.statements.length + p.pairs.length;
    n += Object.keys(S.statements[p.ck] || {}).length + Object.keys(S.pairs[p.ck] || {}).length; }
  const fin = D.papers.filter(done).length;
  document.getElementById("prog").textContent = `${fin} of ${D.papers.length} papers done · ${n} of ${t} answers`; }
function render() {
  const root = document.getElementById("papers"); root.innerHTML = "";
  D.papers.forEach((p, i) => {
    const box = el("section", {class: "paper" + (done(p) ? " done" : ""), id: "paper-" + i});
    box.append(el("h2", {}, `${i + 1}. ${p.label}: ${p.title}`));
    const links = el("div", {class: "meta"}, (p.authors || []).join(", ") + " · ");
    if (p.pdf) links.append(el("a", {href: p.pdf}, "open the PDF"), " · ");
    if (p.zotero) links.append(el("a", {href: p.zotero}, "show in Zotero"));
    box.append(links);
    const ab = el("details", {}, el("summary", {class: "mut"}, "abstract")); ab.append(el("p", {}, p.abstract || "(none)"));
    box.append(ab, el("h3", {}, `Part 1 · ${p.statements.length} statements`));
    p.statements.forEach(s => {
      const st = el("div", {class: "st"});
      st.append(el("div", {}, s.claim), el("div", {class: "meta"}, `${s.domain} · ${s.method} · ${s.conditions}`));
      s.quotes.forEach(q => { const d = el("div", {class: "q"}, `“${q.text}” `);
        if (q.page) { const u = pdf(p, q.page); d.append(u ? el("a", {href: u}, "p." + q.page) : "p." + q.page); }
        st.append(d); });
      const g = el("div", {class: "grid"}, el("span"), el("span", {}, "Main"), el("span", {}, "Minor / not"));
      [["Faithful", "1", "2"], ["Not faithful", "3", "4"]].forEach(([row, a, b]) => {
        g.append(el("span", {}, row));
        [a, b].forEach(v => { const lab = CELLS.find(c => c[1] === v)[2];
          const btn = el("button", {class: (S.statements[p.ck] || {})[s.sid] === v ? "on" : ""}, `${v} · ${lab}`);
          btn.onclick = click(() => { (S.statements[p.ck] = S.statements[p.ck] || {})[s.sid] = v; });
          g.append(btn); }); });
      st.append(g); box.append(st); });
    box.append(el("div", {class: "mut"}, "A main contribution no statement captures (optional, one line):"));
    const ta = el("textarea", {rows: "2"}); ta.value = S.missing[p.ck] || "";
    ta.oninput = () => { S.missing[p.ck] = ta.value; put(); }; box.append(ta);
    if (p.pairs.length) {
      box.append(el("h3", {}, `Part 2 · ${p.pairs.length} pairs: do A and B assert the same contribution?`));
      p.pairs.forEach(pr => {
        const d = el("div", {class: "pr"});
        const side = (x, t) => el("div", {}, el("b", {}, t + " "), x.claim, el("div", {class: "meta"}, `${x.domain} · ${x.conditions}`));
        d.append(el("div", {class: "ab"}, side(pr.a, "A"), side(pr.b, "B")));
        const o = el("div", {class: "opts"});
        PAIRS.forEach(v => { const btn = el("button", {class: (S.pairs[p.ck] || {})[pr.pid] === v ? "on" : ""}, v);
          btn.onclick = click(() => { (S.pairs[p.ck] = S.pairs[p.ck] || {})[pr.pid] = v; });
          o.append(btn); });
        d.append(o); box.append(d); });
    }
    root.append(box); });
  progress(); }
render();
const first = D.papers.findIndex(p => !done(p));
if (first > 0) document.getElementById("paper-" + first).scrollIntoView();
document.getElementById("save").onclick = () => {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([JSON.stringify(S, null, 1)], {type: "application/json"}));
  a.download = "EVAL_cards_DCR.json"; a.click(); };
</script></body></html>"""

"""The claims gate: the one page a person reads before a project's claims reach raconteur.

Ported from bench FP's gate (devtools/bench/fp_gate.py, task 1601) on 2026-11-13, when that run
became the rabbitHole procedure. No model. It reads the project's cards and its closed run
(`work/claims/close.json`) and writes, into the litreview's output/:

    {YYMMDD}_{project}_litreview_gate_ra.html      the gate page
    {YYMMDD}_{project}_litreview_gatemap_ra.html   the contribution map it opens with

The page saves the person's answers as `{YYMMDD}_{project}_litreview_gate_ra_DCR.json`: the
chain ends in their initials, so `haarpi next` reads it as finished markup, and only `next`
releases (`release`, below). Accepting is the person's: nothing is released without a saved
"pass".

What the page holds, in order: the map (topics, the papers under each, how much of each was
confirmed); what the planted canaries say slipped past both verifiers; a sample of confirmed
claims to judge, which measures how often a confirmed claim is still wrong; and, per topic,
what a reader of the field would expect and not find (each line goes to gather as a search).

Placing papers under topics is a stopgap until claims are scored on their own: a paper goes
under every topic it has a central fact, or two useful ones, for in rabbitHole's old
`work/facts.json`, else its best topic; with no facts it is unplaced.
"""
from __future__ import annotations

import html
import json
import random
import re
import time
from collections import Counter, defaultdict
from pathlib import Path

from . import ground as G
from . import mindmap as MM

SAMPLE, PER_PAPER = 25, 2
WRONG_FOR_NO_CONFIDENCE = 2         # the page suggests "no confidence" from this many wrong
CENTRAL, USEFUL = 1, 2              # a paper serves a topic with 1 central or 2 useful facts
OUTCOMES = ("pass", "no_confidence", "hold")
RELEASE_FORMAT = "claims-release/1"
DIRECTION = re.compile(r"\b(increas|decreas|higher|lower|more|less|fewer|greater|reduc|improv|rais|boost|"
                       r"hinder|positive|negative|accelerat|slow|outperform|associated with)", re.I)
esc = lambda s: html.escape(str(s or ""), quote=True)        # noqa: E731


# ── names ───────────────────────────────────────────────────────────────────────

def page_name(short: str, date: str) -> str:
    return f"{date}_{short}_litreview_gate_ra.html"


def map_name(short: str, date: str) -> str:
    return f"{date}_{short}_litreview_gatemap_ra.html"


def answers_name(short: str, date: str, initials: str) -> str:
    return f"{date}_{short}_litreview_gate_ra_{initials}.json"


def release_name(short: str, date: str) -> str:
    """No author token: a release (haarpi.naming)."""
    return f"{date}_{short}_litreview.claims.json"


# ── what the page is built from ─────────────────────────────────────────────────

def _project(run: G.Run):
    from . import config as rhconfig
    return rhconfig.load_project(run.project)


def verdicts(run: G.Run) -> dict[str, dict]:
    """Every claim's verdict on this project's cards (close writes them), with its paper."""
    out = {}
    for cd in run.cards():
        for cid, r in (cd.step("verdict").get("claims") or {}).items():
            out[cid] = dict(r, paper=cd.citekey)
    return out


def _paper_scores(run: G.Run, n_topics: int) -> dict[str, list[list[int]]]:
    """Per paper, its old facts' scores per topic (the stopgap), or {}."""
    fp = Path(run.paths.work) / "facts.json"
    try:
        facts = json.loads(fp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    per: dict[str, list[list[int]]] = defaultdict(list)
    for f in facts.get("facts") or []:
        per[f["citekey"]].append(list(f.get("scores") or [])[:n_topics])
    return per


def placement(per_paper: dict[str, list[list[int]]], n_topics: int) -> dict[str, list[int]]:
    out = {}
    for ck, rows in per_paper.items():
        c = [sum(r[t] >= 3 for r in rows if t < len(r)) for t in range(n_topics)]
        u = [sum(r[t] == 2 for r in rows if t < len(r)) for t in range(n_topics)]
        tot = [sum(r[t] for r in rows if t < len(r)) for t in range(n_topics)]
        ts = [t for t in range(n_topics) if c[t] >= CENTRAL or u[t] >= USEFUL]
        if not ts and n_topics and max(tot) > 0:
            ts = [max(range(n_topics), key=lambda t: tot[t])]
        out[ck] = ts
    return out


def paper_best(per_paper: dict[str, list[list[int]]], n_topics: int) -> dict[str, list[int]]:
    """A paper's best fact score per topic: the release's stopgap `scores`."""
    return {ck: [max((r[t] for r in rows if t < len(r)), default=0) for t in range(n_topics)]
            for ck, rows in per_paper.items()}


def pick_sample(confirmed: list[dict], seed: int, n: int = SAMPLE, per_paper: int = PER_PAPER) -> list[dict]:
    """`n` confirmed claims at random, twice as likely to have a direction (the kind the
    canaries show slips most), at most `per_paper` from one paper."""
    rng = random.Random(seed)
    pool = [(r, 2.0 if DIRECTION.search(r["claim"]) else 1.0) for r in sorted(confirmed, key=lambda r: r["id"])]
    pick, used = [], Counter()
    while len(pick) < n and pool:
        r = rng.choices([p for p, _ in pool], weights=[w for _, w in pool])[0]
        pool = [(p, w) for p, w in pool if p is not r]
        if used[r["paper"]] < per_paper:
            used[r["paper"]] += 1
            pick.append(r)
    return pick


# ── the page ────────────────────────────────────────────────────────────────────

def _map(run: G.Run, metas: dict, by_paper: dict, n_claims: dict, placed: dict, names: list[str],
         short: str) -> str:
    themes = names + [MM.UNPLACED]
    papers, details = [], {}
    for ck, meta in metas.items():
        ts = placed.get(ck) or []
        conf = by_paper.get(ck) or []
        papers.append(MM.Paper(key=ck, label=meta.get("label") or ck,
                               theme=names[ts[0]] if ts else MM.UNPLACED,
                               phrase=conf[0]["claim"] if conf else (meta.get("title") or ""),
                               importance=len(conf)))
        details[ck] = {"facts": [{"text": r["claim"], "where": f"p.{(r.get('passages') or [{}])[0].get('page', '?')}",
                                  "link": meta.get("pdf") or ""} for r in conf],
                       "counts": f"{len(conf)} of {n_claims.get(ck, 0)} claims confirmed",
                       **({"zotero": meta["zotero"]} if meta.get("zotero") else {})}
    m = MM.Mindmap(themes=[t for t in themes if any(p.theme == t for p in papers)], papers=papers, edges=[])
    return MM.to_html(m, title=f"Claims map — {short}", corpus_size=len(papers), details=details)


def _slips_block(close: dict) -> str:
    rows = []
    for chk, per in (close.get("canaries") or {}).items():
        for kind, s in per.items():
            flag = " <b class=bad>holds the release</b>" if s["holds"] else ""
            rows.append(f"<tr><td>{esc(chk)}</td><td>{esc(kind)}</td><td>{s['slipped']} of {s['planted']}</td>"
                        f"<td>{s['low']:.0%} – {s['high']:.0%}{flag}</td></tr>")
    holds = close.get("holds") or []
    head = (f"<p class=small><b class=bad>Held:</b> {esc('; '.join(holds))}. Nothing can be released until "
            f"this kind is caught again.</p>" if holds else
            "<p class=small>No kind slips past both verifiers often enough to hold the release (the line: "
            "more than 1 in 10, even at the low end of its 95% interval).</p>")
    return (head + "<table class=small><tr><td><b>check</b></td><td><b>planted</b></td>"
            "<td><b>slipped past both</b></td><td><b>95% interval</b></td></tr>" + "".join(rows) + "</table>")


def build(project_dir=".", cfg=None, seed: int | None = None, initials: str = "DCR",
          short: str | None = None) -> Path:
    """Write the gate page and its map into the litreview's output/; return the page."""
    run = G.Run(project_dir, cfg)
    close = run.load("close")
    if not close:
        raise SystemExit("cards gate: no closed run (run `cards close` first)")
    pc = _project(run)
    short = short or pc.project_name or run.project.name
    names = [t["name"] for t in pc.topics or []]
    question = (pc.questions or [""])[0]
    date = time.strftime("%y%m%d")
    prev = run.load("gate")
    # a fresh gate draws a fresh sample: the seed after the last one, or today's date
    seed = int(seed if seed is not None else (prev["seed"] + 1 if prev.get("seed") else date))
    V = verdicts(run)
    metas = {cd.citekey: cd.meta() or {} for cd in run.cards()}
    kinds = run.kinds()
    conf = [dict(r, id=cid) for cid, r in V.items() if r["confirmed"]]
    by_paper = defaultdict(list)
    for r in sorted(conf, key=lambda r: r["id"]):
        by_paper[r["paper"]].append(r)
    n_claims = Counter(r["paper"] for r in V.values())
    per = _paper_scores(run, len(names))
    placed = placement(per, len(names))
    cited = sum(1 for r in V.values() if (r.get("failed_at") or "").startswith("step 2b"))

    out = Path(run.paths.output)
    out.mkdir(parents=True, exist_ok=True)
    mp = out / map_name(short, date)
    mp.write_text(_map(run, metas, by_paper, n_claims, placed, names, short), encoding="utf-8")

    pick = pick_sample(conf, seed)
    cards = []
    for i, r in enumerate(pick, 1):
        meta = metas[r["paper"]]
        ev = "".join(f"<blockquote><span class=pg>p.{esc(q['page'])}</span> {esc(q['text'])}</blockquote>"
                     for q in r.get("passages") or [])
        rid = esc(r["id"])
        cards.append(
            f"<div class=card><div class=head><span class=mut>{i} / {len(pick)} · {esc(meta.get('label'))}"
            f"{' · review' if kinds.get(r['paper']) == 'review' else ''}</span>"
            f"<a class=mut href=\"{esc(meta.get('pdf'))}\">PDF</a></div>"
            f"<div class=lab>Claim</div><p class=claim>{esc(r['claim'])}</p>"
            f"<div class=lab>Evidence <span class=mut>(the paper's own words)</span></div>{ev}"
            f"<div class=qq><span>Does the evidence say the claim?</span><div class=ctl data-kind=\"says\" data-id=\"{rid}\">"
            f"<button type=button data-v=\"yes\">Yes</button><button type=button data-v=\"over\">Says less</button>"
            f"<button type=button data-v=\"no\">No / reversed</button></div></div>"
            f"<div class=qq><span>Is it this paper's own?</span><div class=ctl data-kind=\"own\" data-id=\"{rid}\">"
            f"<button type=button data-v=\"yes\">Yes</button><button type=button data-v=\"no\">No, it is cited</button>"
            f"<button type=button data-v=\"unsure\">Not sure</button></div></div></div>")
    ndir = sum(bool(DIRECTION.search(r["claim"])) for r in pick)

    groups = defaultdict(list)
    for ck in metas:
        for t in placed.get(ck) or [None]:
            groups[t].append(ck)
    blocks = []
    for t in list(range(len(names))) + [None]:
        ks = groups.get(t, [])
        if not ks:
            continue
        with_c = sorted((k for k in ks if by_paper[k]), key=lambda k: (-len(by_paper[k]), metas[k].get("label") or k))
        none = sorted((k for k in ks if not by_paper[k]), key=lambda k: metas[k].get("label") or k)
        lis = "".join(
            f"<li><details><summary>{esc(metas[k].get('title') or k)} <span class=n>{len(by_paper[k])}</span>"
            f"<span class=mut> · {esc(metas[k].get('label'))}</span></summary><ul class=claims>"
            + "".join(f"<li>{esc(r['claim'])}</li>" for r in by_paper[k]) + "</ul></details></li>"
            for k in with_c)
        nn = (f"<p class='small mut'>Also here, nothing confirmed ({len(none)}): "
              + "; ".join(esc(metas[k].get("label") or k) for k in none) + "</p>") if none else ""
        title = f"T{t + 1}. {esc(names[t])}" if t is not None else "Not placed under any topic"
        sub = (f"{len(ks)} papers, {sum(len(by_paper[k]) for k in ks)} confirmed claims" if t is not None
               else f"{len(ks)} papers with nothing to place them by")
        tid = f"T{t + 1}" if t is not None else "none"
        blocks.append(f"<div class=q><h3>{title}</h3><p class='small mut'>{sub}</p><ul class=ideas>{lis}</ul>{nn}"
                      f"<label>What well-trod idea is missing here? <span class=mut>(each line goes to gather as a "
                      f"search)</span><textarea data-missing=\"{tid}\" rows=2></textarea></label></div>")

    gate = {"date": date, "seed": seed, "sample": [r["id"] for r in pick], "page": page_name(short, date),
            "answers": answers_name(short, date, initials), "short": short,
            "topics": len(names), "confirmed": len(conf), "claims": len(V)}
    run.dump("gate", gate)
    meta_js = json.dumps({"project": short, "built_from": f"rabbithole cards gate ({date})", "seed": seed,
                          "sample": gate["sample"], "file": gate["answers"]})
    page = _PAGE.format(
        short=esc(short), date=time.strftime("%Y-%m-%d"), papers=len(metas), confirmed=len(conf),
        claims=len(V), with_c=sum(1 for k in metas if by_paper[k]), mapfile=esc(mp.name),
        slips=_slips_block(close), cited=cited, n=len(pick), seed=seed, ndir=ndir, per_paper=PER_PAPER,
        wrong=WRONG_FOR_NO_CONFIDENCE, question=esc(question), ntopics=len(names),
        cards="".join(cards), blocks="".join(blocks), answers=esc(gate["answers"]),
        style=_STYLE, script=_SCRIPT.replace("__META__", meta_js).replace("__KEY__", f"gate-{short}-{date}-{seed}"))
    fp = out / gate["page"]
    fp.write_text(page, encoding="utf-8")
    G._say(f"cards gate: {fp.name} — {len(pick)} in the sample ({ndir} with a direction, seed {seed}), "
           f"{len(blocks)} topic blocks; map {mp.name}")
    return fp


# ── reading the answers, and the release ────────────────────────────────────────

def read_answers(fp: Path, gate: dict) -> dict:
    """What the person decided: their outcome, the sample claims they judged wrong, the
    missing ideas per topic, and anything left unanswered."""
    blob = json.loads(Path(fp).read_text(encoding="utf-8"))
    a = blob.get("answers") or {}
    sample = blob.get("sample") or gate.get("sample") or []
    unjudged = [i for i in sample if not a.get(f"says|{i}") or not a.get(f"own|{i}")]
    wrong = [i for i in sample if i not in unjudged
             and (a.get(f"says|{i}") != "yes" or a.get(f"own|{i}") == "no")]
    missing = {}
    for k, v in a.items():
        if k.startswith("missing|") and str(v or "").strip():
            lines = [x.strip(" -•\t") for x in str(v).splitlines() if x.strip(" -•\t")]
            if lines:
                missing[k.split("|", 1)[1]] = lines
    outcome = blob.get("gate") or a.get("gate")
    return {"outcome": outcome if outcome in OUTCOMES else None, "sample": sample, "wrong": wrong,
            "unjudged": unjudged, "missing": missing, "seed": blob.get("seed") or gate.get("seed")}


def release(project_dir, answers_fp: Path, dst: Path, cfg=None) -> dict:
    """Write the claims release (packages/haarpi/DESIGN_claims_release.md) from a passed gate:
    every confirmed claim but those the person judged wrong. Refuses while a kind holds."""
    run = G.Run(project_dir, cfg)
    close, gate = run.load("close"), run.load("gate")
    if close.get("holds"):
        raise SystemExit(f"cards release: held by {'; '.join(close['holds'])}")
    ans = read_answers(answers_fp, gate)
    if ans["outcome"] != "pass":
        raise SystemExit(f"cards release: the gate says {ans['outcome'] or 'nothing'}, not pass")
    pc = _project(run)
    topics = list(pc.topics or [])
    best = paper_best(_paper_scores(run, len(topics)), len(topics))
    metas = {cd.citekey: cd.meta() or {} for cd in run.cards()}
    origin = {c["id"]: c.get("origin") or c["id"] for cd in run.cards() for c in cd.claims()}
    wrong = set(ans["wrong"])
    claims = []
    for cid, r in sorted(verdicts(run).items()):
        if not r["confirmed"] or cid in wrong:
            continue
        ck = r["paper"]
        claims.append({"id": cid, "citekey": ck, "claim": r["claim"], "role": r.get("role"),
                       "passages": [{"page": p["page"], "pages": p.get("pages") or [p["page"], p["page"]],
                                     "text": p["text"]} for p in r.get("passages") or []],
                       "scores": best.get(ck) or [0] * len(topics), "origin": origin.get(cid, cid)})
    sources = {ck: {"label": metas[ck].get("label"), "title": metas[ck].get("title"), "pdf": metas[ck].get("pdf")}
               for ck in sorted({c["citekey"] for c in claims})}
    blob = {"format": RELEASE_FORMAT, "project": gate.get("short") or pc.project_name,
            "released": time.strftime("%Y-%m-%d"),
            "gate": {"file": Path(answers_fp).name, "outcome": "pass", "sample": len(ans["sample"]),
                     "wrong": len(wrong), "canaries": close.get("canaries")},
            "scores_from": "paper (stopgap: the paper's best old fact score per topic)",
            "questions": list(pc.questions or []), "topics": topics, "sources": sources,
            "claims": claims, "credited": []}
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    Path(dst).write_text(json.dumps(blob, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"claims": len(claims), "sources": len(sources), "left_out": len(wrong), "path": str(dst)}


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{short} gate</title>
{style}</head><body>
<h1>Gate: {short}</h1>
<p class=mut>Built {date}: {papers} papers, {confirmed} of {claims} claims confirmed, on {with_c} papers ·
about 45 minutes · what you decide here is pinned: later runs never undo it</p>
<div class=box>Nothing from this project reaches raconteur until it passes this gate. The machine checks catch
invented claims and wrong settings well. This gate does two things they can't: it finds <b>what is missing</b>, and it
<b>measures</b> how often a confirmed claim is still wrong.</div>
<div class=budget><div><b>The map</b><br>~5 min</div><div><b>1 · Sample of {n}</b><br>~25 min</div>
<div><b>2 · Missing ideas</b><br>~15 min</div></div>

<h2>The map</h2>
<p class=small>Each slice is a topic, each dot a paper; papers with more confirmed claims sit nearer the centre.
Hover for a paper's first confirmed claim, click for all of them. <a href="{mapfile}">Open the map on its own</a>.</p>
<iframe src="{mapfile}" title="Claims map"></iframe>

<h2>What slipped past the machine checks</h2>
<p class=small>Each run plants bad claims of its own (a flipped direction, a changed number, another paper's claim
or setting, a cited result passed off as the paper's own) and counts how many get past both verifiers.</p>
{slips}
<p class="small mut">Not here, because it needs no decision: {cited} claims passed every check but were the paper
reporting someone else's work. They wait for the step that fetches each named original.</p>

<h2>1 · A sample of {n} confirmed claims <span class=mut>(~25 min)</span></h2>
<p class=small>Drawn at random (seed {seed}), weighted twice toward claims with a direction (increase, lower, …), which the
planted tests show the models catch least well ({ndir} of these {n} have one), and at most {per_paper} per paper. Two
questions each. With 0 or 1 wrong the page suggests a pass (a wrong claim is left out); with {wrong} or more it suggests
<b>no confidence</b>: nothing is released, the run goes back to Cale and Claude to find what let them through, and the next
gate draws a fresh sample.</p>
{cards}

<h2>2 · What is missing? <span class=mut>(~15 min)</span></h2>
<p class=small>The project's question, <i>{question}</i>, is served by its {ntopics} topics. Under each topic are the
papers that serve it, with how many confirmed claims each has. Open one to see its claims. You don't need to judge
these: only say what a reader of this field would expect and not find. Anything written here is searched for and read
before the release: a pass with missing ideas releases after they are in.</p>
{blocks}

<footer><div class=in>
<span id=read></span>
<label style="display:inline"><input type=radio name=gate value=pass> Pass</label>
<label style="display:inline"><input type=radio name=gate value=no_confidence> No confidence</label>
<label style="display:inline"><input type=radio name=gate value=hold> Hold</label>
<button type=button id=save>Save</button>
</div><div class="in small mut">Save writes <code>{answers}</code>. Put it in this folder, then mark the gate task done.</div></footer>
{script}
</body></html>
"""

_STYLE = """<style>
:root{--bg:#fbfaf7;--fg:#1e1e1e;--mut:#6a6a6a;--rule:#ddd;--acc:#3b5bdb;--card:#fff;--warn:#b35c00;--ok:#2b8a3e;--bad:#c92a2a}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#161616;--fg:#e8e8e8;--mut:#9a9a9a;--rule:#333;--acc:#8ea2ff;--card:#1f1f1f;--warn:#f0a050;--ok:#69db7c;--bad:#ff8787}}
:root[data-theme="dark"]{--bg:#161616;--fg:#e8e8e8;--mut:#9a9a9a;--rule:#333;--acc:#8ea2ff;--card:#1f1f1f;--warn:#f0a050;--ok:#69db7c;--bad:#ff8787}
*{box-sizing:border-box}body{background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,sans-serif;max-width:900px;margin:0 auto;padding:20px 16px 130px}
h1{font-size:1.5em;margin:.2em 0}h2{font-size:1.15em;margin:1.8em 0 .3em;border-bottom:1px solid var(--rule);padding-bottom:.2em}
h3{font-size:1em;margin:.2em 0 .4em}.mut{color:var(--mut)}.small{font-size:.9em}b.bad{color:var(--bad)}
.box{background:var(--card);border:1px solid var(--rule);border-radius:6px;padding:10px 14px;margin:.8em 0}
.budget{display:flex;gap:6px;margin:.6em 0}.budget div{flex:1;background:var(--card);border:1px solid var(--rule);border-radius:4px;padding:6px 8px;font-size:.88em}
iframe{width:100%;height:720px;border:1px solid var(--rule);border-radius:6px;background:#fff}
.q{background:var(--card);border:1px solid var(--rule);border-radius:6px;padding:10px 14px;margin:.8em 0}
ul.ideas{list-style:none;padding:0;margin:0 0 .6em}ul.ideas>li{padding:3px 0;border-bottom:1px solid var(--rule)}
summary{cursor:pointer}.n{display:inline-block;min-width:1.6em;text-align:center;font-size:.8em;border:1px solid var(--rule);border-radius:9px;margin-left:4px}
ul.claims{font-size:.88em;margin:.3em 0 .3em 1em}
label{display:block;font-size:.9em}textarea{width:100%;background:var(--bg);color:var(--fg);border:1px solid var(--rule);border-radius:4px;padding:6px;font:inherit;font-size:.92em;margin-top:3px}
table{border-collapse:collapse;width:100%}td{border-bottom:1px solid var(--rule);padding:5px 6px;vertical-align:top}
.ctl{display:inline-flex;flex-wrap:wrap;gap:4px}
button{background:var(--card);color:var(--fg);border:1px solid var(--rule);border-radius:4px;padding:3px 9px;font:inherit;font-size:.85em;cursor:pointer}
button.on{border-color:var(--acc);background:var(--acc);color:var(--bg)}
.card{background:var(--card);border:1px solid var(--rule);border-radius:6px;padding:10px 14px;margin:.8em 0}
.lab{font-size:.75em;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);margin-top:.5em}
.head{display:flex;justify-content:space-between;font-size:.85em}.claim{margin:.3em 0 .4em;font-weight:500}
blockquote{margin:.3em 0;padding:3px 10px;border-left:3px solid var(--acc);font-size:.92em}.pg{color:var(--mut);font-size:.85em;margin-right:4px}
.qq{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:6px;font-size:.92em}.qq>span{min-width:12em}
footer{position:fixed;left:0;right:0;bottom:0;background:var(--card);border-top:1px solid var(--rule);padding:10px 16px}
footer .in{max-width:900px;margin:0 auto;display:flex;flex-wrap:wrap;gap:12px;align-items:center}
#read{flex:1 1 280px;font-size:.9em}#read b.ok{color:var(--ok)}#read b.bad{color:var(--bad)}#read b.warn{color:var(--warn)}
</style>"""

_SCRIPT = """<script>
const META = __META__;
const KEY = "__KEY__";
let st = {};
try { st = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) { st = {}; }
const keep = () => { try { localStorage.setItem(KEY, JSON.stringify(st)); } catch (e) {} read(); };
document.querySelectorAll(".ctl").forEach(ctl => {
  const key = ctl.dataset.kind + "|" + ctl.dataset.id;
  ctl.querySelectorAll("button").forEach(b => {
    b.classList.toggle("on", st[key] === b.dataset.v);
    b.onclick = () => {
      st[key] = st[key] === b.dataset.v ? undefined : b.dataset.v;
      ctl.querySelectorAll("button").forEach(x => x.classList.toggle("on", st[key] === x.dataset.v));
      keep();
    };
  });
});
document.querySelectorAll("textarea[data-missing]").forEach(t => {
  const key = "missing|" + t.dataset.missing;
  t.value = st[key] || "";
  t.oninput = () => { st[key] = t.value; keep(); };
});
document.querySelectorAll("input[name=gate]").forEach(r => {
  r.checked = st.gate === r.value; r.onchange = () => { st.gate = r.value; keep(); };
});
function read() {
  const ids = META.sample, n = ids.length;
  const done = ids.filter(i => st["says|" + i] && st["own|" + i]);
  const bad = done.filter(i => st["says|" + i] !== "yes" || st["own|" + i] === "no");
  const el = document.getElementById("read");
  if (!done.length) { el.textContent = `Sample: 0 of ${n} judged`; return; }
  el.innerHTML = bad.length >= 2
    ? `Sample: ${done.length} of ${n} judged · <b class=bad>${bad.length} wrong: no confidence suggested</b>`
    : bad.length === 1
    ? `Sample: ${done.length} of ${n} judged · <b class=warn>1 wrong: pass suggested, without it</b>`
    : `Sample: ${done.length} of ${n} judged · <b class=ok>none wrong</b> · error rate below about 1 in ${Math.max(1, Math.floor(done.length / 3))}`;
}
read();
document.getElementById("save").onclick = () => {
  const out = {project: META.project, saved: new Date().toISOString(), built_from: META.built_from,
               seed: META.seed, sample: META.sample, gate: st.gate || null, answers: st};
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([JSON.stringify(out, null, 1)], {type: "application/json"}));
  a.download = META.file; a.click();
};
</script>"""

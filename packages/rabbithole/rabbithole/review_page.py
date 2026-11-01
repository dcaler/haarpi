"""The fact-base litreview's REVIEW PAGE: how a person assesses the stage without redlining.

A fact base is a coverage instrument, not prose (see `facts`), so its docx is a record rather
than something to annotate. This page asks the questions a person can actually answer:

  1. per research question and per topic: is it covered? (answered / thin / missing / off-target)
  2. known papers: the reviewer lists papers that must be there; each is matched to the corpus
  3. a fact spot-check: a fixed random sample of counted facts beside their quote and page
  4. the contribution map, embedded: a source can be flagged a false friend or mis-filed
  5. a short answer to each research question written from its facts only, judged as an answer

Every verdict is saved with the comment kind it stands for (`haarpi.comment_kinds`
LITREVIEW_FACTS: sources, topic, topic_scope, fact, drop, ingest), so a gate can turn it into
rework without classifying prose. Saving writes `{date}_{short}_review_ra_DCR.json`.

The page needs no server: one self-contained file, with the map in an iframe that talks to it
by postMessage. `rabbithole reviewpage` (re)builds it from `work/facts.json`; with the brain it
also writes the per-question answers (cached in `work/review_answers.json`).
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import sys
from pathlib import Path

from . import guards, runlog
from .facts import FactBase, _synthesis_ok, counts, load_sidecar

SPOT_N = 15            # facts in the spot-check
SPOT_RECHECK = 3       # this many bad ones -> the whole fact base is re-checked
_ANSWER_FACTS = 30     # facts handed to the brain per research question
_ANSWERS_FILE = "review_answers.json"

# verdict -> the LITREVIEW_FACTS comment kind it becomes (None: no rework)
NEEDS = {
    "topic": {"enough": None, "thin": "sources", "off_target": "topic_scope"},
    "question": {"answered": None, "partly": "sources", "no": "topic"},
    "spot": {"right": None, "distorted": "fact", "unsupported": "fact"},
    "known": {"cited": None, "unused": "fact", "absent": "ingest"},
    "map": {"false_friend": "drop", "wrong_topic": "topic_scope"},
}


# ── data ─────────────────────────────────────────────────────────────────────────

def _spot_sample(base: FactBase, links: dict[str, str], seed: str, n: int = SPOT_N) -> list[dict]:
    """A fixed random sample of the facts that count (verbatim quote, useful or central on some
    topic). Seeded by the draft's name, so a reload shows the same facts and a new draft new
    ones."""
    pool = sorted((f for fs in base.facts.values() for f in fs if counts(f) >= 2),
                  key=lambda f: f.id)
    pick = random.Random(seed).sample(pool, min(n, len(pool)))
    return [{"id": f.id, "citekey": f.citekey, "text": f.text, "quote": f.quote,
             "page": f.page, "link": links.get(f.id, ""), "score": counts(f)}
            for f in sorted(pick, key=lambda f: f.id)]


def _topic_rows(base: FactBase) -> list[dict]:
    all_facts = [f for fs in base.facts.values() for f in fs]
    rows = []
    for i, t in enumerate(base.topics):
        central = [f for f in all_facts if counts(f, i) == 3]
        useful = [f for f in all_facts if counts(f, i) == 2]
        rows.append({"n": i + 1, "name": t.name, "covers": t.covers, "serves": list(t.serves),
                     "central": len(central), "useful": len(useful),
                     "sources": len({f.citekey for f in central + useful}),
                     "themes": [{"name": th.name, "facts": len(th.fact_ids),
                                 "synthesis": th.synthesis}
                                for th in base.themes if th.item == i + 1]})
    return rows


def _corpus_index(corpus: list[dict], sources: dict) -> list[dict]:
    """What the known-paper box matches against: every corpus paper, and whether it yielded a
    fact that counts."""
    out = []
    for c in corpus:
        ck = c.get("citekey") or ""
        s = sources.get(ck) or {}
        authors = " ".join((a.get("family") or "") for a in (c.get("authors") or [])
                           if isinstance(a, dict))
        out.append({"ck": ck, "label": s.get("label") or ck, "title": re.sub(r"<[^>]+>", "", c.get("title") or ""),
                    "doi": (c.get("doi") or "").lower(), "year": c.get("year") or "",
                    "authors": authors, "on_brief": bool(s.get("on_brief")),
                    "facts": s.get("facts", 0)})
    return out


def page_data(paths, stem: str, *, litmap: str = "", answers: dict | None = None) -> dict:
    blob = json.loads((paths.work / "facts.json").read_text(encoding="utf-8"))
    base = load_sidecar(paths)
    try:
        corpus = json.loads((paths.work / "corpus.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        corpus = []
    links = {d.get("id"): d.get("link", "") for d in blob.get("facts", [])}
    sources = blob.get("sources") or {}
    labels = {ck: s.get("label") or ck for ck, s in sources.items()}
    topics = _topic_rows(base)
    questions = [{"n": q, "text": text, "answer": (answers or {}).get(str(q)),
                  "topics": [t["n"] for t in topics if q in t["serves"]]}
                 for q, text in enumerate(base.focus, 1)]
    return {"stem": stem, "generated": blob.get("generated", ""), "litmap": litmap,
            "questions": questions, "topics": topics, "coverage": blob.get("coverage", []),
            "spot": _spot_sample(base, links, stem), "spot_recheck": SPOT_RECHECK,
            "corpus": _corpus_index(corpus, sources), "labels": labels, "needs": NEEDS,
            "n_facts": len(blob.get("facts", [])), "n_sources": len(sources)}


# ── the answers (the one brain step) ─────────────────────────────────────────────

_ANSWER_SYS = ("You answer a research question from a fixed list of facts, each extracted from a "
               "named source. You use ONLY those facts and cite each claim with its source's key.")

_ANSWER_PROMPT = """Research question: {question}

Facts (each verified against its source's page):
{facts}

Answer the research question in 3-5 sentences of plain prose, as the literature stands.
- Cite every claim with the key(s) of the fact(s) it rests on, as [@key].
- Use ONLY these facts and ONLY these keys. Do not add knowledge of your own.
- Where the facts do not settle part of the question, say so in one sentence.
Return the answer only: no heading, no list."""


def _answer_facts(base: FactBase, q: int) -> list:
    idx = [i for i, t in enumerate(base.topics) if q in t.serves]
    scored = [(max((counts(f, i) for i in idx), default=0), f)
              for fs in base.facts.values() for f in fs]
    ranked = sorted((p for p in scored if p[0] >= 2), key=lambda p: (-p[0], p[1].id))
    seen: dict[str, int] = {}
    out = []
    for _, f in ranked:              # at most 3 facts per source, so one paper cannot dominate
        if seen.get(f.citekey, 0) < 3:
            seen[f.citekey] = seen.get(f.citekey, 0) + 1
            out.append(f)
        if len(out) >= _ANSWER_FACTS:
            break
    return out


def answer_questions(brain, paths) -> dict:
    """One short cited answer per research question, from that question's best facts only.
    Cached per question on the facts used, so rebuilding the page costs no brain call."""
    base = load_sidecar(paths)
    cache_fp = paths.work / _ANSWERS_FILE
    try:
        cache = json.loads(cache_fp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    out = {}
    for q, question in enumerate(base.focus, 1):
        fs = _answer_facts(base, q)
        if not fs:
            out[str(q)] = {"text": "", "note": "No fact that counts serves this question."}
            continue
        sig = hashlib.sha1((question + "|" + ",".join(f.id for f in fs)).encode()).hexdigest()
        if (hit := cache.get(str(q))) and hit.get("sig") == sig:
            out[str(q)] = hit
            continue
        allowed = {f.citekey for f in fs}
        lines = "\n".join(f"- [@{f.citekey}] {f.text}" for f in fs)
        print(f"  {runlog.stamp()}answering Q{q} from {len(fs)} facts...", flush=True)
        text, note = "", ""
        try:
            raw = brain.coordinator(_ANSWER_PROMPT.format(question=question, facts=lines),
                                    _ANSWER_SYS, think=False, num_ctx=16384)
            text = " ".join((raw or "").split())
            if not _synthesis_ok(text, allowed):
                bad = sorted(set(guards.all_citekeys(text)) - allowed)
                text, note = "", ("The drafted answer cited a source outside its facts "
                                  f"({', '.join(bad) or 'or cited none'}) and was discarded.")
        except Exception as e:  # noqa: BLE001
            note = f"The answer could not be drafted ({e})."
        out[str(q)] = {"text": text, "note": note, "sig": sig, "n_facts": len(fs)}
    cache_fp.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def cached_answers(paths) -> dict:
    try:
        return json.loads((paths.work / _ANSWERS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


# ── the page ─────────────────────────────────────────────────────────────────────

def to_html(data: dict) -> str:
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    title = f"{data['stem'].rsplit('_review_ra', 1)[0]} — litreview review"
    return (_PAGE.replace("__TITLE__", _esc(title)).replace("__DATA__", blob)
            .replace("__SAVE__", json.dumps(f"{data['stem']}_DCR.json")))


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _newest_draft(output: Path) -> Path | None:
    drafts = sorted(output.glob("[0-9]" * 6 + "_*_litreview_ra.md"))
    return drafts[-1] if drafts else None


def write(paths, *, answers: dict | None = None) -> Path | None:
    """Build the page beside the newest `_ra` draft; re-skin that draft's map so it can be
    embedded. Returns the page's path, or None when there is no fact-base draft."""
    from . import mindmap
    draft = _newest_draft(paths.output)
    if draft is None or not (paths.work / "facts.json").is_file():
        return None
    head = draft.stem.rsplit("_litreview_ra", 1)[0]          # {date}_{short}
    stem = f"{head}_review_ra"
    litmap = paths.output / f"{head}_litmap_ra.html"
    if litmap.is_file():
        mindmap.reskin(litmap)
    data = page_data(paths, stem, litmap=litmap.name if litmap.is_file() else "",
                     answers=answers if answers is not None else cached_answers(paths))
    page = paths.output / f"{stem}.html"
    page.write_text(to_html(data), encoding="utf-8")
    return page


def run(directory: str = ".", brain_override: str | None = None, *,
        answers: bool = True) -> int:
    from . import config as _config
    runlog.start()
    paths = _config.project_paths(directory)
    got = None
    if answers:
        from .brain import Brain
        cfg = _config.load_project(directory)
        brain = Brain(cfg.brain, _config.load_global(), backend_override=brain_override)
        got = answer_questions(brain, paths)
    page = write(paths, answers=got)
    if page is None:
        print(f"[reviewpage] no fact-base draft (*_litreview_ra.md + work/facts.json) in "
              f"{paths.output}", file=sys.stderr)
        return 1
    print(f"  {runlog.stamp()}review page: {page}")
    return 0


_PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>__TITLE__</title>
<style>
:root{--bg:#fafaf8;--fg:#1d1d1b;--mut:#6b6b66;--line:#ddd;--card:#fff;--note:#fff6db;--ok:#2e7d32;--warn:#b26a00;--no:#c62828;--acc:#1f5fbf;--in:#fff}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#1a1a19;--fg:#e8e8e3;--mut:#9a9a93;--line:#3a3a37;--card:#232321;--note:#3a3320;--ok:#66bb6a;--warn:#ffb74d;--no:#ef5350;--acc:#6ea8ff;--in:#2c2c2a}}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif;margin:0;padding:0 16px 40px}
main{max-width:980px;margin:auto}
header{position:sticky;top:0;background:var(--bg);padding:10px 0;border-bottom:1px solid var(--line);z-index:2;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
header h1{font-size:1.15em;margin:0;flex:1}
nav a{margin-right:10px;font-size:.9em}
a{color:var(--acc)}
button{font:inherit;font-size:.88em;border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:6px;padding:2px 8px;cursor:pointer}
button.primary{background:var(--acc);border-color:var(--acc);color:#fff;font-size:1em;padding:3px 12px}
button.on-ok{background:var(--ok);border-color:var(--ok);color:#fff}
button.on-warn{background:var(--warn);border-color:var(--warn);color:#fff}
button.on-no{background:var(--no);border-color:var(--no);color:#fff}
section{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px;margin:14px 0}
section>h2{font-size:1.05em;margin:0 0 4px}
.intro,.mut{color:var(--mut);font-size:.9em}
.answer{background:var(--note);padding:8px 12px;border-radius:6px;margin:8px 0}
.row{border-top:1px solid var(--line);padding:7px 0;display:flex;gap:10px;align-items:flex-start;flex-wrap:wrap}
.row .body{flex:1 1 360px;min-width:0}
.btns{display:flex;gap:4px;flex-wrap:wrap;flex:none}
.need{font-size:.78em;color:var(--mut);margin-left:6px}
details{font-size:.88em;margin-top:3px} details ul{margin:4px 0;padding-left:1.3em}
textarea,input[type=text]{width:100%;font:inherit;font-size:.9em;background:var(--in);color:var(--fg);border:1px solid var(--line);border-radius:4px;padding:3px 6px;margin-top:4px}
blockquote{margin:4px 0;padding:2px 10px;border-left:3px solid var(--line);color:var(--mut);font-size:.9em}
.pill{display:inline-block;font-size:.78em;border-radius:10px;padding:0 7px;border:1px solid var(--line)}
.pill.ok{color:var(--ok);border-color:var(--ok)} .pill.warn{color:var(--warn);border-color:var(--warn)} .pill.no{color:var(--no);border-color:var(--no)}
iframe{width:100%;height:640px;border:1px solid var(--line);border-radius:6px;background:#fff}
#verdict{font-size:.9em}
</style></head><body><main>
<header><h1 id="ttl"></h1><span id="verdict"></span><button id="save" class="primary">Save</button></header>
<nav><a href="#s-q">1 · Questions &amp; topics</a><a href="#s-k">2 · Known papers</a><a href="#s-f">3 · Fact spot-check</a><a href="#s-m">4 · Map</a><a href="#s-n">Notes</a></nav>
<p class="intro">This review is a coverage instrument, not prose, so it is assessed here rather than redlined.
Mark what you can; anything left unmarked is taken as fine. Each verdict shows the rework it would ask for.
<b>Save</b> writes <code id="savename"></code> — save it into this folder (<code>litReview/output</code>).
Your marks are also kept in this browser between reloads.</p>
<section id="s-q"><h2>1 · Research questions and topics</h2>
<div class="mut">Does the fact base answer each question? Read the drafted answer, then judge each topic that serves it.</div>
<div id="qs"></div></section>
<section id="s-k"><h2>2 · Papers that must be here</h2>
<div class="mut">One paper per line: a title, a DOI, or author and year. Each is matched against this review's corpus as you type.</div>
<textarea id="known" rows="5" placeholder="e.g. Kauffman 1993 The Origins of Order&#10;10.1287/mnsc.43.7.934"></textarea>
<div id="knownres"></div></section>
<section id="s-f"><h2>3 · Fact spot-check</h2>
<div class="mut" id="spotintro"></div><div id="spot"></div></section>
<section id="s-m"><h2>4 · Contribution map</h2>
<div class="mut" id="mapintro"></div><div id="mapwrap"></div><div id="mapmarks"></div></section>
<section id="s-n"><h2>Anything else</h2>
<div class="mut">Free-text notes, one ask per line. These are sorted into rework the way docx comments are.</div>
<textarea id="notes" rows="4"></textarea></section>
</main>
<script>
const D = __DATA__;
const SAVE = __SAVE__;
const KEY = "litreview_review_" + D.stem;
const el = (tag, attrs = {}, ...kids) => { const e = document.createElement(tag); for (const [k, v] of Object.entries(attrs)) { if (k.startsWith("on")) e.addEventListener(k.slice(2), v); else if (k === "class") e.className = v; else e[k] = v; } for (const c of kids) if (c != null) e.append(c); return e; };
document.getElementById("ttl").textContent = D.stem.replace(/_review_ra$/, "") + " · litreview review";
document.getElementById("savename").textContent = SAVE;
let S;
try { S = JSON.parse(localStorage.getItem(KEY) || "null"); } catch (e) { S = null; }
S = Object.assign({q: {}, t: {}, spot: {}, map: {}, known: "", notes: ""}, S || {});
const persist = () => { try { localStorage.setItem(KEY, JSON.stringify(S)); } catch (e) {} summary(); };
const LABEL = {enough: "enough", thin: "thin", off_target: "off-target", answered: "answers it", partly: "partly", no: "doesn't",
  right: "right", distorted: "distorted", unsupported: "unsupported", false_friend: "doesn't belong", wrong_topic: "wrong topic"};
const TONE = {enough: "on-ok", answered: "on-ok", right: "on-ok", thin: "on-warn", partly: "on-warn", distorted: "on-warn",
  off_target: "on-no", no: "on-no", unsupported: "on-no", false_friend: "on-no", wrong_topic: "on-warn"};
const NEED_TXT = {sources: "→ gather more sources", topic: "→ add a topic", topic_scope: "→ re-scope the topic",
  fact: "→ re-check the fact", drop: "→ drop the source", ingest: "→ pull the paper in"};
function picker(group, kind, store, id, onchange) {
  const cur = () => store[id] && store[id].v;
  const need = el("span", {class: "need"});
  const box = el("div", {class: "btns"});
  const draw = () => { box.replaceChildren(...Object.keys(D.needs[kind]).map(v => el("button", {class: cur() === v ? TONE[v] : "",
      onclick: () => { store[id] = Object.assign(store[id] || {}, {v: cur() === v ? null : v}); persist(); draw(); onchange && onchange(); }}, LABEL[v])), need);
    const n = cur() && D.needs[kind][cur()]; need.textContent = n ? NEED_TXT[n] : ""; };
  draw(); return box;
}
const note = (store, id, ph) => el("input", {type: "text", placeholder: ph, value: (store[id] && store[id].note) || "",
  oninput: e => { store[id] = Object.assign(store[id] || {}, {note: e.target.value}); persist(); }});
const cite = s => s.replace(/\[@([^\]\s;]+)\]/g, (_, k) => "[" + (D.labels[k] || k) + "]");

// 1 · questions and topics
const T = Object.fromEntries(D.topics.map(t => [t.n, t]));
function topicRow(t) {
  const det = el("details", {}, el("summary", {}, `${t.central} central · ${t.useful} useful facts from ${t.sources} sources · ${t.themes.length} themes`));
  if (t.covers) det.append(el("div", {class: "mut"}, "Covers: " + t.covers));
  const ul = el("ul"); t.themes.forEach(th => { const li = el("li", {}, el("b", {}, th.name), ` (${th.facts} facts)`);
    if (th.synthesis.length) { const s = el("ul"); th.synthesis.forEach(b => s.append(el("li", {}, cite(b)))); li.append(s); } ul.append(li); });
  det.append(ul);
  return el("div", {class: "row"}, el("div", {class: "body"}, el("b", {}, `T${t.n} `), t.name, det,
    note(S.t, t.n, "What is thin or off-target (optional)")), picker("t", "topic", S.t, t.n));
}
const qs = document.getElementById("qs");
D.questions.forEach(q => {
  const a = q.answer && q.answer.text ? el("div", {class: "answer"}, el("b", {}, "Drafted answer, from the facts only: "), cite(q.answer.text))
    : el("div", {class: "answer mut"}, (q.answer && q.answer.note) || "No drafted answer yet: run `rabbithole reviewpage` with the brain. Judge from the topics below.");
  qs.append(el("h3", {}, `Q${q.n}. ${q.text}`), a,
    el("div", {class: "row"}, el("div", {class: "body"}, el("b", {}, "Does the fact base answer this question?"),
      note(S.q, q.n, "If not: what subject is missing? (becomes a new topic)")), picker("q", "question", S.q, q.n)));
  q.topics.forEach(n => qs.append(topicRow(T[n])));
});
const orphan = D.topics.filter(t => !t.serves.length);
if (orphan.length) { qs.append(el("h3", {}, "Topics serving no research question")); orphan.forEach(t => qs.append(topicRow(t))); }

// 2 · known papers
const norm = s => (s || "").toLowerCase().normalize("NFKD").replace(/[^a-z0-9 ]+/g, " ").replace(/\s+/g, " ").trim();
const CORPUS = D.corpus.map(c => Object.assign({}, c, {nt: norm(c.title), na: norm(c.authors)}));
function match(line) {
  const doi = (line.match(/10\.\d{4,9}\/\S+/) || [])[0];
  if (doi) return CORPUS.find(c => c.doi && c.doi === doi.toLowerCase().replace(/[.,;]$/, ""));
  const n = norm(line), yr = (n.match(/\b(19|20)\d\d\b/) || [])[0];
  const words = n.split(" ").filter(w => w.length > 3 && !/^\d+$/.test(w));
  let best = null, bs = 0;
  for (const c of CORPUS) {
    if (yr && String(c.year) !== yr) continue;
    const hay = c.nt + " " + c.na; const hit = words.filter(w => hay.includes(w)).length;
    const sc = words.length ? hit / words.length : 0;
    if (sc > bs) { bs = sc; best = c; }
  }
  return bs >= 0.6 ? best : null;
}
const ka = document.getElementById("known");
ka.value = S.known;
function known() {
  const out = document.getElementById("knownres"); out.replaceChildren();
  S.known = ka.value; persist();
  ka.value.split("\n").map(s => s.trim()).filter(Boolean).forEach(line => {
    const c = match(line);
    const st = !c ? "absent" : c.on_brief ? "cited" : "unused";
    const pill = st === "cited" ? el("span", {class: "pill ok"}, "in the review") : st === "unused" ? el("span", {class: "pill warn"}, "in the corpus, no fact counts")
      : el("span", {class: "pill no"}, "not in this review");
    // not found: name the same author's papers that ARE here, so a wrong year or a near-miss shows
    const fam = !c && (norm(line).split(" ").find(w => w.length > 3 && CORPUS.some(x => x.na.split(" ").includes(w))));
    const near = fam ? CORPUS.filter(x => x.na.split(" ").includes(fam)).slice(0, 4) : [];
    const hint = near.length ? el("div", {class: "mut"}, "Here by the same author: " + near.map(x => `${x.label} · ${x.title}`).join("; ")) : null;
    out.append(el("div", {class: "row"}, el("div", {class: "body"}, line, c ? el("div", {class: "mut"}, `${c.label} · ${c.title}`) : hint),
      pill, el("span", {class: "need"}, D.needs.known[st] ? NEED_TXT[D.needs.known[st]] : "")));
  });
}
ka.oninput = known;

// 3 · spot-check
document.getElementById("spotintro").textContent = `${D.spot.length} facts drawn at random from the ${D.n_facts} in this review (the same ones on every reload). ` +
  `Open each page and judge the fact against its source. ${D.spot_recheck} or more bad ones send the whole fact base back for re-checking.`;
const sp = document.getElementById("spot");
D.spot.forEach((f, i) => sp.append(el("div", {class: "row"}, el("div", {class: "body"},
  el("span", {class: "mut"}, `${i + 1}. ${D.labels[f.citekey] || f.citekey} · `), f.link ? el("a", {href: f.link}, f.page ? "p." + f.page : "open") : (f.page ? "p." + f.page : ""),
  el("div", {}, f.text), f.quote ? el("blockquote", {}, "“" + f.quote + "”") : null, note(S.spot, f.id, "What is wrong (optional)")),
  picker("spot", "spot", S.spot, f.id))));

// 4 · map
const mw = document.getElementById("mapwrap");
let frame = null;
if (D.litmap) {
  document.getElementById("mapintro").textContent = "Click a dot, then mark the source in the panel: it doesn't belong (a false friend), or it sits under the wrong topic. Marks land below.";
  frame = el("iframe", {src: D.litmap, title: "Contribution map"}); mw.append(frame);
  frame.addEventListener("load", syncMap);
} else document.getElementById("mapintro").textContent = "No contribution map beside this draft.";
function syncMap() { if (frame && frame.contentWindow) frame.contentWindow.postMessage({type: "haarpi-map-state", marks: S.map}, "*"); }
window.addEventListener("message", e => { const m = e.data || {};
  if (m.type !== "haarpi-map-verdict" || !m.id) return;
  if (!m.v || (S.map[m.id] && S.map[m.id].v === m.v)) delete S.map[m.id];
  else S.map[m.id] = {v: m.v, label: m.label, topic: m.topic, note: (S.map[m.id] && S.map[m.id].note) || ""};
  persist(); drawMap(); syncMap(); });
function drawMap() {
  const out = document.getElementById("mapmarks"); out.replaceChildren();
  Object.entries(S.map).forEach(([id, m]) => out.append(el("div", {class: "row"},
    el("div", {class: "body"}, el("b", {}, m.label || id), ` — ${LABEL[m.v]}` + (m.topic ? ` (now under ${m.topic})` : ""),
      note(S.map, id, m.v === "wrong_topic" ? "Which topic it belongs under" : "Why (optional)")),
    el("span", {class: "need"}, NEED_TXT[D.needs.map[m.v]]),
    el("button", {onclick: () => { delete S.map[id]; persist(); drawMap(); syncMap(); }}, "Remove"))));
}

// notes
const nt = document.getElementById("notes"); nt.value = S.notes; nt.oninput = () => { S.notes = nt.value; persist(); };

// summary + save
function items() {
  const out = [];
  const add = (kind, id, rec, extra) => { if (!rec || !rec.v) return; out.push(Object.assign({kind, id: String(id), verdict: rec.v,
    need: D.needs[kind][rec.v], note: rec.note || ""}, extra)); };
  D.questions.forEach(q => add("question", q.n, S.q[q.n], {text: q.text}));
  D.topics.forEach(t => add("topic", t.n, S.t[t.n], {text: t.name, serves: t.serves}));
  D.spot.forEach(f => add("spot", f.id, S.spot[f.id], {citekey: f.citekey, text: f.text, page: f.page}));
  Object.entries(S.map).forEach(([id, m]) => add("map", id, m, {citekey: id, topic: m.topic || ""}));
  S.known.split("\n").map(s => s.trim()).filter(Boolean).forEach(line => { const c = match(line);
    const st = !c ? "absent" : c.on_brief ? "cited" : "unused";
    out.push({kind: "known", id: line, verdict: st, need: D.needs.known[st], citekey: c ? c.ck : "", note: ""}); });
  return out;
}
function summary() {
  const it = items(), asks = it.filter(x => x.need), bad = it.filter(x => x.kind === "spot" && x.need).length;
  const hasNotes = S.notes.trim().length > 0;
  const v = document.getElementById("verdict");
  v.textContent = asks.length || hasNotes ? `${asks.length} rework ask${asks.length === 1 ? "" : "s"}` + (hasNotes ? " + notes" : "") +
    (bad >= D.spot_recheck ? " · full fact re-check" : "") + " → hold" : "nothing asked → release";
}
document.getElementById("save").addEventListener("click", async () => {
  const it = items(), bad = it.filter(x => x.kind === "spot" && x.need).length;
  const out = {schema: 1, review: D.stem, saved: new Date().toISOString(),
    release: !it.some(x => x.need) && !S.notes.trim(),
    spot: {checked: it.filter(x => x.kind === "spot").length, bad, of: D.spot.length, recheck_all: bad >= D.spot_recheck},
    items: it, notes: S.notes.split("\n").map(s => s.trim()).filter(Boolean)};
  const text = JSON.stringify(out, null, 2);
  if (window.showSaveFilePicker) {
    try {
      const h = await showSaveFilePicker({suggestedName: SAVE, types: [{description: "JSON", accept: {"application/json": [".json"]}}]});
      const w = await h.createWritable(); await w.write(text); await w.close(); return;
    } catch (e) { if (e && e.name === "AbortError") return; }
  }
  const a = el("a", {href: URL.createObjectURL(new Blob([text], {type: "application/json"})), download: SAVE});
  document.body.append(a); a.click(); a.remove();
});
known(); drawMap(); summary();
</script></body></html>"""

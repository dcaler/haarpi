"""The fact base's research questions and topics: drafted, reviewed by a person, then adopted.

A fact base scores every fact against approved TOPICS, each mapped to the research QUESTIONS it
serves (see `facts`). Neither should be the search `focus`: its items are lenses, keywords and
appended asks — FirmPathways' seven focus items were lenses on one question. So a review gets
both from its brief, and a person settles them before the first `report`:

  topics           draft 1-3 questions (or keep the config's) and 6-12 topics from the brief,
                   topic line, focus and this cycle's asks; write the review page
                   `{date}_{short}_topics_ra.html` beside the drafts
  (the reviewer)   accept or reject each question and topic, edit, add; Save writes
                   `{date}_{short}_topics_ra_DCR.json` into the same folder
  topics --apply   read the newest saved review (or, with none, the draft as drafted) and write
                   a new numbered config: report_format facts + questions + topics

The page is self-contained and needs no server. It is the same page the 2026-10 migration of
fifteen narrative reviews used, which is why it takes a list of projects.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from haarpi import naming

from . import config, runlog
from .facts import _json_objects, focus_items

DRAFT = "topics_draft.json"            # in work/: what the page was written from

_SYS = """You set up a literature review that records each source's facts and scores every fact
against a list of TOPICS, each serving one or more RESEARCH QUESTIONS. Respond with ONLY a JSON
object, no other text:
{"questions": ["..."], "topics": [{"name": "...", "serves": [1]}]}

- questions: 1 to 3 research questions the work itself sets out to answer, each a real question
  ending in "?", taken from the brief. Search lenses, methods and keyword lists are NOT questions.
  Prefer one question when the brief has one.
- topics: 6 to 12 single subjects a paper could be about, each a short noun phrase (at most ten
  words) — a method, a mechanism, a body of evidence, a model family. One subject per topic: no
  "and"-joined pairs of unrelated things. Fold the focus lenses in as topics where each is a
  single subject. No topic restates a question.
- serves: the 1-based numbers of the questions each topic bears on; every topic serves at least
  one."""


# ── drafting ─────────────────────────────────────────────────────────────────────
def _prompt(cfg, fixed: list[str]) -> str:
    lines = [f"Brief:\n{(cfg.research_prompt or '').strip() or '(none)'}", "",
             f"Topic line: {cfg.topic or '(none)'}", "", "Focus items (search lenses):"]
    lines += [f"- {x}" for x in focus_items(cfg.focus)] or ["- (none)"]
    asks = [str(x).strip() for x in (cfg.gather_topics or []) if str(x).strip()]
    if asks:
        lines += ["", "This cycle's search asks:"] + [f"- {x[:300]}" for x in asks]
    if fixed:
        lines += ["", "The research questions are settled — return them unchanged, in order:"]
        lines += [f"{i}. {q}" for i, q in enumerate(fixed, 1)]
    return "\n".join(lines)


def parse_draft(raw: str, fixed: list[str]) -> tuple[list[str], list[dict]]:
    """Questions and topics from the model's reply; settled questions always win. Bad rows are
    dropped, never repaired: a topic with no valid question it serves serves them all."""
    obj = None
    m = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            obj = None
    if not isinstance(obj, dict):
        obj = {"questions": [], "topics": [d for d in _json_objects(raw) if isinstance(d, dict)]}
    qs = fixed or [str(q).strip() for q in (obj.get("questions") or []) if str(q).strip()][:3]
    topics, seen = [], set()
    for t in obj.get("topics") or []:
        if not isinstance(t, dict):
            continue
        name = " ".join(str(t.get("name") or "").split())
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        serves = sorted({int(x) for x in (t.get("serves") or [])
                         if str(x).strip().isdigit() and 1 <= int(x) <= len(qs)})
        topics.append({"name": name, "serves": serves or list(range(1, len(qs) + 1))})
    return qs, topics


def fallback(cfg, fixed: list[str]) -> tuple[list[str], list[dict]]:
    """No usable draft from the model: the topic line as the one question, the focus items as
    topics. Crude, and the page says so — but the reviewer has something to edit."""
    qs = fixed or ([f"{cfg.topic.rstrip('?. ')}?"] if cfg.topic else [])
    every = list(range(1, len(qs) + 1))
    return qs, [{"name": x[:120], "serves": every} for x in focus_items(cfg.focus)]


def draft(brain, cfg) -> tuple[list[str], list[dict], str]:
    """(questions, topics, note). Keeps questions already in the config (from `haarpi init`)."""
    fixed = [str(q).strip() for q in (cfg.questions or []) if str(q).strip()]
    try:
        raw = brain.coordinator(_prompt(cfg, fixed), _SYS, think=False)
        qs, topics = parse_draft(raw, fixed)
    except Exception as e:                       # noqa: BLE001 — the reviewer still gets a page
        print(f"  [warn] drafting topics failed ({e})", file=sys.stderr)
        qs, topics = [], []
    if qs and len(topics) >= 3:
        return qs, topics, ""
    qs, topics = fallback(cfg, fixed)
    return qs, topics, ("The model gave no usable draft, so these are the topic line and the "
                        "focus items as they stand. Rewrite them.")


# ── the review page ──────────────────────────────────────────────────────────────
def review_page(projects: list[dict], save_name: str, *, title: str = "Questions and topics",
                intro: str = "") -> str:
    """A self-contained page to accept/reject/edit each project's questions and topics.

    ``projects``: [{project, config, note, focus, questions: [str], topics: [{name, serves}]}].
    Save writes ``save_name`` — into a folder the reviewer picks where the browser allows it,
    else as a download."""
    blob = json.dumps(projects, ensure_ascii=False).replace("</", "<\\/")
    key = "topics_review_" + re.sub(r"\W", "_", save_name)
    return (_PAGE.replace("__TITLE__", _esc(title)).replace("__INTRO__", intro)
            .replace("__SAVE__", json.dumps(save_name)).replace("__KEY__", json.dumps(key))
            .replace("__DATA__", blob))


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ── adopting the review ──────────────────────────────────────────────────────────
def settle(entry: dict) -> tuple[list[str], list[dict], list[str]]:
    """One project's saved review -> (questions, topics, warnings). Pending counts as accepted;
    a rejected question's number is dropped from every topic and the rest renumbered."""
    warn: list[str] = []
    keep, renum = [], {}
    for i, q in enumerate(entry.get("questions") or [], 1):
        text = str(q.get("text") if isinstance(q, dict) else q or "").strip()
        if text and (not isinstance(q, dict) or q.get("status") != "rejected"):
            keep.append(text)
            renum[i] = len(keep)
    topics = []
    for t in entry.get("topics") or []:
        name = str(t.get("name") or "").strip()
        if not name or t.get("status") == "rejected":
            continue
        serves = sorted({renum[int(x)] for x in t.get("serves") or []
                         if str(x).isdigit() and int(x) in renum})
        if not serves:
            warn.append(f"'{name}' serves no question, so it is scored but carries no weight")
        topics.append({"name": name, "serves": serves})
    if not keep:
        warn.append("every question was rejected — the focus items stand in for them")
    return keep, topics, warn


def adopt(directory, questions: list[str], topics: list[dict]) -> Path:
    """Write the next numbered config: the latest one as a fact base with these questions and
    topics. Everything else — focus, corpus, sources — is carried over untouched."""
    cfg = config.load_project(directory)
    cfg.report_format = "facts"
    cfg.questions = list(questions)
    cfg.topics = [dict(t) for t in topics]
    return config.save_project_to(cfg, config.next_project_file(directory))


def latest_review(output: Path) -> Path | None:
    """The newest saved review in output/: `*_topics_*.json` whose chain ends in a person's
    initials (anything but `ra`)."""
    found = [p for p in output.glob("*_topics_*.json") if p.stem.rsplit("_", 1)[-1] != "ra"]
    return max(found, key=lambda p: p.stat().st_mtime) if found else None


def _short(cfg, paths) -> str:
    """The filename stem the project's drafts use (its short title), case and all: the haarpi
    manifest's, else an earlier draft's, else the project name."""
    try:
        from haarpi import project as _project
        root = _project.find_root(paths.output)
        if root is not None:
            return _project.load_manifest(root).short_title
    except Exception:                            # noqa: BLE001 — a standalone rabbitHole run
        pass
    for p in sorted(paths.output.glob("*_litreview*"), key=lambda p: p.stat().st_mtime, reverse=True):
        m = re.match(r"^\d{6}_(.+?)_litreview", p.name)
        if m:
            return m.group(1)
    return re.sub(r"\W", "", cfg.project_name) or "review"


def run(directory: str = ".", brain_override: str | None = None, *, apply: bool = False) -> int:
    """CLI entry: draft the page, or (``--apply``) adopt the reviewer's saved answers."""
    from .brain import Brain
    runlog.start()
    cfg = config.load_project(directory)
    paths = config.project_paths(directory)
    paths.output.mkdir(parents=True, exist_ok=True)
    paths.work.mkdir(parents=True, exist_ok=True)
    draft_file = paths.work / DRAFT

    if apply:
        saved = latest_review(paths.output)
        drafted = json.loads(draft_file.read_text()) if draft_file.is_file() else None
        if saved is not None and drafted and saved.stat().st_mtime < draft_file.stat().st_mtime:
            saved = None                                  # an older cycle's answers
        if saved is not None:
            blob = json.loads(saved.read_text(encoding="utf-8"))
            entry = (blob.get("projects") or [blob])[0]
            source = saved.name
        elif drafted:
            entry, source = drafted, f"the draft as drafted (no saved review in {paths.output.name}/)"
        else:
            print("[topics] nothing to apply — run `haarpi rabbithole topics` first.", file=sys.stderr)
            return 1
        qs, topics, warn = settle(entry)
        fp = adopt(directory, qs, topics)
        print(f"  {runlog.stamp()}[topics] adopted {source}: {len(qs)} question(s), "
              f"{len(topics)} topic(s) -> {fp.name}")
        for w in warn:
            print(f"  [note] {w}")
        if entry.get("comment"):
            print(f"  [note] the reviewer wrote: {entry['comment']}")
        return 0

    print(f"  {runlog.stamp()}[topics] drafting questions and topics "
          f"(coordinator={cfg.brain.coordinator_model})", flush=True)
    brain = Brain(cfg.brain, config.load_global(), backend_override=brain_override)
    qs, topics, note = draft(brain, cfg)
    short = _short(cfg, paths)
    stem = f"{naming.today()}_{short}_topics_ra"
    entry = {"project": cfg.project_name, "config": config.next_project_file(directory).name,
             "note": note, "focus": focus_items(cfg.focus), "questions": qs, "topics": topics}
    draft_file.write_text(json.dumps(entry, indent=2, ensure_ascii=False), encoding="utf-8")
    page = paths.output / f"{stem}.html"
    page.write_text(review_page([entry], f"{stem}_DCR.json"), encoding="utf-8")
    print(f"  {runlog.stamp()}[topics] {len(qs)} question(s), {len(topics)} topic(s) -> {page.name}")
    print(f"  Review it, Save into {paths.output}, then mark the review task done.")
    return 0


_PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>__TITLE__</title>
<style>
:root{--bg:#fafaf8;--fg:#1d1d1b;--mut:#6b6b66;--line:#ddd;--card:#fff;--note:#fff6db;--ok:#2e7d32;--no:#c62828;--acc:#1f5fbf;--in:#fff}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#1a1a19;--fg:#e8e8e3;--mut:#9a9a93;--line:#3a3a37;--card:#232321;--note:#3a3320;--ok:#66bb6a;--no:#ef5350;--acc:#6ea8ff;--in:#2c2c2a}}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif;margin:0;padding:0 16px 40px}
main{max-width:900px;margin:auto}
header{position:sticky;top:0;background:var(--bg);padding:10px 0;border-bottom:1px solid var(--line);z-index:2;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
header h1{font-size:1.2em;margin:0;flex:1}
button{font:inherit;border:1px solid var(--line);background:var(--card);color:var(--fg);border-radius:6px;padding:3px 9px;cursor:pointer}
button.primary{background:var(--acc);border-color:var(--acc);color:#fff}
button.on-ok{background:var(--ok);border-color:var(--ok);color:#fff}
button.on-no{background:var(--no);border-color:var(--no);color:#fff}
section{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px;margin:14px 0}
section h2{font-size:1.05em;margin:0} h3{font-size:.85em;color:var(--mut);margin:12px 0 2px;text-transform:uppercase;letter-spacing:.04em}
.meta{color:var(--mut);font-size:.85em;margin:2px 0 6px}
.note{background:var(--note);padding:6px 10px;border-radius:6px;font-size:.92em}
details{font-size:.85em;color:var(--mut)} details ul{margin:4px 0;padding-left:1.4em}
.row{display:flex;gap:6px;align-items:flex-start;border-top:1px solid var(--line);padding:5px 0}
.row.accepted{box-shadow:inset 4px 0 var(--ok);padding-left:8px} .row.rejected{box-shadow:inset 4px 0 var(--no);padding-left:8px}
.row.rejected input[type=text],.row.rejected textarea.q{opacity:.5;text-decoration:line-through}
.lab{color:var(--mut);width:2.2em;flex:none;padding-top:4px}
.body{flex:1;min-width:0}
.btns{display:flex;gap:4px;flex:none}
input[type=text],textarea{width:100%;font:inherit;background:var(--in);color:var(--fg);border:1px solid var(--line);border-radius:4px;padding:3px 6px}
textarea.q{resize:vertical;min-height:2.4em} textarea.c{min-height:2.2em;margin-top:8px}
.qs{margin-top:3px}.qs label{display:inline-block;margin-right:10px;font-size:.85em;white-space:nowrap}
.status{font-size:.85em;color:var(--mut)} .intro{color:var(--mut);font-size:.92em}
.tools{display:flex;gap:6px;margin-top:6px;flex-wrap:wrap}
@media (max-width:600px){.row{flex-wrap:wrap}.body{flex:1 1 calc(100% - 2.6em)}.btns{margin-left:2.6em}}
</style></head><body><main>
<header><h1>__TITLE__</h1><span class="status" id="tally"></span>
<button id="save" class="primary">Save</button></header>
<p class="intro">__INTRO__ <b>Research questions</b> are what the work sets out to answer; <b>topics</b> are
the single subjects every fact is scored against, each serving one or more questions. The focus items stay as
search scope. Accept ✓ or reject ✕ each one; one left pending is used as drafted. Edit text, tick the questions
each topic serves, add or delete. <b>Save</b> writes <code id="savename"></code> — save it into the review's
<code>litReview/output</code> folder. Edits are also kept in this browser between reloads.</p>
<div id="list"></div></main>
<script>
const ORIG = __DATA__;
const SAVE = __SAVE__;
const KEY = __KEY__;
document.getElementById("savename").textContent = SAVE;
const fresh = p => ({...p, questions: p.questions.map(q => ({text: q, status: "pending"})),
  topics: p.topics.map(t => ({name: t.name, serves: [...t.serves], status: "pending"})), comment: ""});
let S;
try { S = JSON.parse(localStorage.getItem(KEY) || "null"); } catch (e) { S = null; }
if (!S || S.length !== ORIG.length) S = ORIG.map(fresh);
const persist = () => { try { localStorage.setItem(KEY, JSON.stringify(S)); } catch (e) {} tally(); };
const el = (tag, attrs = {}, ...kids) => { const e = document.createElement(tag); for (const [k, v] of Object.entries(attrs)) { if (k.startsWith("on")) e.addEventListener(k.slice(2), v); else if (k === "class") e.className = v; else e[k] = v; } for (const c of kids) e.append(c); return e; };

function tally() {
  let a = 0, r = 0, p = 0;
  for (const x of S) for (const it of [...x.questions, ...x.topics]) it.status === "accepted" ? a++ : it.status === "rejected" ? r++ : p++;
  document.getElementById("tally").textContent = `${a} accepted · ${r} rejected · ${p} pending`;
}

function verdict(it) {
  const set = s => () => { it.status = it.status === s ? "pending" : s; persist(); render(); };
  return el("div", {class: "btns"},
    el("button", {title: "Accept", class: it.status === "accepted" ? "on-ok" : "", onclick: set("accepted")}, "✓"),
    el("button", {title: "Reject", class: it.status === "rejected" ? "on-no" : "", onclick: set("rejected")}, "✕"));
}

function card(p, i) {
  const sec = el("section");
  sec.append(el("h2", {}, p.project), el("div", {class: "meta"}, `new config: ${p.config}`));
  if (p.note) { const d = el("p", {class: "note"}); d.innerHTML = p.note; sec.append(d); }
  const fd = el("details", {}, el("summary", {}, `Focus items (kept as search scope): ${p.focus.length}`));
  const ul = el("ul"); p.focus.forEach(f => ul.append(el("li", {}, f))); fd.append(ul); sec.append(fd);

  sec.append(el("h3", {}, "Research questions"));
  p.questions.forEach((q, k) => {
    sec.append(el("div", {class: "row " + q.status},
      el("span", {class: "lab"}, `Q${k + 1}`),
      el("div", {class: "body"}, el("textarea", {class: "q", rows: 2, value: q.text, oninput: e => { q.text = e.target.value; persist(); }})),
      verdict(q),
      el("button", {title: "Delete question", onclick: () => {
        p.questions.splice(k, 1);
        p.topics.forEach(t => t.serves = t.serves.filter(x => x !== k + 1).map(x => x > k + 1 ? x - 1 : x));
        persist(); render(); }}, "Del")));
  });
  sec.append(el("div", {class: "tools"}, el("button", {onclick: () => { p.questions.push({text: "", status: "accepted"}); persist(); render(); }}, "+ Add question")));

  sec.append(el("h3", {}, "Topics · questions each serves"));
  p.topics.forEach((t, j) => {
    const qs = el("div", {class: "qs"});
    p.questions.forEach((_, k) => {
      const q = k + 1;
      qs.append(el("label", {}, el("input", {type: "checkbox", checked: t.serves.includes(q), onchange: e => {
        t.serves = e.target.checked ? [...new Set([...t.serves, q])].sort((a, b) => a - b) : t.serves.filter(x => x !== q); persist(); }}), `Q${q}`));
    });
    sec.append(el("div", {class: "row " + t.status},
      el("span", {class: "lab"}, `T${j + 1}`),
      el("div", {class: "body"}, el("input", {type: "text", value: t.name, oninput: e => { t.name = e.target.value; persist(); }}), qs),
      verdict(t),
      el("button", {title: "Delete topic", onclick: () => { p.topics.splice(j, 1); persist(); render(); }}, "Del")));
  });
  sec.append(el("div", {class: "tools"},
    el("button", {onclick: () => { p.topics.push({name: "", serves: p.questions.length === 1 ? [1] : [], status: "accepted"}); persist(); render(); }}, "+ Add topic"),
    el("button", {onclick: () => { for (const it of [...p.questions, ...p.topics]) if (it.status === "pending") it.status = "accepted"; persist(); render(); }}, "Accept the rest"),
    el("button", {onclick: () => { if (confirm(`Reset ${p.project} to the draft?`)) { S[i] = fresh(ORIG[i]); persist(); render(); } }}, "Reset")));
  sec.append(el("textarea", {class: "c", placeholder: "Note for Claude (optional)", value: p.comment, oninput: e => { p.comment = e.target.value; persist(); }}));
  return sec;
}

function render() {
  const y = scrollY; document.getElementById("list").replaceChildren(...S.map(card)); scrollTo(0, y); tally();
}

document.getElementById("save").addEventListener("click", async () => {
  const out = {saved: new Date().toISOString(), projects: S.map(p => ({
    project: p.project, config: p.config, comment: p.comment,
    questions: p.questions.filter(q => q.text.trim()).map(q => ({text: q.text.trim(), status: q.status})),
    topics: p.topics.filter(t => t.name.trim()).map(t => ({name: t.name.trim(), serves: t.serves, status: t.status}))}))};
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
render();
</script></body></html>"""

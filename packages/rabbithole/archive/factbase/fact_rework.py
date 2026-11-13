"""Rework of a fact base from the reviewer's comments — routing only, never a prose edit.

A fact is its source's: it changes only by reading the source again, never by rewording it to
satisfy a comment. So every comment on a fact base is ROUTED (the shared redline engine's
primal) by its kind, and the rework is a deterministic re-run of part of the cached `report`:

  fact / pdf     that source's cached facts and scores move aside → re-extracted
  drop           the source is quarantined in the corpus ledger (locked, by the human)
  topic          a topic is added to the review's config (and searched for) → re-scored
  topic_scope    the topic gets a written scope the scorer reads → re-scored
  edit           themes and synthesis are rewritten (they are on every run)
  sources / ingest / cite / redirect / correct   as for any review

`haarpi next` sorts the comments (with the vocabulary in `haarpi.comment_kinds`), builds the
chain, and calls `apply` here to make the changes and record a response per comment. The next
`report` opens with those responses, each marked done or not, because a regenerated document
cannot carry the reviewer's comment threads forward.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from haarpi import comment_kinds
from haarpi.redline_engine import Evidence, ParaContext, route_class_of, triage

from . import config

KINDS = comment_kinds.LITREVIEW_FACTS
RESPONSES = "comment_responses.json"
_ASIDE = "old_rework"                    # where invalidated caches go — moved, never deleted


class FactBasePolicy:
    """The fact base's side of the shared redline engine: nothing is editable in place, and
    each kind is routed with the reply the reviewer's guide promised for it."""

    author = "rabbitHole"
    route_classes = ("sources",) + tuple(n for n in KINDS.names if n != "sources")

    def editable(self, ctx: ParaContext) -> bool:
        return False

    def route_reply(self, cls: str) -> str:
        k = KINDS.kind(cls)
        return (k.does[0].upper() + k.does[1:] + ".") if k and k.does else f"Routed as '{cls}'."

    # The rest of the protocol is never reached: triage stops every comment.
    def evidence_for(self, ctx):
        return Evidence(set(), "")

    def resolve_named_source(self, citekey):
        return None


# ── what a comment is ON ────────────────────────────────────────────────────────

_KEY = re.compile(r"@([^\s;,\]\[]+)")
_TOPIC_HEAD = re.compile(r"^T(\d+)\.")


def targets(anchor: dict, sources: dict, fact_texts: dict[str, list[str]]) -> dict:
    """The sources and topic a comment sits on, read from where it is anchored.

    Topic-section facts and source headers carry their `[@key]`; a fact bullet in the
    by-publication list does not, so it is matched to its source by its own text."""
    text = anchor.get("text") or ""
    keys = [k for k in dict.fromkeys(_KEY.findall(text)) if k in sources]
    if not keys:
        flat = " ".join(text.split())
        keys = [ck for ck, ts in fact_texts.items()
                if any(" ".join(t.split())[:80] in flat for t in ts if t)]
    topic = None
    for h in reversed(anchor.get("headings") or []):
        m = _TOPIC_HEAD.match(h.strip())
        if m:
            topic = int(m.group(1))
            break
    if topic is None:
        m = _TOPIC_HEAD.match(text.strip())
        topic = int(m.group(1)) if m else None
    return {"citekeys": keys, "topic": topic}


def _load_sidecar(paths) -> tuple[dict, dict]:
    try:
        blob = json.loads((paths.work / "facts.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, {}
    texts: dict[str, list[str]] = {}
    for f in blob.get("facts", []):
        texts.setdefault(f["citekey"], []).append(f.get("text", ""))
    return blob.get("sources") or {}, texts


def _move_aside(paths, citekey: str) -> int:
    """Move one source's cached facts and scores aside, so the next report reads it again."""
    from .summarize import _located_filename
    moved = 0
    for sub in ("", "scores"):
        src = paths.work / "facts" / sub / f"{_located_filename(citekey)}.json"
        if src.exists():
            dst = paths.work / "facts" / _ASIDE / sub
            dst.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst / src.name))
            moved += 1
    return moved


# ── apply ──────────────────────────────────────────────────────────────────────

def apply(directory: str, tasks: list[dict], asks: list[dict], anchors: dict[str, dict], *,
          gather: bool, markup: str = "", dry_run: bool = False) -> dict:
    """Make the deterministic changes the sorted comments call for, and record a response.

    ``tasks`` are `haarpi next`'s decomposed tasks (``need``, ``comments`` texts, ``query``…);
    ``asks`` the open comments (``id``, ``text``) and ``anchors`` what each is on. Returns
    ``{"lines": [...], "config": name|None, "responses": [...]}``. With ``dry_run`` nothing is
    written — the lines say what would be.
    """
    paths = config.project_paths(directory)
    sources, fact_texts = _load_sidecar(paths)
    cfg = config.load_project(directory)
    topics = [dict(t) if isinstance(t, dict) else {"name": str(t)} for t in (cfg.topics or [])]
    policy = FactBasePolicy()
    ids_by_text: dict[str, list[str]] = {}
    for a in asks:
        ids_by_text.setdefault(a["text"], []).append(a["id"])

    lines: list[str] = []
    responses: list[dict] = []
    gather_topics: list[str] = []
    config_changed = False
    for t in tasks:
        need = t["need"]
        for text in t["comments"]:
            cid = (ids_by_text.get(text) or [None]).pop(0) if ids_by_text.get(text) else None
            anchor = anchors.get(cid or "", {})
            tg = targets(anchor, sources, fact_texts)
            ctx = ParaContext(heading=(anchor.get("headings") or [""])[-1],
                              text=anchor.get("text", ""), comments=[text], route_hint=need)
            verdict = triage(ctx, Evidence(set(), ""), policy)
            cls = route_class_of(verdict.disposition or "") or need
            labels = [sources.get(ck, {}).get("label", ck) for ck in tg["citekeys"]]
            detail, ok, name = "", True, ""
            if cls in ("fact", "pdf"):
                if not tg["citekeys"]:
                    ok, detail = False, ("could not tell which source this is about — put the "
                                         "comment on the fact or the source's entry")
                else:
                    detail = "re-extracting " + ", ".join(labels)
                    if not dry_run:
                        for ck in tg["citekeys"]:
                            _move_aside(paths, ck)
            elif cls == "drop":
                keys = [(ck, sources.get(ck, {}).get("zotero_key", "")) for ck in tg["citekeys"]]
                if not keys or not all(z for _, z in keys):
                    ok, detail = False, ("could not tell which source to quarantine — put the "
                                         "comment on the source's entry")
                else:
                    detail = "quarantining " + ", ".join(labels)
                    if not dry_run:
                        from . import corpus_ledger
                        for ck, zk in keys:
                            corpus_ledger.set_status(directory, zk, "quarantine", locked=True,
                                                     added_by="human")
            elif cls == "topic":
                name = (t.get("query") or "").strip()
                if not name:
                    ok, detail = False, "no topic name could be read from the comment"
                elif any(x.get("name", "").lower() == name.lower() for x in topics):
                    detail = f"'{name}' is already a topic"
                else:
                    topics.append({"name": name, "serves": t.get("serves") or []})
                    gather_topics.append(name)
                    config_changed = True
                    qs = ", ".join(f"Q{q}" for q in t.get("serves") or []) or "no question yet"
                    detail = f"adding topic T{len(topics)} '{name}' (serves {qs}) and searching for it"
            elif cls == "topic_scope":
                n = t.get("topic") or tg["topic"]
                scope = (t.get("query") or "").strip()
                if not n or not (1 <= n <= len(topics)) or not scope:
                    ok, detail = False, ("could not tell which topic, or what it should cover — "
                                         "name it (T7) and say what it covers")
                else:
                    topics[n - 1]["covers"] = scope
                    config_changed = True
                    detail = f"T{n} now covers: {scope}"
            elif cls in ("sources", "redirect") and t.get("query"):
                gather_topics.append(t["query"])
                detail = f"searching for: {t['query']}"
            reply = verdict.reply or policy.route_reply(cls)
            responses.append({"comment": text, "kind": cls, "citekeys": tg["citekeys"],
                              "topic": t.get("topic") or tg["topic"], "topic_name": name,
                              "detail": detail, "planned": ok, "reply": reply})
            lines.append(f"  {cls}: {text[:70]!r} — {detail or reply}"
                         + ("" if ok else "  [NOT ACTIONABLE]"))

    cfg_name = None
    if not dry_run and (config_changed or (gather and gather_topics)):
        cfg.topics = topics
        if gather:
            cfg.gather_topics = list(dict.fromkeys(gather_topics))
        fp = config.next_project_file(directory)
        config.save_project_to(cfg, fp)
        cfg_name = fp.name
    if not dry_run:
        (paths.work / RESPONSES).write_text(json.dumps(
            {"markup": markup, "generated": datetime.now().isoformat(timespec="seconds"),
             "responses": responses}, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"lines": lines, "config": cfg_name, "responses": responses}


# ── the next report: what became of each comment ──────────────────────────────

def response_lines(paths, base) -> list[str]:
    """The report's opening account of the last markup's comments, each checked against what
    this run actually holds — so a promise the run did not keep says so."""
    try:
        blob = json.loads((paths.work / RESPONSES).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rs = blob.get("responses") or []
    if not rs:
        return []
    since = blob.get("generated", "")
    names = [t.name.lower() for t in base.topics]
    out = ["## Your comments on the last version", "",
           f"*{len(rs)} comment(s) on {blob.get('markup') or 'the last version'}, and what this "
           f"run did about each. The document is regenerated, so the comments themselves are "
           f"not carried over.*", ""]
    for r in rs:
        status = _status(r, base, names, paths, since)
        say = r.get("detail") or r.get("reply") or ""
        out.append(f"- “{' '.join(r['comment'].split())}” — **{r['kind']}**: {say}. "
                   f"*{status}*")
    return out + [""]


def _status(r: dict, base, names: list[str], paths, since: str) -> str:
    if not r.get("planned", True):
        return "Not done: " + (r.get("detail") or "not actionable")
    kind, cks = r["kind"], r.get("citekeys") or []
    if kind in ("fact", "pdf"):
        fresh = [ck for ck in cks if base.facts.get(ck)
                 and _newer(paths, ck, since)]
        return "Done." if len(fresh) == len(cks) else "Not done: still the old extraction."
    if kind == "drop":
        return ("Done." if not any(base.facts.get(ck) for ck in cks)
                else "Not done: the source is still in the corpus.")
    if kind == "topic":
        name = (r.get("topic_name") or "").lower()
        return "Done." if name and name in names else "Not done: the topic is not in this run."
    if kind == "topic_scope":
        n = r.get("topic")
        ok = n and 1 <= n <= len(base.topics) and base.topics[n - 1].covers
        return "Done." if ok else "Not done: the topic has no scope in this run."
    return "Ran in this cycle."


def _newer(paths, citekey: str, since: str) -> bool:
    from .summarize import _located_filename
    fp = paths.work / "facts" / f"{_located_filename(citekey)}.json"
    try:
        return datetime.fromtimestamp(fp.stat().st_mtime).isoformat(timespec="seconds") >= since
    except OSError:
        return False

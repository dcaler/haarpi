"""In-place, comment-preserving revision with tracked changes.

This is what `revise` does by default. The alternative (`revise --resynth`) re-synthesises
the whole narrative from markdown and renders a fresh .docx — which discards the reviewer's
Word comments and gives them no redline to read the tool's edits against. This module instead
edits a COPY of the annotated .docx in place: it answers each comment by rewriting only the
paragraph(s) that comment is anchored to, records every rewrite as a Word tracked change
attributed to `rabbitHole`, and leaves the comments anchored and every un-flagged paragraph
byte-for-byte untouched.

The reviewer opens the result and sees, per comment, their note beside the tool's
tracked-change answer — accept/reject, re-comment, repeat.

This file is the deterministic machinery only: XML surgery, GPU-free and unit-testable.
The LLM call that turns a comment + evidence into revised paragraph text lives in
`revise` (it is the only part that needs the brain).

OOXML notes:
  * A comment is anchored by ``<w:commentRangeStart w:id=N/>`` … ``<w:commentRangeEnd
    w:id=N/>`` markers bracketing a run range, plus a ``<w:commentReference w:id=N/>``
    run; the text lives in comments.xml. python-docx preserves all of these across an
    open/save, so we only manipulate the body XML.
  * A tracked deletion wraps the old run(s) in ``<w:del>`` and turns ``<w:t>`` into
    ``<w:delText>``; a tracked insertion wraps new run(s) in ``<w:ins>``. Both carry an
    author and date, and Word renders them as an accept/rejectable redline.

The annotated bibliography is regenerated against the post-edit narrative (see
`accepted_body_text` / `replace_bibliography`), so a newly-cited source still gets a
verifiable entry. Comments anchored to a heading are NOT rewritten as prose — the caller
routes those elsewhere (a heading comment usually means "add a source" / "find more",
which is a corpus action, not a paragraph edit).

Known limitations (documented, not bugs):
  * Multiple comments on one paragraph are coarsened to bracket the whole revised
    paragraph — every comment stays valid and anchored, but loses sub-paragraph
    precision.
  * Assumes the annotated draft has no still-open tracked changes from a prior cycle
    (true for a freshly rendered _ra draft the reviewer annotated).
"""

from __future__ import annotations

import copy
import datetime
import secrets
import zipfile
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from lxml import etree

# Threaded-comment namespaces: w14 carries paraId on each comment paragraph; w15
# (commentsExtended.xml) links a reply's paraId to its parent's via paraIdParent.
_W14 = "http://schemas.microsoft.com/office/word/2010/wordml"
_W15 = "http://schemas.microsoft.com/office/word/2012/wordml"

# python-docx's nsmap does not register the reserved ``xml`` prefix, so qn("xml:space")
# would KeyError — use the literal namespaced attribute name.
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")


# ── id allocation ──────────────────────────────────────────────────────────────

class _Ids:
    """Hand out w:id values that don't collide with existing comment/change ids."""

    def __init__(self, start: int):
        self._n = start

    def next(self) -> int:
        self._n += 1
        return self._n


def _max_existing_id(doc) -> int:
    """Highest w:id already used by comments or tracked changes, body + comments part."""
    ids = [0]

    def _scan(root):
        for tag in ("w:comment", "w:commentRangeStart", "w:commentRangeEnd",
                    "w:commentReference", "w:ins", "w:del"):
            for el in root.iter(qn(tag)):
                v = el.get(qn("w:id"))
                if v and v.lstrip("-").isdigit():
                    ids.append(int(v))

    _scan(doc.element.body)
    for rel in doc.part.rels.values():
        if rel.reltype.lower().endswith("/comments"):
            _scan(rel.target_part._element)
            break
    return max(ids)


# ── element builders ───────────────────────────────────────────────────────────

def _rpr_clone(run_el):
    rpr = run_el.find(qn("w:rPr"))
    return copy.deepcopy(rpr) if rpr is not None else None


def _text_run(text: str, rpr=None):
    r = OxmlElement("w:r")
    if rpr is not None:
        r.append(copy.deepcopy(rpr))
    t = OxmlElement("w:t")
    t.set(_XML_SPACE, "preserve")
    t.text = text
    r.append(t)
    return r


def _ins(text: str, author: str, wid: int, rpr=None):
    el = OxmlElement("w:ins")
    el.set(qn("w:id"), str(wid))
    el.set(qn("w:author"), author)
    el.set(qn("w:date"), _now())
    el.append(_text_run(text, rpr))
    return el


def _del(text: str, author: str, wid: int, rpr=None):
    el = OxmlElement("w:del")
    el.set(qn("w:id"), str(wid))
    el.set(qn("w:author"), author)
    el.set(qn("w:date"), _now())
    r = OxmlElement("w:r")
    if rpr is not None:
        r.append(copy.deepcopy(rpr))
    dt = OxmlElement("w:delText")
    dt.set(_XML_SPACE, "preserve")
    dt.text = text
    r.append(dt)
    el.append(r)
    return el


# ── paragraph inspection ─────────────────────────────────────────────────────────

def _is_text_run(r) -> bool:
    """A run carrying visible text (not a comment-reference marker run)."""
    return r.find(qn("w:t")) is not None and r.find(qn("w:commentReference")) is None


def _para_text(p_el) -> str:
    return "".join(t.text or "" for r in p_el.findall(qn("w:r"))
                   if _is_text_run(r) for t in r.findall(qn("w:t")))


# ── tracked edits ─────────────────────────────────────────────────────────────

def tracked_replace(p_el, new_text: str, author: str, ids: _Ids) -> bool:
    """Replace a paragraph's text with a tracked deletion of the old + insertion of the
    new, preserving any comment-range markers and comment-reference runs so the comment
    stays anchored to the revised paragraph.

    Returns False (a no-op) when there is no text to replace or the text is unchanged.
    """
    text_runs = [r for r in p_el.findall(qn("w:r")) if _is_text_run(r)]
    old_text = "".join(t.text or "" for r in text_runs for t in r.findall(qn("w:t")))
    if not text_runs or new_text.strip() == old_text.strip():
        return False
    rpr = _rpr_clone(text_runs[0])

    # Detach the comment plumbing and old text so we can re-lay the paragraph cleanly.
    starts = p_el.findall(qn("w:commentRangeStart"))
    ends = p_el.findall(qn("w:commentRangeEnd"))
    ref_runs = [r for r in p_el.findall(qn("w:r"))
                if r.find(qn("w:commentReference")) is not None]
    for el in text_runs + starts + ends + ref_runs:
        p_el.remove(el)

    # Re-lay as: [starts] <del old> <ins new> [ends] [reference runs]. Every comment in
    # the paragraph now brackets the revised text (sub-paragraph precision is coarsened
    # to the paragraph, but every comment stays valid and anchored).
    ppr = p_el.find(qn("w:pPr"))
    insert_at = list(p_el).index(ppr) + 1 if ppr is not None else 0
    seq = (list(starts)
           + [_del(old_text, author, ids.next(), rpr),
              _ins(new_text, author, ids.next(), rpr)]
           + list(ends) + list(ref_runs))
    for offset, el in enumerate(seq):
        p_el.insert(insert_at + offset, el)
    return True


def tracked_insert_after(p_el, text: str, author: str, ids: _Ids):
    """Insert a brand-new paragraph (wholly a tracked insertion) after ``p_el``,
    cloning its paragraph properties. For structural comments that ask to split a
    paragraph or add material."""
    new_p = OxmlElement("w:p")
    ppr = p_el.find(qn("w:pPr"))
    if ppr is not None:
        new_p.append(copy.deepcopy(ppr))
    rpr = next((_rpr_clone(r) for r in p_el.findall(qn("w:r")) if _is_text_run(r)), None)
    new_p.append(_ins(text, author, ids.next(), rpr))
    p_el.addnext(new_p)
    return new_p


# ── comment reading / anchoring ────────────────────────────────────────────────

def comments_by_id(path: Path) -> dict[str, dict]:
    """Map comment id -> {author, text} from the comments part."""
    doc = Document(str(path))
    out: dict[str, dict] = {}
    for rel in doc.part.rels.values():
        if not rel.reltype.lower().endswith("/comments"):
            continue
        for c in rel.target_part._element.findall(".//" + qn("w:comment")):
            cid = c.get(qn("w:id"))
            texts = [t.text for t in c.findall(".//" + qn("w:t")) if t.text]
            out[cid] = {"author": c.get(qn("w:author"), "reviewer"),
                        "text": " ".join(texts)}
        break
    return out


def comment_anchors(path: Path) -> list[dict]:
    """Paragraphs carrying a comment anchor, with the comment ids and current text.

    Returns a list of {para, ids, text, style} in document order. ``style`` is the
    paragraph's style name, so callers can tell a comment on a heading from one on a
    body paragraph (a heading comment must not be answered by rewriting the heading).
    Paragraphs with no comment are omitted.
    """
    doc = Document(str(path))
    out = []
    for i, p in enumerate(doc.paragraphs):
        ids = [s.get(qn("w:id")) for s in p._p.findall(qn("w:commentRangeStart"))]
        if ids:
            style = p.style.name if p.style is not None else ""
            out.append({"para": i, "ids": ids, "text": p.text, "style": style})
    return out


def is_heading_style(style_name: str) -> bool:
    """True for Word heading/title styles (so we never rewrite a heading as prose)."""
    s = (style_name or "").lower()
    return s.startswith("heading") or s == "title"


# ── post-edit narrative + bibliography regeneration ──────────────────────────────
# After the body is redlined the cited set may have changed, so the annotated
# bibliography must be regenerated against the CURRENT text to stay verifiable. We
# read the "accepted" narrative (inserted + unchanged text, deletions dropped — w:t
# lives in normal and <w:ins> runs; deleted text is in <w:delText>), re-locate, and
# replace the bibliography section wholesale. The body keeps its tracked changes; the
# bibliography is rebuilt clean — 30 entries of tracked-change noise would be unreadable
# and the bibliography is a generated artifact, not something the reviewer redlines.

_BIB_HEADING = "annotated bibliography"


def _accepted_para_text(p_el) -> str:
    """Text of a paragraph as it reads with all tracked changes accepted."""
    return "".join(t.text or "" for t in p_el.iter(qn("w:t")))


def accepted_body_text(path: Path, stop_heading: str = _BIB_HEADING) -> str:
    """Reconstruct the narrative (citekeys intact) from a redlined docx.

    Returns the body up to the bibliography heading, with tracked changes accepted, so
    callers can see which [@citekey] tags the revised draft now cites.
    """
    doc = Document(str(path))
    parts: list[str] = []
    for p in doc.paragraphs:
        txt = _accepted_para_text(p._p)
        if txt.strip().lower().startswith(stop_heading):
            break
        if txt.strip():
            parts.append(txt)
    return "\n\n".join(parts)


def _parse_bibliography_md(md: str) -> tuple[str, list[tuple[str, list[str]]]]:
    """Parse bibliography markdown into (heading, [(citation, [claim_line, ...])])."""
    heading = "Annotated Bibliography"
    blocks: list[tuple[str, list[str]]] = []
    cur: tuple[str, list[str]] | None = None
    for line in md.splitlines():
        s = line.strip()
        if s.startswith("## "):
            heading = s[3:].strip()
        elif s.startswith("**") and s.endswith("**") and len(s) > 4:
            cur = (s[2:-2].strip(), [])
            blocks.append(cur)
        elif s.startswith("- ") and cur is not None:
            cur[1].append(s[2:].strip())
    return heading, blocks


def _strip_md(text: str) -> str:
    """Drop the light markdown emphasis the bibliography lines carry (*…*)."""
    return text.replace("*", "")


def replace_bibliography(path: Path, biblio_md: str) -> dict:
    """Replace the annotated-bibliography section of ``path`` with freshly built entries.

    Deletes from the bibliography heading to the end of the body and rebuilds it from
    ``biblio_md``, reusing the heading's own style so it matches the document. The body
    (and its tracked changes + comments) above the heading is untouched. Returns a summary.
    """
    doc = Document(str(path))
    bib_idx = None
    for i, p in enumerate(doc.paragraphs):
        if p.text.strip().lower().startswith(_BIB_HEADING):
            bib_idx = i
            break
    heading_style = doc.paragraphs[bib_idx].style if bib_idx is not None else None
    if bib_idx is not None:
        for p in list(doc.paragraphs[bib_idx:]):
            p._element.getparent().remove(p._element)

    heading, blocks = _parse_bibliography_md(biblio_md)
    h = doc.add_paragraph()
    if heading_style is not None:
        h.style = heading_style
    h.add_run(heading)
    for citation, claims in blocks:
        cp = doc.add_paragraph()
        cp.add_run(_strip_md(citation)).bold = True
        for cl in claims:
            doc.add_paragraph().add_run("•  " + _strip_md(cl))

    doc.save(str(path))
    return {"bib_entries": len(blocks), "had_existing_section": bib_idx is not None}


# ── reply comments (rabbitHole's account of what it did) ─────────────────────────
# A threaded reply on each reviewer comment, authored "rabbitHole", saying briefly what
# was done about it — so the docx itself is the accountability record. python-docx adds
# the comment but not the threading link, so we (1) add the reply via add_comment with a
# paraId we assign, then (2) zip-patch commentsExtended.xml with a paraIdParent link to
# the parent comment. If the doc has no commentsExtended part, the replies are still added
# as authored comments — just not visually nested.


def _patch_comments_extended(path: Path, reply_links: list[tuple[str, str]]) -> None:
    """Add <w15:commentEx paraIdParent=…> links so each reply nests under its parent.

    reply_links: (reply_paraId, parent_comment_id). The parent's canonical paraId is the
    one already referenced in commentsExtended (a comment body may span several paragraphs).
    """
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if "word/commentsExtended.xml" not in names:
            return  # no threading part — replies stay top-level (still authored, visible)
        data = {n: z.read(n) for n in names}
    croot = etree.fromstring(data["word/comments.xml"])
    cid_paras = {c.get(qn("w:id")): [p.get(f"{{{_W14}}}paraId") for p in c.findall(qn("w:p"))]
                 for c in croot.findall(qn("w:comment"))}
    ce_root = etree.fromstring(data["word/commentsExtended.xml"])
    ext_paraids = set(ce_root.xpath("//@w15:paraId", namespaces={"w15": _W15}))

    def parent_canonical(pcid: str) -> str | None:
        for pid in cid_paras.get(pcid, []):
            if pid in ext_paraids:
                return pid
        ps = cid_paras.get(pcid, [])
        return ps[0] if ps else None

    for reply_pid, parent_cid in reply_links:
        ppid = parent_canonical(parent_cid)
        if not ppid:
            continue
        ce = etree.SubElement(ce_root, f"{{{_W15}}}commentEx")
        ce.set(f"{{{_W15}}}paraId", reply_pid)
        ce.set(f"{{{_W15}}}paraIdParent", ppid)
        ce.set(f"{{{_W15}}}done", "0")
    data["word/commentsExtended.xml"] = etree.tostring(
        ce_root, xml_declaration=True, encoding="UTF-8", standalone=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for n, b in data.items():
            z.writestr(n, b)


def add_reply_comments(path: Path, replies: list[dict], author: str = "rabbitHole") -> dict:
    """Add a threaded reply (authored ``author``) to each reviewer comment.

    Each reply: {"parent_id": <reviewer comment id>, "text": <what rabbitHole did>}.
    Returns {"replies_added": n}. A reply with no resolvable anchor is skipped.
    """
    if not replies:
        return {"replies_added": 0}
    with zipfile.ZipFile(path) as z:
        used = set(etree.fromstring(z.read("word/comments.xml")).xpath(
            "//@w14:paraId", namespaces={"w14": _W14}))

    def new_paraId() -> str:
        while True:
            pid = secrets.token_hex(4).upper()
            if pid not in used:
                used.add(pid)
                return pid

    doc = Document(str(path))
    # parent comment id -> the paragraph that opens its range (where we anchor the reply)
    para_by_cid: dict[str, object] = {}
    for p in doc.paragraphs:
        for s in p._p.findall(qn("w:commentRangeStart")):
            para_by_cid.setdefault(s.get(qn("w:id")), p)

    reply_links: list[tuple[str, str]] = []
    for r in replies:
        pcid = str(r.get("parent_id"))
        text = (r.get("text") or "").strip()
        p = para_by_cid.get(pcid)
        if p is None or not text or not p.runs:
            continue  # need a parent range and a run to anchor onto
        c = doc.add_comment(p.runs, text=text, author=author, initials="rH")
        reply_pid = new_paraId()
        c.paragraphs[-1]._p.set(f"{{{_W14}}}paraId", reply_pid)
        reply_links.append((reply_pid, pcid))
    if not reply_links:
        return {"replies_added": 0}
    doc.save(str(path))
    _patch_comments_extended(path, reply_links)
    return {"replies_added": len(reply_links)}


# ── orchestration ──────────────────────────────────────────────────────────────

def apply_edits(src: Path, out: Path, edits: list[dict], author: str = "rabbitHole") -> dict:
    """Apply paragraph edits to a copy of ``src`` and write ``out``.

    Each edit: ``{"para": int, "op": "replace"|"insert_after", "text": str}``. Pure XML
    surgery — no LLM, no network. Returns a small summary dict.
    """
    doc = Document(str(src))
    ids = _Ids(_max_existing_id(doc))
    paras = doc.paragraphs  # snapshot: holds the original <w:p> elements by index
    applied = {"replace": 0, "insert_after": 0, "skipped": 0}
    # Replaces first (they don't change paragraph count), inserts after — and because we
    # index into the snapshot's element objects (not a re-read), later inserts never
    # invalidate earlier indices.
    for e in sorted(edits, key=lambda e: (e["op"] == "insert_after", e["para"])):
        p_el = paras[e["para"]]._p
        if e["op"] == "insert_after":
            tracked_insert_after(p_el, e["text"], author, ids)
            applied["insert_after"] += 1
        else:
            ok = tracked_replace(p_el, e["text"], author, ids)
            applied["replace" if ok else "skipped"] += 1
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out))
    applied["comments_preserved"] = len(comments_by_id(out))
    return applied

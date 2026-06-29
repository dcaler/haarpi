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

Known v1 limitations (documented, not bugs):
  * Multiple comments on one paragraph are coarsened to bracket the whole revised
    paragraph — every comment stays valid and anchored, but loses sub-paragraph
    precision.
  * The bibliography section is not regenerated; a revision that cites a brand-new
    source will not yet add its bibliography entry (follow-up).
  * Assumes the annotated draft has no still-open tracked changes from a prior cycle
    (true for a freshly rendered _ra draft the reviewer annotated).
"""

from __future__ import annotations

import copy
import datetime
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

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

    Returns a list of {para: int, ids: [str], text: str} in document order. Paragraphs
    with no comment are omitted.
    """
    doc = Document(str(path))
    out = []
    for i, p in enumerate(doc.paragraphs):
        ids = [s.get(qn("w:id")) for s in p._p.findall(qn("w:commentRangeStart"))]
        if ids:
            out.append({"para": i, "ids": ids, "text": p.text})
    return out


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

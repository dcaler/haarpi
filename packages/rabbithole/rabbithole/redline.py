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

A paragraph is modelled as an ordered stream of TEXT and OPAQUE atoms (equations,
hyperlinks, footnote references), not as the text inside its ``w:r/w:t`` runs. That older
model was blind to everything else in the paragraph: an equation is a SIBLING of the text
runs, so the differ saw prose with holes where every number had been, no sentence could
match, and each rewrite collapsed to a whole-paragraph replacement — with the equations
left stranded at the paragraph tail, severed from the claims they verified.

Atoms serialize to sentinels (``⟦m:1⟧``) for the differ and for the LLM, and expand back to
their original elements on write. rabbitHole never authors an atom: an equation is re-laid
as accepted content between the redlined prose around it, never inside a ``w:ins``/``w:del``.

Known limitations (documented, not bugs):
  * Multiple comments on one paragraph are coarsened to bracket the whole revised
    paragraph — every comment stays valid and anchored, but loses sub-paragraph
    precision. (`comment_spans` recovers that precision on the way IN, which is what tells
    the minimal-edit guard which sentences a comment actually bears on.)
  * Assumes the annotated draft has no still-open tracked changes from a prior cycle
    (true for a freshly rendered _ra draft the reviewer annotated).
"""

from __future__ import annotations

import copy
import datetime
import difflib
import re
import secrets
import zipfile
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from lxml import etree

from . import guards

# ── the shared engine (haarpi.redline), re-exported under the old names ──────
from haarpi import redline as _engine
from haarpi.redline import (  # noqa: F401
    _MATH, _XML_SPACE, _SENTINEL_SPLIT, _OPAQUE_RUN_CHILDREN,
    _now, _Ids, _max_existing_id, ids_for, _rpr_clone, _text_run, _ins, _del,
    _is_text_run, _is_ref_run, _is_opaque, _sentinel_kind,
    serialize_paragraph, paragraph_text, atom_text, flatten_paragraph,
    _render, _segments, _redline_chunk, _relay,
    comments_by_id, comment_spans, anchored_sentences, is_heading_style,
    _accepted_para_text,
)
from haarpi.redline import (  # noqa: F401 — author/ids are always passed explicitly here
    tracked_replace, tracked_replace_sentencewise, tracked_insert_after,
)


# Threaded-comment namespaces: w14 carries paraId on each comment paragraph; w15
# (commentsExtended.xml) links a reply's paraId to its parent's via paraIdParent.
_W14 = "http://schemas.microsoft.com/office/word/2010/wordml"
_W15 = "http://schemas.microsoft.com/office/word/2012/wordml"


def comment_anchors(path: Path) -> list[dict]:
    """Paragraphs carrying a comment anchor, with the comment ids and current text.

    Returns a list of {para, ids, text, style, anchored} in document order. ``text`` is the
    serialized paragraph (atoms as sentinels) — the exact string the reviser is asked to
    revise. ``style`` lets callers tell a comment on a heading from one on a body paragraph
    (a heading comment must not be answered by rewriting the heading). ``anchored`` is the
    union of sentence indices the paragraph's comments bear on. Paragraphs with no comment
    are omitted.
    """
    doc = Document(str(path))
    out = []
    for i, p in enumerate(doc.paragraphs):
        ids = [s.get(qn("w:id")) for s in p._p.findall(qn("w:commentRangeStart"))]
        if not ids:
            continue
        text = paragraph_text(p._p)
        anchored: set[int] = set()
        for span in comment_spans(p._p).values():
            anchored |= anchored_sentences(text, span)
        out.append({"para": i, "ids": ids, "text": text,
                    "style": p.style.name if p.style is not None else "",
                    "anchored": sorted(anchored)})
    return out


# ── post-edit narrative + bibliography regeneration ──────────────────────────────
# After the body is redlined the cited set may have changed, so the annotated
# bibliography must be regenerated against the CURRENT text to stay verifiable. We
# read the "accepted" narrative (inserted + unchanged text, deletions dropped — w:t
# lives in normal and <w:ins> runs; deleted text is in <w:delText>), re-locate, and
# replace the bibliography section wholesale. The body keeps its tracked changes; the
# bibliography is rebuilt clean — 30 entries of tracked-change noise would be unreadable
# and the bibliography is a generated artifact, not something the reviewer redlines.

_BIB_HEADING = "annotated bibliography"


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


def _parse_bibliography_md(md: str) -> tuple[str, list[tuple[str, object]]]:
    """Parse bibliography markdown into (heading, items).

    Each item is ("sub", subheading_text) for a ``### `` tier heading, or
    ("entry", (citation, [claim_line, ...])) for a source entry."""
    heading = "Annotated Bibliography"
    items: list[tuple[str, object]] = []
    cur_claims: list[str] | None = None
    for line in md.splitlines():
        s = line.strip()
        if s.startswith("### "):
            items.append(("sub", s[4:].strip()))
            cur_claims = None
        elif s.startswith("## "):
            heading = s[3:].strip()
        elif s.startswith("**") and s.endswith("**") and len(s) > 4:
            cur_claims = []
            items.append(("entry", (s[2:-2].strip(), cur_claims)))
        elif s.startswith("- ") and cur_claims is not None:
            cur_claims.append(s[2:].strip())
    return heading, items


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

    heading, items = _parse_bibliography_md(biblio_md)
    h = doc.add_paragraph()
    if heading_style is not None:
        h.style = heading_style
    h.add_run(heading)
    n_entries = 0
    for kind, payload in items:
        if kind == "sub":
            sp = doc.add_paragraph()
            sp.add_run(_strip_md(payload)).bold = True  # tier heading (cited / additional)
            continue
        citation, claims = payload
        n_entries += 1
        cp = doc.add_paragraph()
        cp.add_run(_strip_md(citation)).bold = True
        for cl in claims:
            doc.add_paragraph().add_run("•  " + _strip_md(cl))

    doc.save(str(path))
    return {"bib_entries": n_entries, "had_existing_section": bib_idx is not None}


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
            ok = tracked_replace_sentencewise(p_el, e["text"], author, ids)
            applied["replace" if ok else "skipped"] += 1
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out))
    applied["comments_preserved"] = len(comments_by_id(out))
    return applied

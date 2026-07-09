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

# Threaded-comment namespaces: w14 carries paraId on each comment paragraph; w15
# (commentsExtended.xml) links a reply's paraId to its parent's via paraIdParent.
_W14 = "http://schemas.microsoft.com/office/word/2010/wordml"
_W15 = "http://schemas.microsoft.com/office/word/2012/wordml"

# OOXML math. An equation is a sibling of the text runs, NOT inside one, so any paragraph
# model built from <w:r><w:t> alone is blind to it: the differ sees prose with holes where
# every number was, no sentence can match, and the whole paragraph is replaced. The
# equations then survive the re-lay only because nothing removed them — stranded at the
# paragraph tail, severed from the claims they verify. Hence the atom stream below.
_MATH = "http://schemas.openxmlformats.org/officeDocument/2006/math"

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


# ── the paragraph as an atom stream ──────────────────────────────────────────────
# A paragraph is an ordered stream of TEXT and OPAQUE atoms. An opaque atom — an equation,
# a hyperlink, a footnote reference — is content rabbitHole must carry through verbatim but
# must never author. Serialising it as a sentinel (``⟦m:1⟧``) gives the differ stable
# sentences and gives the LLM something it can copy but not invent. On write, each sentinel
# expands back to its original element, in place.
#
# This is what makes the sentence-level redline real: an untouched sentence containing an
# equation now matches, so it stays verbatim, and its citekeys and its numbers survive.

_SENTINEL_SPLIT = re.compile(r"(⟦[a-z]+:\d+⟧)")

_OPAQUE_RUN_CHILDREN = ("w:footnoteReference", "w:endnoteReference",
                        "w:drawing", "w:pict", "w:object")


def _is_text_run(r) -> bool:
    """A run carrying visible text (not a comment-reference marker run)."""
    return r.find(qn("w:t")) is not None and r.find(qn("w:commentReference")) is None


def _is_ref_run(r) -> bool:
    return r.tag == qn("w:r") and r.find(qn("w:commentReference")) is not None


def _is_opaque(el) -> bool:
    """Content we preserve but never author."""
    if el.tag in (f"{{{_MATH}}}oMath", f"{{{_MATH}}}oMathPara"):
        return True
    if el.tag == qn("w:hyperlink"):
        return True
    if el.tag == qn("w:r"):
        return any(el.find(qn(t)) is not None for t in _OPAQUE_RUN_CHILDREN)
    return False


def _sentinel_kind(el) -> str:
    if el.tag.startswith(f"{{{_MATH}}}"):
        return "m"
    if el.tag == qn("w:hyperlink"):
        return "h"
    return "x"


def serialize_paragraph(p_el) -> tuple[str, dict[str, object], list]:
    """Render a paragraph as (text_with_sentinels, sentinel -> element, consumed children).

    Comment plumbing and ``w:pPr`` are left alone — they are re-attached around the rebuilt
    body. Prior tracked deletions are dropped (the paragraph reads as it currently stands);
    prior insertions read as accepted text.
    """
    parts: list[str] = []
    smap: dict[str, object] = {}
    consumed: list = []
    n = 0
    for child in list(p_el):
        tag = child.tag
        if tag == qn("w:pPr") or tag in (qn("w:commentRangeStart"), qn("w:commentRangeEnd")):
            continue
        if _is_ref_run(child) or tag == qn("w:del"):
            continue
        if _is_opaque(child):
            n += 1
            key = f"⟦{_sentinel_kind(child)}:{n}⟧"
            smap[key] = child
            parts.append(key)
            consumed.append(child)
        elif tag == qn("w:ins"):
            parts.append("".join(t.text or "" for t in child.iter(qn("w:t"))))
            consumed.append(child)
        elif tag == qn("w:r") and child.findall(qn("w:t")):
            parts.append("".join(t.text or "" for t in child.findall(qn("w:t"))))
            consumed.append(child)
    return "".join(parts), smap, consumed


def paragraph_text(p_el) -> str:
    """The paragraph as the reviser and the differ see it: prose with sentinels for atoms."""
    return serialize_paragraph(p_el)[0]


def _render(text: str, smap: dict, rpr) -> list:
    """Text with sentinels -> runs, each sentinel expanded to its original element.

    An unknown sentinel (one the model invented) renders as nothing: rabbitHole never
    authors an equation. The adversary's ``dropped_sentinels`` guard rejects the rewrite
    before it reaches here, so this is a backstop, not a path.
    """
    out: list = []
    for piece in _SENTINEL_SPLIT.split(text):
        if not piece:
            continue
        if _SENTINEL_SPLIT.fullmatch(piece):
            el = smap.get(piece)
            if el is not None:
                out.append(copy.deepcopy(el))
        else:
            out.append(_text_run(piece, rpr))
    return out


def _segments(chunk: str) -> tuple[list[str], list[str]]:
    """Split on sentinels: (text segments, sentinels). ``len(texts) == len(sents) + 1``."""
    parts = _SENTINEL_SPLIT.split(chunk)
    return parts[0::2], parts[1::2]


def _redline_chunk(old_chunk: str, new_chunk: str, smap: dict,
                   author: str, ids: _Ids, rpr) -> list:
    """Redline one changed span, never touching an atom.

    An equation is re-laid as accepted content between the redlined prose around it —
    rabbitHole cannot author an equation, so it must not claim to have deleted or inserted
    one. When the sentinel sequence is unchanged (the guarded case) the prose segments
    around each atom are redlined individually, so even a rewritten sentence keeps its
    numbers exactly where they were.
    """
    o_texts, o_sents = _segments(old_chunk)
    n_texts, n_sents = _segments(new_chunk)
    body: list = []

    if o_sents != n_sents:
        # The reviser moved, dropped, or invented an atom. The guard rejects this upstream;
        # here we simply never lose one: redline the prose, then re-lay every original atom.
        o_all, n_all = "".join(o_texts), "".join(n_texts)
        if o_all:
            body.append(_del(o_all, author, ids.next(), rpr))
        if n_all:
            body.append(_ins(n_all, author, ids.next(), rpr))
        body.extend(copy.deepcopy(smap[s]) for s in o_sents if s in smap)
        return body

    for k in range(len(o_sents) + 1):
        o = o_texts[k] if k < len(o_texts) else ""
        n = n_texts[k] if k < len(n_texts) else ""
        if o and o == n:
            body.append(_text_run(o, rpr))     # unchanged prose around an atom
        else:
            if o:
                body.append(_del(o, author, ids.next(), rpr))
            if n:
                body.append(_ins(n, author, ids.next(), rpr))
        if k < len(o_sents):
            el = smap.get(o_sents[k])
            if el is not None:
                body.append(copy.deepcopy(el))  # the atom itself: accepted, in place
    return body


def _relay(p_el, body: list, consumed: list) -> None:
    """Detach what we consumed and re-lay [starts] <body> [ends] [reference runs]."""
    starts = p_el.findall(qn("w:commentRangeStart"))
    ends = p_el.findall(qn("w:commentRangeEnd"))
    ref_runs = [r for r in p_el.findall(qn("w:r")) if _is_ref_run(r)]
    for el in consumed + starts + ends + ref_runs:
        p_el.remove(el)
    ppr = p_el.find(qn("w:pPr"))
    insert_at = list(p_el).index(ppr) + 1 if ppr is not None else 0
    for offset, el in enumerate(list(starts) + body + list(ends) + list(ref_runs)):
        p_el.insert(insert_at + offset, el)


# ── tracked edits ─────────────────────────────────────────────────────────────

def tracked_replace(p_el, new_text: str, author: str, ids: _Ids) -> bool:
    """Replace a paragraph's text with one tracked deletion of the old + insertion of the
    new, preserving comment anchors and every opaque atom.

    Coarse: the whole paragraph is redlined. `tracked_replace_sentencewise` is what the
    redline path uses; this remains for callers that genuinely mean to replace wholesale.

    Returns False (a no-op) when there is no text to replace or the text is unchanged.
    """
    old_text, smap, consumed = serialize_paragraph(p_el)
    if not consumed or new_text.strip() == old_text.strip():
        return False
    rpr = next((_rpr_clone(r) for r in consumed
                if r.tag == qn("w:r") and _is_text_run(r)), None)
    _relay(p_el, _redline_chunk(old_text, new_text, smap, author, ids, rpr), consumed)
    return True


def tracked_replace_sentencewise(p_el, new_text: str, author: str, ids: _Ids) -> bool:
    """Replace a paragraph's text with SENTENCE-level tracked changes.

    Diffs old against new at sentence granularity and redlines only the sentences that
    actually changed; every unchanged sentence is re-laid as a plain (accepted) run,
    byte-for-byte, so its [@citekey] tags, its grounding, and its equations survive the
    revision untouched. Opaque atoms are never deleted or inserted — see `_redline_chunk`.

    Returns False (a no-op) when there is no text to replace or the text is unchanged.
    """
    old_text, smap, consumed = serialize_paragraph(p_el)
    if not consumed or new_text.strip() == old_text.strip():
        return False
    rpr = next((_rpr_clone(r) for r in consumed
                if r.tag == qn("w:r") and _is_text_run(r)), None)

    old_units = guards.sentence_units(old_text)
    new_units = guards.sentence_units(new_text)
    sm = difflib.SequenceMatcher(a=old_units, b=new_units, autojunk=False)
    body: list = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for u in old_units[i1:i2]:
                body.extend(_render(u, smap, rpr))  # unchanged — accepted, atoms in place
        else:
            body.extend(_redline_chunk("".join(old_units[i1:i2]),
                                       "".join(new_units[j1:j2]),
                                       smap, author, ids, rpr))
    _relay(p_el, body, consumed)
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


def comment_spans(p_el) -> dict[str, tuple[int, int]]:
    """Character offsets ``[start, end)`` of each comment's anchored range, measured over
    the same serialized text `paragraph_text` returns.

    The reviewer highlights the phrase their comment is about, so this recovers WHICH
    SENTENCES a comment actually bears on. `apply_edits` later coarsens the surviving
    anchors to the whole paragraph, but on the way in the precision is there — and it is
    what lets the minimal-edit guard know that a comment on sentence 2 does not license
    rewriting sentences 1 and 3-7.
    """
    offset = 0
    n = 0  # must track serialize_paragraph's counter: ⟦m:10⟧ is wider than ⟦m:1⟧
    opens: dict[str, int] = {}
    spans: dict[str, tuple[int, int]] = {}
    for child in list(p_el):
        tag = child.tag
        if tag == qn("w:commentRangeStart"):
            opens[child.get(qn("w:id"))] = offset
        elif tag == qn("w:commentRangeEnd"):
            cid = child.get(qn("w:id"))
            if cid in opens:
                spans[cid] = (opens.pop(cid), offset)
        elif tag == qn("w:pPr") or _is_ref_run(child) or tag == qn("w:del"):
            continue
        elif _is_opaque(child):
            n += 1
            offset += len(f"⟦{_sentinel_kind(child)}:{n}⟧")
        elif tag == qn("w:ins"):
            offset += sum(len(t.text or "") for t in child.iter(qn("w:t")))
        elif tag == qn("w:r"):
            offset += sum(len(t.text or "") for t in child.findall(qn("w:t")))
    for cid, start in opens.items():  # range never closed in this paragraph
        spans[cid] = (start, offset)
    return spans


def anchored_sentences(text: str, span: tuple[int, int]) -> set[int]:
    """Indices of the sentences a comment's character range overlaps."""
    out: set[int] = set()
    pos = 0
    for i, unit in enumerate(guards.sentence_units(text)):
        if pos < span[1] and span[0] < pos + len(unit):
            out.add(i)
        pos += len(unit)
    return out


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

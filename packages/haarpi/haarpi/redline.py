"""The shared redline engine: in-place, comment-preserving revision with tracked changes.

Deterministic OOXML machinery only — GPU-free and unit-testable. The LLM call that
turns a reviewer comment into revised paragraph text lives in each tool's revise
layer, and each tool keeps its own comment-routing (`comment_anchors` and friends)
in its redline binding; what lives here is the paragraph model and the XML surgery
rabbitHole and raconteur previously maintained as a fork pair.

A paragraph is modelled as an ordered stream of TEXT and OPAQUE atoms (equations,
footnote references, drawings), NOT as the text inside its ``w:r/w:t`` runs: an
equation is a SIBLING of the text runs, so a differ built on ``w:t`` alone sees
prose with holes where every number had been. Atoms serialize to sentinels
(``⟦m:1⟧``) for the differ and for the LLM, and expand back to their original
elements on write.

    INVARIANT: the tool never authors an equation; it only edits the prose
    around one. An atom is re-laid as ACCEPTED content between the redlined
    prose, never inside a ``w:ins``/``w:del``.

OOXML notes:
  * A comment is anchored by ``<w:commentRangeStart w:id=N/>`` …
    ``<w:commentRangeEnd w:id=N/>`` markers bracketing a run range, plus a
    ``<w:commentReference w:id=N/>`` run; the text lives in comments.xml.
    python-docx preserves all of these across an open/save.
  * A tracked deletion wraps the old run(s) in ``<w:del>`` and turns ``<w:t>``
    into ``<w:delText>``; a tracked insertion wraps new run(s) in ``<w:ins>``.
"""

from __future__ import annotations

import copy
import datetime
import difflib
import re
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from .text import sentence_units

# OOXML math. An equation is a sibling of the text runs, NOT inside one — see module docstring.
_MATH = "http://schemas.openxmlformats.org/officeDocument/2006/math"


# python-docx's nsmap does not register the reserved ``xml`` prefix, so qn("xml:space")
# would KeyError — use the literal namespaced attribute name.
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")


# ── id allocation ─────────────────────────────────────────────────────────────

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


def ids_for(doc) -> _Ids:
    return _Ids(_max_existing_id(doc))


# ── element builders ──────────────────────────────────────────────────────────

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


# ── the paragraph as an atom stream ───────────────────────────────────────────

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


def atom_text(el) -> str:
    """The visible text inside an opaque atom.

    An equation's characters live in ``m:t``, not ``w:t``, so anything reading only ``w:t``
    sees a paragraph with holes where every number was. Used to flatten a paragraph for
    callers that want plain prose rather than sentinels.

    One ordered pass over both tags: no atom carries m:t and w:t together today, but an
    atom that ever did — a hyperlink inside an equation — would reorder the prose under a
    tag-at-a-time read, and the damage would look like a model error rather than a reader bug.
    """
    return "".join(t.text or "" for t in el.iter(f"{{{_MATH}}}t", qn("w:t")))


def flatten_paragraph(p_el) -> str:
    """The paragraph as plain prose, with each atom rendered as its own text.

    Deletions are dropped and insertions read as accepted, exactly as ``serialize_paragraph``
    defines it — but atoms come back as their characters instead of ``⟦m:1⟧``, so the result
    is readable markdown rather than a sentinel stream.
    """
    text, smap, _ = serialize_paragraph(p_el)
    for key, el in smap.items():
        text = text.replace(key, atom_text(el))
    return text


def _render(text: str, smap: dict, rpr) -> list:
    """Text with sentinels -> runs, each sentinel expanded to its original element.

    An unknown sentinel (one the model invented) renders as nothing: the tool never authors
    an equation. The ``invented_sentinels`` guard rejects the rewrite before it reaches here,
    so this is a backstop, not a path.
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
    the tool cannot author an equation, so it must not claim to have deleted or inserted
    one. When the sentinel sequence is unchanged (the guarded case) the prose segments around
    each atom are redlined individually, so even a rewritten sentence keeps its numbers
    exactly where they were.
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
            body.append(_text_run(o, rpr))      # unchanged prose around an atom
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

def tracked_replace(p_el, new_text: str, author: str, ids: _Ids | None = None) -> bool:
    """Replace a paragraph's text with one tracked deletion of the old + insertion of the new,
    preserving comment anchors and every opaque atom.

    Coarse: the whole paragraph is redlined. ``tracked_replace_sentencewise`` is what the
    redline path uses; this remains for callers that genuinely mean to replace wholesale.

    Returns False (a no-op) when there is no text to replace or the text is unchanged.
    """
    ids = ids or _Ids(1000)
    old_text, smap, consumed = serialize_paragraph(p_el)
    if not consumed or new_text.strip() == old_text.strip():
        return False
    rpr = next((_rpr_clone(r) for r in consumed
                if r.tag == qn("w:r") and _is_text_run(r)), None)
    _relay(p_el, _redline_chunk(old_text, new_text, smap, author, ids, rpr), consumed)
    return True


def tracked_replace_sentencewise(p_el, new_text: str, author: str,
                                 ids: _Ids | None = None) -> bool:
    """Replace a paragraph's text with SENTENCE-level tracked changes.

    Diffs old against new at sentence granularity and redlines only the sentences that
    actually changed; every unchanged sentence is re-laid as a plain (accepted) run,
    byte-for-byte, so its [@citekey] tags, its grounding, and its equations survive the
    revision untouched. Opaque atoms are never deleted or inserted — see ``_redline_chunk``.

    Returns False (a no-op) when there is no text to replace or the text is unchanged.
    """
    ids = ids or _Ids(1000)
    old_text, smap, consumed = serialize_paragraph(p_el)
    if not consumed or new_text.strip() == old_text.strip():
        return False
    rpr = next((_rpr_clone(r) for r in consumed
                if r.tag == qn("w:r") and _is_text_run(r)), None)

    old_units = sentence_units(old_text)
    new_units = sentence_units(new_text)
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


def tracked_insert_after(p_el, text: str, author: str, ids: _Ids | None = None):
    """Insert a brand-new paragraph (wholly a tracked insertion) after ``p_el``, cloning its
    paragraph properties. For structural comments that ask to split a paragraph or add
    material."""
    ids = ids or _Ids(1000)
    new_p = OxmlElement("w:p")
    ppr = p_el.find(qn("w:pPr"))
    if ppr is not None:
        new_p.append(copy.deepcopy(ppr))
    rpr = next((_rpr_clone(r) for r in p_el.findall(qn("w:r")) if _is_text_run(r)), None)
    new_p.append(_ins(text, author, ids.next(), rpr))
    p_el.addnext(new_p)
    return new_p


# ── comment reading / anchoring ───────────────────────────────────────────────

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
    """Character offsets ``[start, end)`` of each comment's anchored range, measured over the
    same serialized text ``paragraph_text`` returns.

    The reviewer highlights the phrase their comment is about, so this recovers WHICH
    SENTENCES a comment actually bears on. This is what lets the minimal-edit guard know that
    a comment on sentence 2 does not license rewriting sentences 1 and 3-7.
    """
    offset = 0
    n = 0
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
            # Must mirror serialize_paragraph's numbering exactly. Hardcoding the width as
            # len("⟦m:0⟧") drifts one char per atom from the tenth atom onward, which
            # silently mis-anchors every comment after it in a paragraph with many atoms —
            # e.g. a Results paragraph full of inline statistics.
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
    for i, unit in enumerate(sentence_units(text)):
        if pos < span[1] and span[0] < pos + len(unit):
            out.add(i)
        pos += len(unit)
    return out


def is_heading_style(style_name: str) -> bool:
    """True for Word heading/title styles (so we never rewrite a heading as prose)."""
    s = (style_name or "").lower()
    return s.startswith("heading") or s == "title"


def _accepted_para_text(p_el) -> str:
    """One paragraph as it reads with every tracked change accepted.

    Insertions kept, deletions dropped — ``w:t`` lives in normal and ``<w:ins>`` runs, while
    deleted text sits in ``<w:delText>`` and is therefore skipped.
    """
    out: list[str] = []
    for r in p_el.iter(qn("w:r")):
        parent = r.getparent()
        if parent is not None and parent.tag == qn("w:del"):
            continue
        for t in r.iter(qn("w:t")):
            out.append(t.text or "")
    return "".join(out)

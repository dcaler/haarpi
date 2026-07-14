"""Fixture factory: a .docx carrying real comment threads.

Word's comment plumbing spans three parts that must agree — the body anchors
(``commentRangeStart``/``End``), ``comments.xml`` (who said what), and
``commentsExtended.xml`` (resolution and threading, keyed by paragraph id, not comment
id). A fixture that fakes any one of them tests nothing: the resolved-comment bug lived
precisely in the gap between the part the gate read and the part the reviser read.

Shared by every package's tests, because every package reads these parts through
``haarpi.redline``.
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from lxml import etree

_W14 = "http://schemas.microsoft.com/office/word/2010/wordml"
_W15 = "http://schemas.microsoft.com/office/word/2012/wordml"
_CT = "http://schemas.openxmlformats.org/package/2006/content-types"
_RELS = "http://schemas.openxmlformats.org/package/2006/relationships"

_COMMENTS_XML = (
    '<w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:w14="{w14}">{body}</w:comments>'
)
_COMMENT_XML = (
    '<w:comment w:id="{cid}" w:author="{author}" w:initials="{initials}">'
    '<w:p w14:paraId="{para}"><w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'
    "</w:comment>"
)
_EXT_XML = '<w15:commentsEx xmlns:w15="{w15}">{body}</w15:commentsEx>'

_COMMENTS_CT = ("application/vnd.openxmlformats-officedocument."
                "wordprocessingml.comments+xml")
_EXT_CT = "application/vnd.ms-word.commentsExtended+xml"
_COMMENTS_RT = ("http://schemas.openxmlformats.org/officeDocument/2006/"
                "relationships/comments")
_EXT_RT = "http://schemas.microsoft.com/office/2011/relationships/commentsExtended"


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def write_commented_docx(path: Path, paragraphs: list[str],
                         comments: list[dict]) -> Path:
    """Build a .docx whose comments are real: threaded, resolvable, anchored.

    ``comments`` entries: ``{cid, author, text, anchor=0, done=False, parent=None}``
    where ``anchor`` is a paragraph index and ``parent`` is another comment's ``cid``
    (making this one a reply in that thread).
    """
    path = Path(path)
    doc = Document()
    paras = [doc.add_paragraph(t) for t in paragraphs]
    for c in comments:
        p_el = paras[c.get("anchor", 0)]._p
        cid = str(c["cid"])
        p_el.insert(0, p_el.makeelement(qn("w:commentRangeStart"), {qn("w:id"): cid}))
        p_el.append(p_el.makeelement(qn("w:commentRangeEnd"), {qn("w:id"): cid}))
    doc.save(str(path))

    para_of = {str(c["cid"]): f"0000{int(c['cid']):04X}" for c in comments}
    cbody = "".join(
        _COMMENT_XML.format(
            cid=c["cid"], author=_esc(c["author"]),
            initials=_esc(c.get("initials", c["author"][:2])),
            text=_esc(c["text"]), para=para_of[str(c["cid"])])
        for c in comments)
    ebody = "".join(
        '<w15:commentEx w15:paraId="{p}" w15:done="{done}"{parent}/>'.format(
            p=para_of[str(c["cid"])],
            done="1" if c.get("done") else "0",
            parent=(f' w15:paraIdParent="{para_of[str(c["parent"])]}"'
                    if c.get("parent") is not None else ""))
        for c in comments)

    tmp = path.with_suffix(".rebuilt.docx")
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(tmp, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "[Content_Types].xml":
                root = etree.fromstring(data)
                for part, ct in (("/word/comments.xml", _COMMENTS_CT),
                                 ("/word/commentsExtended.xml", _EXT_CT)):
                    root.append(root.makeelement(
                        f"{{{_CT}}}Override", {"PartName": part, "ContentType": ct}))
                data = etree.tostring(root)
            elif item.filename == "word/_rels/document.xml.rels":
                root = etree.fromstring(data)
                for rid, tgt, rt in (("rId900", "comments.xml", _COMMENTS_RT),
                                     ("rId901", "commentsExtended.xml", _EXT_RT)):
                    root.append(root.makeelement(
                        f"{{{_RELS}}}Relationship",
                        {"Id": rid, "Type": rt, "Target": tgt}))
                data = etree.tostring(root)
            zout.writestr(item, data)
        zout.writestr("word/comments.xml", _COMMENTS_XML.format(w14=_W14, body=cbody))
        zout.writestr("word/commentsExtended.xml", _EXT_XML.format(w15=_W15, body=ebody))
    shutil.move(str(tmp), str(path))
    return path

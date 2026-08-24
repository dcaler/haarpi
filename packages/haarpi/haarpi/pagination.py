"""A pandoc reference doc whose pages are numbered.

Pandoc's default reference.docx has no header or footer parts at all, so every .docx this
pipeline has ever rendered came out unpaginated — which makes a 27-page literature review
impossible to talk about. There is no way to point at a place in it.

Built from pandoc's own default rather than shipped as a binary blob, for the reason raconteur's
numbering reference doc gives: the default tracks the pandoc actually installed, and a committed
.docx is a file nobody can review in a diff.

A footer means four coordinated edits to the OOXML package — the part itself, its relationship,
its content-type override, and the section reference that points at it. Miss any one and Word
opens the file with no footer and no complaint.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

_FOOTER_PART = "word/footer1.xml"
_FOOTER_REL_TYPE = ("http://schemas.openxmlformats.org/officeDocument/2006/"
                    "relationships/footer")
_FOOTER_CONTENT_TYPE = ("application/vnd.openxmlformats-officedocument."
                        "wordprocessingml.footer+xml")

# "Page N of M", centred. PAGE and NUMPAGES are field codes Word evaluates on open; the <w:t>
# inside each field is the cached value it shows until then, so a reader who never lets Word
# recalculate still sees something sane rather than an empty footer.
_FOOTER_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:p>
    <w:pPr><w:jc w:val="center"/></w:pPr>
    <w:r><w:t xml:space="preserve">Page </w:t></w:r>
    <w:r><w:fldChar w:fldCharType="begin"/></w:r>
    <w:r><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>
    <w:r><w:fldChar w:fldCharType="separate"/></w:r>
    <w:r><w:t>1</w:t></w:r>
    <w:r><w:fldChar w:fldCharType="end"/></w:r>
    <w:r><w:t xml:space="preserve"> of </w:t></w:r>
    <w:r><w:fldChar w:fldCharType="begin"/></w:r>
    <w:r><w:instrText xml:space="preserve"> NUMPAGES </w:instrText></w:r>
    <w:r><w:fldChar w:fldCharType="separate"/></w:r>
    <w:r><w:t>1</w:t></w:r>
    <w:r><w:fldChar w:fldCharType="end"/></w:r>
  </w:p>
</w:ftr>
"""


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def _free_rel_id(rels_xml: str) -> str:
    used = {int(m) for m in re.findall(r'Id="rId(\d+)"', rels_xml)}
    n = 1
    while n in used:
        n += 1
    return f"rId{n}"


def _add_footer_rel(rels_xml: str, rel_id: str) -> str:
    rel = (f'<Relationship Type="{_FOOTER_REL_TYPE}" Id="{rel_id}" '
           f'Target="footer1.xml" />')
    return rels_xml.replace("</Relationships>", rel + "</Relationships>")


def _add_content_type(types_xml: str) -> str:
    if f'PartName="/{_FOOTER_PART}"' in types_xml:
        return types_xml
    override = (f'<Override PartName="/{_FOOTER_PART}" '
                f'ContentType="{_FOOTER_CONTENT_TYPE}"/>')
    return types_xml.replace("</Types>", override + "</Types>")


def _reference_footer(doc_xml: str, rel_id: str) -> str:
    """Point the section at the footer.

    ``w:footerReference`` is order-sensitive inside ``w:sectPr`` — it belongs first, ahead of
    ``w:footnotePr`` and the rest. Word rejects a section whose children are out of schema
    order, so appending it is not an option.
    """
    ref = (f'<w:footerReference xmlns:r="http://schemas.openxmlformats.org/'
           f'officeDocument/2006/relationships" w:type="default" r:id="{rel_id}"/>')
    if "<w:sectPr>" in doc_xml:
        return doc_xml.replace("<w:sectPr>", "<w:sectPr>" + ref, 1)
    if m := re.search(r"<w:sectPr\b[^>]*>", doc_xml):
        return doc_xml[:m.end()] + ref + doc_xml[m.end():]
    # No section properties at all: give the body one, immediately before it closes.
    return doc_xml.replace("</w:body>", f"<w:sectPr>{ref}</w:sectPr></w:body>")


def build(dest: Path, base_doc: Path | None = None) -> Path | None:
    """Write a reference .docx carrying a centred 'Page N of M' footer.

    ``base_doc`` layers the footer onto an existing reference doc (raconteur's numbering one,
    say) instead of pandoc's default, so the two are composable rather than exclusive.
    Returns None — never raises — when pandoc is absent or the package cannot be read.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if base_doc is not None and Path(base_doc).is_file():
        blob = Path(base_doc).read_bytes()
    else:
        if not shutil.which("pandoc"):
            _log("[warn] pandoc not found — cannot build the page-number reference doc")
            return None
        try:
            blob = subprocess.run(["pandoc", "--print-default-data-file", "reference.docx"],
                                  capture_output=True, check=True).stdout
        except (subprocess.CalledProcessError, OSError) as e:      # noqa: BLE001
            _log(f"[warn] could not read pandoc's default reference doc ({e})")
            return None
    tmp = dest.with_suffix(".tmp.docx")
    tmp.write_bytes(blob)
    try:
        with zipfile.ZipFile(tmp) as zin:
            parts = {n: zin.read(n) for n in zin.namelist()}
    except zipfile.BadZipFile as e:                                # noqa: BLE001
        _log(f"[warn] reference doc is not a readable .docx ({e})")
        tmp.unlink(missing_ok=True)
        return None
    finally:
        tmp.unlink(missing_ok=True)

    rels_name = "word/_rels/document.xml.rels"
    if rels_name not in parts or "word/document.xml" not in parts:
        _log("[warn] reference doc is missing its document part — cannot add a footer")
        return None
    rels = parts[rels_name].decode("utf-8")
    rel_id = _free_rel_id(rels)
    parts[rels_name] = _add_footer_rel(rels, rel_id).encode("utf-8")
    parts["[Content_Types].xml"] = _add_content_type(
        parts["[Content_Types].xml"].decode("utf-8")).encode("utf-8")
    parts["word/document.xml"] = _reference_footer(
        parts["word/document.xml"].decode("utf-8"), rel_id).encode("utf-8")
    parts[_FOOTER_PART] = _FOOTER_XML.encode("utf-8")

    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in parts.items():
            zout.writestr(name, data)
    return dest


def reference_for(work_dir: Path, base_doc: Path | None = None) -> Path | None:
    """The cached page-numbered reference doc for a project, built on first use.

    A hand-edited one is never overwritten: if the file is there, it is the author's.
    """
    dest = Path(work_dir) / "reference_paged.docx"
    if dest.is_file():
        return dest
    return build(dest, base_doc)

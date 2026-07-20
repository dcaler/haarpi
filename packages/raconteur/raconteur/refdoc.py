"""The .docx reference document that numbers the headings.

Section numbers are a property of the heading STYLE, not digits in the text. A "2.1" typed
into a heading is a number the author has to renumber by hand every time a section moves,
and it is the number a drafting model reads as a contract — an outline running 1.1, 1.3
reads as a missing 1.2, and the css2026 draft duly invented one. Word's own outline
numbering has neither problem: insert a section and everything below it renumbers itself.

So the markdown carries no numbers, and this builds the reference.docx that supplies them:
a multilevel list bound to the heading styles, giving `1`, `1.1`, `1.1.1`.

    ## Introduction        ->  1 Introduction
    ### The Model          ->  1.1 The Model

WHICH styles: raconteur's markdown puts the paper's title at `# `, so pandoc renders
sections at Heading 2 and subsections at Heading 3. The numbering is therefore bound to
Heading 2/3/4 rather than 1/2/3 — the rendered result is what was asked for ("1", "1.1"),
and the alternative was moving the title into metadata and re-levelling every heading in
the codebase's parsers.

The Abstract, Acknowledgements and References are headings at the same level and must NOT
be numbered. Pandoc gives them the same style as a numbered section — `{.unnumbered}` does
not survive into the .docx — so they are suppressed per paragraph afterwards, which is
Word's own idiom for it (``numId 0``).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import zipfile
from pathlib import Path

from . import guards
from .log import log

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

# High ids, to sit clear of whatever pandoc's own default ships with (990/1000 today).
ABSTRACT_NUM_ID = 800
NUM_ID = 800

# markdown level -> (Word style id, list level). `# ` is the title and is never numbered.
_BOUND = {"Heading2": 0, "Heading3": 1, "Heading4": 2}

_LVL_TEXT = ("%1", "%1.%2", "%1.%2.%3")


def _abstract_num() -> str:
    lvls = []
    for i, text in enumerate(_LVL_TEXT):
        lvls.append(
            f'<w:lvl w:ilvl="{i}">'
            f'<w:start w:val="1"/><w:numFmt w:val="decimal"/>'
            f'<w:lvlText w:val="{text}"/><w:lvlJc w:val="left"/>'
            f'<w:pPr><w:ind w:left="0" w:firstLine="0"/></w:pPr>'
            f"</w:lvl>")
    return (f'<w:abstractNum w:abstractNumId="{ABSTRACT_NUM_ID}">'
            f'<w:multiLevelType w:val="multilevel"/>'
            + "".join(lvls) + "</w:abstractNum>")


def _inject_numbering(xml: str) -> str:
    """Add our abstractNum + num to numbering.xml, idempotently."""
    if f'w:abstractNumId="{ABSTRACT_NUM_ID}"' in xml:
        return xml
    # abstractNum elements must all precede the num elements — Word rejects the part
    # outright if they interleave, and a rejected part means an unopenable document.
    num = f'<w:num w:numId="{NUM_ID}"><w:abstractNumId w:val="{ABSTRACT_NUM_ID}"/></w:num>'
    if "<w:num " in xml:
        i = xml.index("<w:num ")
        return xml[:i] + _abstract_num() + xml[i:].replace("</w:numbering>", num + "</w:numbering>")
    return xml.replace("</w:numbering>", _abstract_num() + num + "</w:numbering>")


def _enable_track_changes(xml: str) -> str:
    """Turn revision recording ON for the document, idempotently.

    Every human-gated document in this pipeline is answered by a redline, and the redline
    contract rests on knowing which spans the author typed by hand: a tracked insertion is
    an atom the tool preserves and may never author. An author who forgets to switch
    tracking on loses that protection silently — their edits arrive as ordinary text,
    indistinguishable from the tool's own, and the only defence left is freezing whole
    paragraphs.

    ``w:trackChanges`` records; it does not enforce. The author can still turn it off, which
    is right — a document that refuses to let you edit it untracked is a document you fight.
    """
    if "<w:trackChanges" in xml:
        return xml
    # Order matters in w:settings — the schema is a sequence — but Word tolerates
    # trackChanges early, and putting it first avoids guessing at the neighbours present.
    i = xml.index(">", xml.index("<w:settings")) + 1
    return xml[:i] + "<w:trackChanges/>" + xml[i:]


def _bind_styles(xml: str) -> str:
    """Attach the list to each heading style, idempotently."""
    for style_id, ilvl in _BOUND.items():
        m = re.search(rf'(<w:style [^>]*w:styleId="{style_id}".*?</w:style>)', xml, re.S)
        if not m:
            log(f"[warn] reference doc has no {style_id} style — headings at that level "
                f"will not be numbered")
            continue
        block = m.group(1)
        if f'w:numId w:val="{NUM_ID}"' in block:
            continue
        numpr = (f'<w:numPr><w:ilvl w:val="{ilvl}"/>'
                 f'<w:numId w:val="{NUM_ID}"/></w:numPr>')
        if "<w:pPr>" in block:
            new = block.replace("<w:pPr>", "<w:pPr>" + numpr, 1)
        else:
            new = block.replace("</w:name>", "</w:name><w:pPr>" + numpr + "</w:pPr>", 1)
        xml = xml.replace(block, new, 1)
    return xml


def build(dest: Path) -> Path | None:
    """Write a reference.docx whose heading styles carry outline numbering.

    Built from pandoc's own default rather than shipped as a binary blob: the default
    tracks the pandoc actually installed, and a committed .docx is a file nobody can review
    in a diff.
    """
    if not shutil.which("pandoc"):
        log("[warn] pandoc not found — cannot build the numbering reference doc")
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        base = subprocess.run(["pandoc", "--print-default-data-file", "reference.docx"],
                              capture_output=True, check=True).stdout
    except subprocess.CalledProcessError as e:      # noqa: BLE001
        log(f"[warn] could not read pandoc's default reference doc ({e})")
        return None
    tmp = dest.with_suffix(".tmp.docx")
    tmp.write_bytes(base)
    with zipfile.ZipFile(tmp) as zin:
        parts = {n: zin.read(n) for n in zin.namelist()}
    numbering = parts.get("word/numbering.xml", b"").decode("utf-8")
    if not numbering:
        log("[warn] pandoc's reference doc has no numbering part — cannot attach numbering")
        tmp.unlink(missing_ok=True)
        return None
    parts["word/numbering.xml"] = _inject_numbering(numbering).encode("utf-8")
    parts["word/styles.xml"] = _bind_styles(
        parts["word/styles.xml"].decode("utf-8")).encode("utf-8")
    settings = parts.get("word/settings.xml", b"").decode("utf-8")
    if settings:
        parts["word/settings.xml"] = _enable_track_changes(settings).encode("utf-8")
    else:
        log("[warn] reference doc has no settings part — track changes not enabled")
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in parts.items():
            zout.writestr(name, data)
    tmp.unlink(missing_ok=True)
    log(f"[raconteur] built numbering reference doc: {dest.name}")
    return dest


def unnumber_furniture(docx_path: Path) -> int:
    """Suppress numbering on the headings that must not carry a number.

    The Abstract, Acknowledgements and References sit at the same heading level as a
    numbered section and pandoc gives them the same style, so the style cannot tell them
    apart. ``numId 0`` on the paragraph is Word's own way of saying "not this one".

    Uses the same predicates as every other reader in the codebase, so "Reference list" and
    "Acknowledgments" are recognised here exactly as they are everywhere else.
    """
    from docx import Document
    from docx.oxml.ns import qn

    doc = Document(str(docx_path))
    n = 0
    for p in doc.paragraphs:
        text = p.text.strip()
        if not text or not (p.style.name or "").lower().startswith("heading"):
            continue
        if not (guards.is_abstract(text) or guards.is_references(text)
                or guards.is_acknowledgements(text)):
            continue
        pPr = p._p.get_or_add_pPr()
        for existing in pPr.findall(qn("w:numPr")):
            pPr.remove(existing)
        numpr = pPr.makeelement(qn("w:numPr"), {})
        ilvl = pPr.makeelement(qn("w:ilvl"), {qn("w:val"): "0"})
        numid = pPr.makeelement(qn("w:numId"), {qn("w:val"): "0"})
        numpr.append(ilvl)
        numpr.append(numid)
        pPr.insert(0, numpr)
        n += 1
    if n:
        doc.save(str(docx_path))
    return n


def enable_track_changes(docx_path: Path) -> bool:
    """Switch revision recording on in a RENDERED document.

    Applied to the output rather than the reference doc: pandoc writes its own
    settings.xml and discards the reference document's, so setting it upstream is silently
    dropped — verified, not assumed.
    """
    try:
        with zipfile.ZipFile(docx_path) as zin:
            parts = {n: zin.read(n) for n in zin.namelist()}
    except (zipfile.BadZipFile, OSError) as e:      # noqa: BLE001
        log(f"[warn] could not open {docx_path.name} to enable track changes ({e})")
        return False
    settings = parts.get("word/settings.xml", b"").decode("utf-8")
    if not settings or "<w:trackChanges" in settings:
        return False
    parts["word/settings.xml"] = _enable_track_changes(settings).encode("utf-8")
    with zipfile.ZipFile(docx_path, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in parts.items():
            zout.writestr(name, data)
    return True


def reference_for(project_dir: Path) -> Path | None:
    """This project's numbering reference doc, built on first use.

    Kept in paper/ rather than bundled: it is regenerated from whatever pandoc is installed,
    and a project may legitimately carry a hand-edited one (a venue's house style) that must
    not be overwritten.
    """
    dest = project_dir / "paper" / "reference.docx"
    if dest.exists():
        return dest
    return build(dest)


def render(md_path: Path, project_dir: Path, **kw) -> Path | None:
    """Render markdown to .docx with numbered headings and unnumbered furniture.

    The one place the two halves are kept together: attaching the numbering without
    suppressing it on the Abstract would number the Abstract, which is worse than not
    numbering at all.
    """
    from .render import to_docx
    out = to_docx(md_path, reference_doc=reference_for(project_dir), **kw)
    if out is not None:
        unnumber_furniture(out)
        enable_track_changes(out)
    return out

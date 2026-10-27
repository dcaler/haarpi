"""Markdown → .docx via pandoc — the one conversion path for the pipeline.

GENESIS ONLY. A deliverable's FIRST draft is written in markdown and rendered here;
every cycle after that edits the .docx in place (see ``haarpi.redline``). Markdown is
lossy in exactly the dimensions a reviewed document cares about — who authored which
span, where a comment is anchored, which drawing is which — so once a document carries
markup, it is never re-rendered from markdown again.

This is where citations become citations. Without ``--citeproc`` a ``[@key]`` reaches
the reader as the literal string ``[@key]``, which is how a released litreview came to
carry 70 raw citekeys in its body. Every caller that renders prose containing citekeys
must pass a bibliography.

Degrades politely: without pandoc the .md is still written and the caller is told; a
failed conversion warns and returns None/False rather than raising.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path


# Word's flag for "record revisions" is w:trackRevisions. w:trackChanges is not an element
# of CT_Settings at all, so Word ignored it in silence — which is why every document this
# pipeline ever rendered opened with tracking OFF and every author had to switch it on by
# hand, and why the check that reported it "on" was reading its own invention back.
# Ground truth, from settings.xml as Word itself wrote it on a redlined skeleton:
#     … stylePaneFormatFilter, trackRevisions, defaultTabStop …
_TRACK = "<w:trackRevisions/>"

# CT_Settings is a strict sequence, so the flag cannot simply go first. These are the
# elements that FOLLOW trackRevisions in that sequence, nearest first: the flag goes before
# whichever of them the document actually has. pandoc emits doNotTrackMoves, Word emits
# defaultTabStop, and both land in the right slot.
_AFTER_TRACK = ("w:doNotTrackMoves", "w:doNotTrackFormatting", "w:documentProtection",
                "w:autoFormatOverride", "w:styleLockTheme", "w:styleLockQFSet",
                "w:defaultTabStop")


def _with_track_changes(xml: str) -> str:
    """Turn revision recording on in a settings.xml, idempotently."""
    if "<w:trackRevisions" in xml:
        return xml
    for tag in _AFTER_TRACK:
        i = xml.find("<" + tag)
        if i != -1:
            return xml[:i] + _TRACK + xml[i:]
    i = xml.rfind("</w:settings>")
    if i != -1:
        return xml[:i] + _TRACK + xml[i:]
    i = xml.index(">", xml.index("<w:settings")) + 1
    return xml[:i] + _TRACK + xml[i:]


def enable_track_changes(docx_path: Path) -> bool:
    """Switch revision recording on in a rendered .docx.

    Every human-gated document in this pipeline is answered by a redline, and the redline
    contract rests on knowing which spans the author typed by hand: a tracked insertion is
    an atom the tool preserves and may never author. An author who forgets to switch
    tracking on loses that protection silently — their edits arrive as ordinary text,
    indistinguishable from the tool's own, and the only defence left is freezing whole
    paragraphs.

    Applied to the OUTPUT: pandoc writes its own settings.xml and discards a reference
    document's, so setting it upstream looks right and is silently dropped.

    It records; it does not enforce. ``w:documentProtection`` would stop the author turning
    it off, and a document that refuses to let you edit it untracked is one you fight.
    """
    import zipfile
    try:
        with zipfile.ZipFile(docx_path) as zin:
            parts = {n: zin.read(n) for n in zin.namelist()}
    except (zipfile.BadZipFile, OSError, KeyError):
        return False
    settings = parts.get("word/settings.xml", b"").decode("utf-8")
    if not settings or "<w:trackRevisions" in settings:
        return False
    parts["word/settings.xml"] = _with_track_changes(settings).encode("utf-8")
    with zipfile.ZipFile(docx_path, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, data in parts.items():
            zout.writestr(name, data)
    return True


# A citekey pandoc can read: letters (any script), digits, underscore and the few separators
# its BibTeX and markdown parsers both accept. Zotero keys are generated from the record, so
# a record that spells O'Sullivan with a prime (U+2032) yields `o′sullivanSchellings2009`,
# and pandoc then refuses the WHOLE bibliography — one stray character, no .docx at all.
_KEY_CHAR = re.compile(r"[^\w:.\-+/]")
_BIB_KEY = re.compile(r"(@\w+\s*\{\s*)([^,\s]+)(\s*,)")


def _pandoc_safe_keys(bib_text: str) -> dict[str, str]:
    """Every bib key pandoc would reject, mapped to a readable stand-in (unique in the file)."""
    keys = [m.group(2) for m in _BIB_KEY.finditer(bib_text)]
    taken, out = set(keys), {}
    for k in keys:
        safe = _KEY_CHAR.sub("", k)
        if safe == k or k in out:
            continue
        base, n = safe or "ref", 1
        while safe in taken or not safe:
            n += 1
            safe = f"{base}{n}"
        taken.add(safe)
        out[k] = safe
    return out


def _with_keys(text: str, keys: dict[str, str], bib: bool) -> str:
    for old, new in keys.items():
        if bib:
            text = _BIB_KEY.sub(lambda m: m.group(1) + (new if m.group(2) == old else m.group(2))
                                + m.group(3), text)
        else:
            text = re.sub(r"@" + re.escape(old) + r"(?=[\s\],;}]|$)", "@" + new, text)
    return text


def _pandoc_ready(src: Path, bib_path: Path | None) -> tuple[Path, Path | None, list[Path]]:
    """The source and bibliography as pandoc should see them, plus the scratch files to remove.

    Untouched unless a key needs repair; then both files are copied beside the originals
    (so relative figure paths still resolve) with the key rewritten in the bib entry AND in
    every citation of it. The files on disk keep the key exactly as Zotero wrote it."""
    if bib_path is None or not Path(bib_path).exists():
        return src, bib_path, []
    bib_text = Path(bib_path).read_text(encoding="utf-8", errors="replace")
    keys = _pandoc_safe_keys(bib_text)
    if not keys:
        return src, bib_path, []
    print(f"  [note] citekeys pandoc cannot read, renamed for this render: "
          + ", ".join(f"{k} → {v}" for k, v in keys.items()))
    bib2 = Path(bib_path).with_name(f".{Path(bib_path).stem}.pandoc.bib")
    bib2.write_text(_with_keys(bib_text, keys, bib=True), encoding="utf-8")
    scratch = [bib2]
    if src.suffix.lower() == ".md":
        src2 = src.with_name(f".{src.stem}.pandoc.md")
        src2.write_text(_with_keys(src.read_text(encoding="utf-8"), keys, bib=False),
                        encoding="utf-8")
        scratch.append(src2)
        src = src2
    return src, bib2, scratch


def check_pandoc() -> bool:
    try:
        subprocess.run(["pandoc", "--version"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def _pandoc_cmd(src: Path, dst: Path, bib_path: Path | None,
                resource_path: Path | None, suppress_bibliography: bool,
                reference_doc: Path | None = None) -> list[str]:
    cmd = ["pandoc", str(src), "-o", str(dst)]
    if reference_doc is not None and Path(reference_doc).exists():
        # Styles come from the reference doc — including the outline numbering bound to the
        # heading styles, which is why the markdown carries no numbers of its own.
        cmd += ["--reference-doc", str(reference_doc)]
    if bib_path is not None and Path(bib_path).exists():
        cmd += ["--bibliography", str(bib_path), "--citeproc"]
        if suppress_bibliography:
            # The caller already writes its own reference list (rabbitHole's annotated
            # bibliography). Citeproc would append a second one.
            cmd += ["-M", "suppress-bibliography=true"]
    if resource_path is not None:
        # let pandoc resolve figure paths (e.g. results/figures/x.png) relative
        # to the project root regardless of the current working directory
        cmd += ["--resource-path", str(resource_path)]
    return cmd


def pandoc_convert(src: Path, dst: Path, bib_path: Path | None = None,
                   resource_path: Path | None = None,
                   suppress_bibliography: bool = False,
                   reference_doc: Path | None = None,
                   track_changes: bool = True) -> bool:
    """Explicit src → dst conversion. The single pandoc invocation in the pipeline."""
    if not shutil.which("pandoc"):
        print("  [note] pandoc not found — skipping .docx (md written). "
              "Install pandoc to get .docx output.")
        return False
    src, bib_path, scratch = _pandoc_ready(src, bib_path)
    cmd = _pandoc_cmd(src, dst, bib_path, resource_path, suppress_bibliography,
                      reference_doc)
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        if track_changes:
            # Every .docx this pipeline renders is a document a human will mark up. The
            # submission package is built by its own pandoc invocation and is unaffected.
            enable_track_changes(dst)
        return True
    except subprocess.CalledProcessError as e:
        print(f"  [warn] pandoc failed: {e.stderr.decode()[:200]}")
        return False
    finally:
        for p in scratch:
            p.unlink(missing_ok=True)


def to_docx(
    md_path: Path,
    bib_path: Path | None = None,
    resource_path: Path | None = None,
    suppress_bibliography: bool = False,
    reference_doc: Path | None = None,
    track_changes: bool = True,
) -> Path | None:
    """Convert alongside the source (`x.md` → `x.docx`), optionally with a
    bibliography (citeproc), a resource path for figure resolution, and a reference
    document supplying the styles (heading numbering among them)."""
    docx_path = md_path.with_suffix(".docx")
    ok = pandoc_convert(md_path, docx_path, bib_path, resource_path,
                        suppress_bibliography, reference_doc, track_changes)
    return docx_path if ok else None

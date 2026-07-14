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

import shutil
import subprocess
import sys
from pathlib import Path


def check_pandoc() -> bool:
    try:
        subprocess.run(["pandoc", "--version"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def _pandoc_cmd(src: Path, dst: Path, bib_path: Path | None,
                resource_path: Path | None, suppress_bibliography: bool) -> list[str]:
    cmd = ["pandoc", str(src), "-o", str(dst)]
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
                   suppress_bibliography: bool = False) -> bool:
    """Explicit src → dst conversion. The single pandoc invocation in the pipeline."""
    if not shutil.which("pandoc"):
        print("  [note] pandoc not found — skipping .docx (md written). "
              "Install pandoc to get .docx output.")
        return False
    cmd = _pandoc_cmd(src, dst, bib_path, resource_path, suppress_bibliography)
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"  [warn] pandoc failed: {e.stderr.decode()[:200]}")
        return False


def to_docx(
    md_path: Path,
    bib_path: Path | None = None,
    resource_path: Path | None = None,
    suppress_bibliography: bool = False,
) -> Path | None:
    """Convert alongside the source (`x.md` → `x.docx`), optionally with a
    bibliography (citeproc) and a resource path for figure resolution."""
    docx_path = md_path.with_suffix(".docx")
    ok = pandoc_convert(md_path, docx_path, bib_path, resource_path,
                        suppress_bibliography)
    return docx_path if ok else None

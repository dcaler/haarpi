"""Markdown → .docx via pandoc — the one conversion path for the pipeline.

Degrades politely: without pandoc the .md is still written and the caller is
told; a failed conversion warns and returns None/False rather than raising.
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


def to_docx(
    md_path: Path,
    bib_path: Path | None = None,
    resource_path: Path | None = None,
) -> Path | None:
    """Convert alongside the source (`x.md` → `x.docx`), optionally with a
    bibliography (citeproc) and a resource path for figure resolution."""
    docx_path = md_path.with_suffix(".docx")
    cmd = ["pandoc", str(md_path), "-o", str(docx_path)]
    if bib_path is not None and bib_path.exists():
        cmd += ["--bibliography", str(bib_path), "--citeproc"]
    if resource_path is not None:
        # let pandoc resolve figure paths (e.g. results/figures/x.png) relative
        # to the project root regardless of the current working directory
        cmd += ["--resource-path", str(resource_path)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"[warn] pandoc: {r.stderr[:200]}", file=sys.stderr)
            return None
        return docx_path
    except FileNotFoundError:
        print("[warn] pandoc not found — skipping .docx output", file=sys.stderr)
        return None


def pandoc_convert(src: Path, dst: Path) -> bool:
    """Explicit src → dst conversion (rabbitHole style)."""
    if not shutil.which("pandoc"):
        print("  [note] pandoc not found — skipping .docx (md written). "
              "Install pandoc to get .docx output.")
        return False
    try:
        subprocess.run(["pandoc", str(src), "-o", str(dst)],
                       check=True, capture_output=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"  [warn] pandoc failed: {e.stderr.decode()[:200]}")
        return False

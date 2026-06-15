from __future__ import annotations
import subprocess
import sys
from pathlib import Path


def check_pandoc() -> bool:
    try:
        subprocess.run(["pandoc", "--version"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def to_docx(md_path: Path, bib_path: Path | None = None) -> Path | None:
    docx_path = md_path.with_suffix(".docx")
    cmd = ["pandoc", str(md_path), "-o", str(docx_path)]
    if bib_path is not None and bib_path.exists():
        cmd += ["--bibliography", str(bib_path), "--citeproc"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"[warn] pandoc: {r.stderr[:200]}", file=sys.stderr)
            return None
        return docx_path
    except FileNotFoundError:
        print("[warn] pandoc not found — skipping .docx output", file=sys.stderr)
        return None

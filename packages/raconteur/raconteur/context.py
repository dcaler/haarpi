from __future__ import annotations
import sys
from pathlib import Path

_LIT_GLOB = "{litrev_dir}/output/*.md"
_CODE_SUFFIXES = {".py", ".R", ".jl", ".ipynb"}
_RESULTS_SUFFIXES = {".py", ".R", ".jl", ".ipynb", ".txt", ".md", ".csv", ".tsv", ".json"}
_MAX_LITREV_CHARS = 12000
_MAX_CODE_CHARS = 4000
_MAX_RESULTS_CHARS = 4000
_MAX_FILE_LINES = 80


def load_litreview(project_dir: Path, subdir: str = "litReview") -> str:
    """Read the most recent literature review from {subdir}/output/."""
    glob = _LIT_GLOB.format(litrev_dir=subdir)
    files = sorted(
        project_dir.glob(glob),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not files:
        return ""
    text = files[0].read_text(encoding="utf-8", errors="replace")
    if len(text) > _MAX_LITREV_CHARS:
        text = text[:_MAX_LITREV_CHARS] + "\n\n[truncated]"
    print(f"[raconteur] reading litreview ({subdir}): {files[0].name}", file=sys.stderr)
    return text


def load_code(project_dir: Path, subdir: str = "code") -> str:
    """Read analysis scripts from methods directory."""
    code_dir = project_dir / subdir
    if not code_dir.is_dir():
        return ""
    parts = []
    total = 0
    for p in sorted(code_dir.rglob("*")):
        if p.suffix not in _CODE_SUFFIXES or not p.is_file():
            continue
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            snippet = "\n".join(lines[:_MAX_FILE_LINES])
            chunk = f"### {p.relative_to(code_dir)}\n```\n{snippet}\n```\n"
            if total + len(chunk) > _MAX_CODE_CHARS:
                break
            parts.append(chunk)
            total += len(chunk)
        except Exception:
            continue
    if not parts:
        return ""
    print(f"[raconteur] reading methods ({subdir}): {len(parts)} file(s)", file=sys.stderr)
    return "\n".join(parts)


def load_results(project_dir: Path, subdir: str = "results") -> str:
    """Read results files from results directory."""
    results_dir = project_dir / subdir
    if not results_dir.is_dir():
        return ""
    parts = []
    total = 0
    for p in sorted(results_dir.rglob("*")):
        if p.suffix not in _RESULTS_SUFFIXES or not p.is_file():
            continue
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            snippet = "\n".join(lines[:_MAX_FILE_LINES])
            chunk = f"### {p.relative_to(results_dir)}\n```\n{snippet}\n```\n"
            if total + len(chunk) > _MAX_RESULTS_CHARS:
                break
            parts.append(chunk)
            total += len(chunk)
        except Exception:
            continue
    if not parts:
        return ""
    print(f"[raconteur] reading results ({subdir}): {len(parts)} file(s)", file=sys.stderr)
    return "\n".join(parts)


def load_venue_analysis(project_dir: Path) -> str:
    """Read paper/venue_analysis.md if present."""
    path = project_dir / "paper" / "venue_analysis.md"
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    print("[raconteur] reading venue_analysis.md", file=sys.stderr)
    return text

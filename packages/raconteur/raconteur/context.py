from __future__ import annotations
import sys
from pathlib import Path

_LIT_GLOB = "litReview/output/*.md"
_CODE_SUFFIXES = {".py", ".R", ".jl", ".ipynb"}
_MAX_LITREV_CHARS = 12000
_MAX_CODE_CHARS = 4000
_MAX_FILE_LINES = 80


def load_litreview(project_dir: Path) -> str:
    """Read the most recent RabbitHole literature review from litReview/output/."""
    files = sorted(
        project_dir.glob(_LIT_GLOB),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not files:
        return ""
    text = files[0].read_text(encoding="utf-8", errors="replace")
    if len(text) > _MAX_LITREV_CHARS:
        text = text[:_MAX_LITREV_CHARS] + "\n\n[truncated]"
    print(f"[raconteur] reading litreview: {files[0].name}", file=sys.stderr)
    return text


def load_code(project_dir: Path) -> str:
    """Read analysis code from code/ for methods/results context."""
    code_dir = project_dir / "code"
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
    print(f"[raconteur] reading code: {len(parts)} file(s)", file=sys.stderr)
    return "\n".join(parts)


def load_venue_analysis(project_dir: Path) -> str:
    """Read paper/venue_analysis.md if present."""
    path = project_dir / "paper" / "venue_analysis.md"
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    print("[raconteur] reading venue_analysis.md", file=sys.stderr)
    return text

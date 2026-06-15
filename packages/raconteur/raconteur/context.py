from __future__ import annotations
import re
import sys
from .log import log
from pathlib import Path

_LIT_GLOB = "{litrev_dir}/output/*.md"
_CODE_SUFFIXES = {".py", ".R", ".jl", ".ipynb"}
_RESULTS_SUFFIXES = {".py", ".R", ".jl", ".ipynb", ".txt", ".md", ".csv", ".tsv", ".json"}
_MAX_LITREV_CHARS = 12000
_MAX_CODE_CHARS = 20000
_MAX_RESULTS_CHARS = 4000
_MAX_FILE_LINES = 200
_MAX_BIB_CHARS = 4000


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
    log(f"[raconteur] reading litreview ({subdir}): {files[0].name}")
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
            snippet = p.read_text(encoding="utf-8", errors="replace")
            chunk = f"### {p.relative_to(code_dir)}\n```\n{snippet}\n```\n"
            if total + len(chunk) > _MAX_CODE_CHARS:
                break
            parts.append(chunk)
            total += len(chunk)
        except Exception:
            continue
    if not parts:
        return ""
    log(f"[raconteur] reading methods ({subdir}): {len(parts)} file(s)")
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
    log(f"[raconteur] reading results ({subdir}): {len(parts)} file(s)")
    return "\n".join(parts)


def _parse_bib(text: str) -> list[tuple[str, str, str, str]]:
    """Parse BibTeX → [(citekey, first_author, year, short_title), ...]."""
    entries = []
    citekey = author = year = title = ""
    for line in text.splitlines():
        line = line.strip()
        m = re.match(r'@\w+\{([^,\s]+)\s*,', line)
        if m:
            if citekey:
                entries.append((citekey, author, year, title))
            citekey = m.group(1).strip()
            author = year = title = ""
            continue
        if not citekey:
            continue
        am = re.match(r'author\s*=\s*\{(.+)\},?\s*$', line, re.IGNORECASE)
        if am and not author:
            raw = am.group(1).strip()
            first = raw.split(" and ")[0].strip()
            author = first.split(",")[0].strip() if "," in first else (first.split()[-1] if first else "")
            if " and " in raw:
                author += " et al."
        ym = re.match(r'year\s*=\s*\{?(\d{4})\}?,?\s*$', line, re.IGNORECASE)
        if ym and not year:
            year = ym.group(1)
        tm = re.match(r'title\s*=\s*\{(.+)\},?\s*$', line, re.IGNORECASE)
        if tm and not title:
            raw_t = re.sub(r'[{}]', '', tm.group(1)).strip()
            title = raw_t[:60] + ("…" if len(raw_t) > 60 else "")
    if citekey:
        entries.append((citekey, author, year, title))
    return entries


def load_bib_summary(project_dir: Path, subdir: str = "litReview") -> str:
    """Return compact citekey list from refs.bib for citation guidance in prompts."""
    bib_path = project_dir / subdir / "output" / "refs.bib"
    if not bib_path.exists():
        return ""
    text = bib_path.read_text(encoding="utf-8", errors="replace")
    entries = _parse_bib(text)
    if not entries:
        return ""
    log(f"[raconteur] reading refs.bib: {len(entries)} entries")
    lines = [f"[@{e[0]}] {e[1]} ({e[2]}). {e[3]}" for e in entries]
    summary = "\n".join(lines)
    if len(summary) > _MAX_BIB_CHARS:
        summary = summary[:_MAX_BIB_CHARS] + "\n[…truncated]"
    return summary


def load_venue_analysis(project_dir: Path) -> str:
    """Read paper/venue_analysis.md if present."""
    path = project_dir / "paper" / "venue_analysis.md"
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    log("[raconteur] reading venue_analysis.md")
    return text

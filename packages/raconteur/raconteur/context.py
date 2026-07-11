from __future__ import annotations
import re
import sys
from .log import log
from pathlib import Path

_LIT_GLOB = "{litrev_dir}/output/*.md"
_RESULTS_SUFFIXES = {".py", ".R", ".jl", ".ipynb", ".txt", ".md", ".csv", ".tsv", ".json"}
_MAX_LITREV_CHARS = 12000
_MAX_METHODS_CHARS = 20000
_MAX_RESULTS_CHARS = 4000
_MAX_RESULTS_DIGEST_CHARS = 20000
_MAX_FILE_LINES = 200
_MAX_BIB_CHARS = 4000

# Default output locations of the upstream ra* tools raconteur consumes.
DEFAULT_LITREV_DIR = "litReview"   # rabbitHole
DEFAULT_RESULTS_DIR = "results"    # rayleigh
# raster writes a purpose-built methods writeup at the project root:
#   <date>_<project>_methods_<initials_chain>.md
_METHODS_RE = re.compile(r"^(\d{6})_(?:.+_)?methods((?:_[A-Za-z]+)+)\.md$")
# rayleigh's counterpart digests live at the results root: a chained
#   <date>_<project>_results[_<initials_chain>].md   (chain absent = a release)
# or its working writeup RESULTS.md.
_RESULTS_DIGEST_RE = re.compile(r"^(\d{6})_(?:.+_)?results((?:_[A-Za-z]+)*)\.md$")


def _litrev_complete(d: Path) -> bool:
    out = d / "output"
    return out.is_dir() and any(out.glob("*.md"))


def find_methods_file(project_dir: Path) -> Path | None:
    """Latest raster methods writeup.

    Matches ``<date>_<project>_methods_<chain>.md`` (chained like paper files).
    Picks the highest datestamp, breaking ties by most-recent mtime, so the
    newest state of the writeup wins regardless of who last touched the chain.
    Searched tiered like haarpi's release lookup: the build stage's output dir
    first (`code/output/`, where raster handoff writes), then `code/`, then the
    project root (where legacy handoffs landed).
    """
    for d in (project_dir / "code" / "output", project_dir / "code", project_dir):
        if not d.is_dir():
            continue
        candidates = []
        for p in d.glob("*_methods_*.md"):
            m = _METHODS_RE.match(p.name)
            if m:
                candidates.append((m.group(1), p.stat().st_mtime, p))
        if candidates:
            candidates.sort(key=lambda t: (t[0], t[1]))
            return candidates[-1][2]
    return None


def _results_complete(d: Path) -> bool:
    if not d.is_dir():
        return False
    if (d / "findings.json").exists():   # rayleigh's structured results
        return True
    return any(
        p.is_file() and p.suffix in _RESULTS_SUFFIXES for p in d.rglob("*")
    )


def check_prerequisites(project_dir: Path, cfg) -> None:
    """Warn loudly for any upstream ra* tool whose output is missing.

    raconteur expects rabbitHole, raster, and rayleigh to have run to
    completion before it does. Missing outputs are non-fatal (a theory paper
    may legitimately have no experiments), but each is warned loudly so the
    absence is a deliberate choice rather than an oversight.
    """
    litrev_dir = project_dir / (cfg.litrev_dir or DEFAULT_LITREV_DIR)
    results_dir = project_dir / (cfg.results_dir or DEFAULT_RESULTS_DIR)
    checks = [
        ("rabbitHole", "literature review",
         _litrev_complete(litrev_dir), f"{litrev_dir.name}/"),
        ("raster", "methods writeup",
         find_methods_file(project_dir) is not None, "*_methods_*.md"),
        ("rayleigh", "experiment results",
         _results_complete(results_dir), f"{results_dir.name}/"),
    ]
    missing = [(tool, what, where) for tool, what, ok, where in checks if not ok]
    if not missing:
        log("[raconteur] upstream outputs present: rabbitHole, raster, rayleigh")
        return
    log("[warn] ────────────────────────────────────────────────")
    log("[warn] raconteur expects rabbitHole, raster, and rayleigh")
    log("[warn] to be complete before it runs. Missing:")
    for tool, what, where in missing:
        log(f"[warn]   • {tool} — no {what} found ({where})")
    log("[warn] Proceeding with reduced context.")
    log("[warn] ────────────────────────────────────────────────")


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


def load_methods(project_dir: Path) -> str:
    """Read raster's methods writeup (<date>_methods_<chain>.md at project root)."""
    path = find_methods_file(project_dir)
    if path is None:
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) > _MAX_METHODS_CHARS:
        text = text[:_MAX_METHODS_CHARS] + "\n\n[truncated]"
    log(f"[raconteur] reading methods: {path.name}")
    return text


def find_results_file(results_dir: Path) -> Path | None:
    """rayleigh's purpose-built results writeup at the results root.

    Prefers the chained deliverable (highest datestamp, mtime tie-break, like
    find_methods_file); falls back to the working RESULTS.md. None if neither
    exists — the caller then crawls the directory instead.
    """
    candidates = []
    for p in results_dir.glob("*_results*.md"):
        m = _RESULTS_DIGEST_RE.match(p.name)
        if m:
            candidates.append((m.group(1), p.stat().st_mtime, p))
    if candidates:
        candidates.sort(key=lambda t: (t[0], t[1]))
        return candidates[-1][2]
    working = results_dir / "RESULTS.md"
    return working if working.is_file() else None


def load_results(project_dir: Path, subdir: str = "results") -> str:
    """Read the results writeup, or failing that sample the results directory.

    rayleigh writes a digest for exactly this purpose — read it whole (methods
    treatment) rather than trawling raw run outputs past it.
    """
    results_dir = project_dir / subdir
    if not results_dir.is_dir():
        return ""
    digest = find_results_file(results_dir)
    if digest is not None:
        text = digest.read_text(encoding="utf-8", errors="replace")
        if len(text) > _MAX_RESULTS_DIGEST_CHARS:
            text = text[:_MAX_RESULTS_DIGEST_CHARS] + "\n\n[truncated]"
        log(f"[raconteur] reading results digest: {digest.name}")
        return text
    parts = []
    total = 0
    for p in sorted(results_dir.rglob("*")):
        if p.suffix not in _RESULTS_SUFFIXES or not p.is_file():
            continue
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            snippet = "\n".join(lines[:_MAX_FILE_LINES])
            chunk = f"### {p.relative_to(results_dir)}\n```\n{snippet}\n```\n"
            remaining = _MAX_RESULTS_CHARS - total
            if len(chunk) > remaining:
                if parts:
                    break
                # the first candidate alone overflows the budget: keep what fits
                # rather than returning nothing
                chunk = chunk[:remaining] + "\n[truncated]\n"
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


def load_bib_keys(project_dir: Path, subdir: str = "litReview") -> set[str]:
    """Return the set of citekeys defined in refs.bib.

    ``load_bib_summary`` formats these for a prompt and throws the set away. The guards
    need the set itself: a [@key] outside it is unresolvable and renders as literal text
    in the .docx.
    """
    bib_path = project_dir / subdir / "output" / "refs.bib"
    if not bib_path.exists():
        return set()
    text = bib_path.read_text(encoding="utf-8", errors="replace")
    return {e[0] for e in _parse_bib(text) if e[0]}


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


def load_style_profile(project_dir: Path) -> str:
    """Return the global style profile body (stripped of YAML frontmatter), capped at 2000 chars."""
    from .config import GLOBAL_CONFIG_PATH
    path = GLOBAL_CONFIG_PATH.parent / "style_profile.md"
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    # strip YAML frontmatter
    if text.startswith("---"):
        end = text.find("\n---\n", 3)
        if end != -1:
            text = text[end + 5:]
    text = text.strip()
    if len(text) > 2000:
        text = text[:2000] + "\n[…truncated]"
    log("[raconteur] reading style_profile.md")
    return text


_FIGURE_SUFFIXES = {".png", ".svg", ".pdf", ".jpg", ".jpeg"}
_MAX_FIGURES_LISTED = 12


def load_figure_manifest(project_dir: Path, subdir: str = "results") -> list[str]:
    """List rayleigh figure files as project-relative paths, for figure embedding.

    Returns paths like 'results/figures/opinion_drift.png' that pandoc can
    resolve via --resource-path=<project_dir>. Prefers a figures/ subdir; falls
    back to image files anywhere under the results directory.
    """
    base = project_dir / (subdir or "results")
    fig_dir = base / "figures"
    search = fig_dir if fig_dir.is_dir() else base
    if not search.is_dir():
        return []
    paths = sorted(
        p for p in search.rglob("*")
        if p.is_file() and p.suffix.lower() in _FIGURE_SUFFIXES
        # skip NAS/OS metadata litter (Synology @eaDir thumbnails, dotfiles)
        and not any(part.startswith((".", "@"))
                    for part in p.relative_to(search).parts)
    )
    rel = [str(p.relative_to(project_dir)) for p in paths[:_MAX_FIGURES_LISTED]]
    if rel:
        log(f"[raconteur] found {len(rel)} figure(s) under {subdir}/")
    return rel


def load_onepager(project_dir: Path, short_title: str) -> str:
    """Return the latest tool-generated one-pager narrative (paper/*_onepager_ra.md)."""
    from .naming import find_latest
    paper_dir = project_dir / "paper"
    path = find_latest(
        paper_dir, short_title, "md",
        last_initials="ra", chain_includes="onepager",
    )
    if path is None:
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    log(f"[raconteur] reading one-pager: {path.name}")
    return text


def load_venue_analysis(project_dir: Path) -> str:
    """Read paper/venue_analysis.md if present."""
    path = project_dir / "paper" / "venue_analysis.md"
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    log("[raconteur] reading venue_analysis.md")
    return text

from __future__ import annotations
import json
import re
import sys
from typing import NamedTuple
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


def _read_profile() -> tuple[dict, str]:
    """The style profile: (frontmatter, body). Empty if it has never been trained."""
    import yaml

    from .config import GLOBAL_CONFIG_PATH
    path = GLOBAL_CONFIG_PATH.parent / "style_profile.md"
    if not path.exists():
        return {}, ""
    text = path.read_text(encoding="utf-8", errors="replace")
    meta: dict = {}
    if text.startswith("---"):
        end = text.find("\n---\n", 3)
        if end != -1:
            try:
                meta = yaml.safe_load(text[4:end]) or {}
            except yaml.YAMLError:
                meta = {}
            text = text[end + 5:]
    return meta, text.strip()


def load_style_signature(project_dir: Path) -> dict:
    """The MEASURED voice: rhythm, and the closed-class palettes. What the guards check."""
    meta, _ = _read_profile()
    return meta.get("signature") or {}


def load_style_profile(project_dir: Path, kind: str = "", budget: int = 3800) -> str:
    """The author's voice, as the drafter must receive it.

    A MEASURED palette — the transitions, hedges and rhythm of the author's own published
    prose — followed by passages of the real thing. Exemplars are never cut in half: the old
    loader capped the profile at 2,000 characters with the verbatim excerpts at the END of
    the file, so the excerpts were exactly what got truncated, and every draft raconteur ever
    wrote was styled from a 350-word DESCRIPTION of the author's prose with not one sentence
    of the prose itself. It was cut off, memorably, in the middle of quoting the metaphor
    that best demonstrated the voice it was meant to imitate.

    ``kind`` selects section-appropriate exemplars (a Methods voice is not a Discussion
    voice); without it, all of them are candidates.
    """
    from . import voice

    meta, body = _read_profile()
    if not meta and not body:
        return ""
    log("[raconteur] reading style_profile.md")
    sig = meta.get("signature") or {}
    exemplars = _exemplars(body, kind)
    analysis = (body.split("## Voice — analysis", 1)[1].strip()
                if "## Voice — analysis" in body else "")
    block = voice.style_block(sig, exemplars, analysis, budget=budget)
    if block:
        return block
    # an untrained or hand-written profile: fall back to its prose, whole sentences only
    return body[:budget]


_EX_HEADING = re.compile(r"^#{2,3}\s*(.+?)\s*$")


def _exemplars(body: str, kind: str = "") -> list[str]:
    """Verbatim passages from the profile, preferring those for this section kind."""
    if "## Voice — exemplars" not in body:
        return []
    section = body.split("## Voice — exemplars", 1)[1].split("\n## ", 1)[0]
    out: list[tuple[str, str]] = []
    current = ""
    for line in section.splitlines():
        if (m := _EX_HEADING.match(line.strip())):
            current = m.group(1).strip().lower()
            continue
        raw = line.strip()
        if not raw.startswith(">"):
            continue                      # the section's own prose is not an exemplar
        t = raw.lstrip("> ").strip()
        if len(t.split()) >= 10:
            out.append((current, t))
    if kind:
        preferred = [t for k, t in out if k == kind.lower()]
        if preferred:
            return preferred + [t for k, t in out if k != kind.lower()]
    return [t for _, t in out]


_FIGURE_SUFFIXES = {".png", ".svg", ".pdf", ".jpg", ".jpeg"}
_MAX_FIGURES_LISTED = 12


class Figure(NamedTuple):
    """A figure and what it shows. ``caption`` is rayleigh's, and may be empty.

    ``origin`` says who owns the placement decision. 'results' figures belong with the
    finding they show and rayleigh captions them. 'author' figures — a model schematic, a
    conceptual diagram — belong wherever the author put them, and only the author can say
    where that is. Tracked-change deference protects an author's figure through a REVISE,
    but a regeneration reads no prior outline and would silently drop it; the manifest is
    what survives that.
    """
    path: str
    caption: str = ""
    origin: str = "results"


# The author's own illustrations, declared where the author can edit them by hand. Mirrors
# rayleigh's findings.json for figures rayleigh did not make.
AUTHOR_FIGURES_FILE = "figures.yaml"


def load_authors_block(project_dir: Path, anonymized: bool = False) -> str:
    """The authors-and-affiliations block for this project, or "".

    Authorship is project-level and lives in haarpi.yaml, above every stage — a co-author
    who joins after the one-pager circulates may trigger a re-think that regenerates the
    litreview, the build, the experiments and every document below them, and the author
    list must outlive all of it. Read at RENDER time so that list is the only place it is
    recorded; a name in prose is lost at the next major revision.

    Returns "" outside a HAARPi project: raconteur runs standalone, and a paper written by
    a tool with no manifest simply has no recorded authors. It never invents one.
    """
    try:
        from haarpi import project as hproject
    except ImportError:
        return ""
    root = hproject.find_root(project_dir)
    if root is None:
        return ""
    try:
        return hproject.authors_block(hproject.load_manifest(root), anonymized=anonymized)
    except Exception as e:  # noqa: BLE001 — a malformed manifest must not fail the render
        log(f"[warn] could not read the author list ({e}) — writing without one")
        return ""


def load_author_figures(project_dir: Path) -> list[Figure]:
    """Author-supplied illustrations from paper/figures/figures.yaml.

    Schema — a list, each entry naming a file in paper/figures/ and where it belongs:

        - path: paper/figures/model-schematic.png
          caption: A schematic of the 1D chord lattice — agents as pitch-class sets…
          section: 2.1 The Model

    ``section`` is a hint carried into the outline prompt; the outline still does the
    placing. Entries whose file is missing are dropped with a warning rather than silently,
    because a figure declared and not placed is a figure the author thinks is in the paper.
    """
    manifest = project_dir / "paper" / "figures" / AUTHOR_FIGURES_FILE
    if not manifest.is_file():
        return []
    try:
        import yaml
        data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or []
    except Exception as e:                                  # malformed YAML is the author's
        log(f"[warn] could not read {manifest.name}: {e}")   # typo, not a reason to die
        return []
    if not isinstance(data, list):
        log(f"[warn] {manifest.name} must be a list of figures — ignoring")
        return []
    figs: list[Figure] = []
    for entry in data:
        if not isinstance(entry, dict) or not entry.get("path"):
            continue
        rel = str(entry["path"]).strip()
        if not (project_dir / rel).is_file():
            log(f"[warn] {manifest.name} lists {rel} but the file does not exist — skipped")
            continue
        figs.append(Figure(rel, str(entry.get("caption", "")).strip(), origin="author"))
    if figs:
        log(f"[raconteur] {len(figs)} author illustration(s) from {manifest.name}")
    return figs


def author_figure_sections(project_dir: Path) -> dict[str, str]:
    """path → the author's requested section, for figures that name one."""
    manifest = project_dir / "paper" / "figures" / AUTHOR_FIGURES_FILE
    if not manifest.is_file():
        return {}
    try:
        import yaml
        data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or []
    except Exception:
        return {}
    if not isinstance(data, list):
        return {}
    return {str(e["path"]).strip(): str(e.get("section", "")).strip()
            for e in data
            if isinstance(e, dict) and e.get("path") and e.get("section")}


def _rayleigh_captions(project_dir: Path, subdir: str) -> dict[str, str]:
    """rayleigh's own captions, keyed by project-relative path.

    rayleigh WRITES these — naming the axes, the colour encoding, and what to look for:

      "PRIMARY: recovery landscape. Distance to Beethoven 5-1 over tolerance x radius
       (blue = closer to the phrase). Expect a low-distance settling band…"

    raconteur used to throw them away and glob the figures directory for .png files, so the
    model saw nothing but filenames and invented captions from them. It cannot mention an
    axis it has never seen.
    """
    findings = project_dir / (subdir or "results") / "findings.json"
    if not findings.is_file():
        return {}
    try:
        data = json.loads(findings.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log(f"[warn] could not read {findings.name} ({e}); figure captions unavailable")
        return {}
    out: dict[str, str] = {}
    for exp in data.get("experiments", []) or []:
        for fig in exp.get("figures", []) or []:
            path, caption = fig.get("path"), (fig.get("caption") or "").strip()
            if path and caption:
                # findings.json paths are relative to the results dir
                out[str(Path(subdir or "results") / path)] = caption
    return out


def load_figure_manifest(project_dir: Path, subdir: str = "results") -> list[Figure]:
    """rayleigh's figures, each with the caption rayleigh wrote for it.

    findings.json is AUTHORITATIVE where it exists: it says which figures carry the results
    and what each one shows. The directory holds the same plot three times over (.png, .svg,
    .eps) and a glob offers all of them — including the two formats rayleigh never described,
    so the writer could pick a captionless twin of a figure it had a perfectly good caption
    for. Fall back to globbing only when there is no manifest at all.

    Paths are project-relative ('results/figures/x.png') so pandoc can resolve them via
    --resource-path=<project_dir>.
    """
    base = project_dir / (subdir or "results")
    captions = _rayleigh_captions(project_dir, subdir)
    if captions:
        figs = [Figure(rel, cap) for rel, cap in sorted(captions.items())
                if (project_dir / rel).is_file()][:_MAX_FIGURES_LISTED]
        if figs:
            log(f"[raconteur] {len(figs)} figure(s) from rayleigh's manifest, "
                f"with the captions rayleigh wrote")
            return figs
        log("[warn] findings.json lists figures but none of the files exist — "
            "falling back to the figures directory")

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
    seen: set[str] = set()
    figs: list[Figure] = []
    for p in paths:
        if p.stem in seen:          # the same plot in another format
            continue
        seen.add(p.stem)
        figs.append(Figure(str(p.relative_to(project_dir)), ""))
        if len(figs) >= _MAX_FIGURES_LISTED:
            break
    if figs:
        log(f"[raconteur] found {len(figs)} figure(s) under {subdir}/ "
            f"(no rayleigh manifest — the writer has no description of what they show)")
    return figs


def load_onepager(project_dir: Path, short_title: str) -> str:
    """Return the approved one-pager narrative.

    A gate-minted release (paper/output/*_onepager.md) is the author-approved
    text and outranks the working chain; the newest paper/*_onepager_*ra.md is
    the fallback for projects that haven't gated the one-pager."""
    from .naming import find_latest
    from haarpi.naming import find_latest_release
    paper_dir = project_dir / "paper"
    path = find_latest_release(
        paper_dir / "output", short_title, "md", chain_includes="onepager",
    ) or find_latest(
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

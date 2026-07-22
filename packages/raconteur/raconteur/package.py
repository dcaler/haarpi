"""raconteur package: lay the approved manuscript into a venue's submission template.

The final mile. The paper stage produced an APPROVED manuscript — a release .docx with
the human's edits baked in — and a venue requires that manuscript in ITS template. This
assembles a complete, compilable submission project under ``paper/submission/<venue>/``
and, where a TeX toolchain is present, compiles it to a PDF the author reads while they
finish the venue-specific blocks in the .tex.

Two template kinds, detected from ``paper/templates/<venue>/``:

  * a Word template (.dotx/.docx) -> pandoc ``--reference-doc``: unattended, a submission
    .docx styled by the venue's own template.
  * a LaTeX class (.cls) -> attended: ONE ``submission.tex`` — the venue's
    ``\\documentclass``, the real title, the recorded authors and the approved abstract,
    then the manuscript converted to LaTeX (figures extracted from the .docx) — with the
    class + style + figures copied alongside, and compiled.

An empty slot is an honest outcome, not a failure: the approved release IS the submission.

Re-run safe: a marker line inside ``submission.tex`` separates the preamble the human
edits from the converted manuscript. Everything above it is carried forward verbatim;
everything below it is rewritten from the approved release on every run.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from .log import log
from .config import ProjectConfig
from . import guards, slate

from haarpi import naming as hnaming


def _template_dir(project_dir: Path, venue: str) -> Path:
    """A venue's submission template, inside that venue's folder.

    Venue-specific by nature — a template IS the venue's house style — so it sits with the
    rest of that venue's work rather than in a shared templates/ that then has to re-state
    the venue in a subfolder name.
    """
    return project_dir / "paper" / (venue or "") / "templates"


def _classify_template(tdir: Path) -> tuple[str, dict]:
    """What kind of template sits in the slot, and its salient files.

    Walks the slot (venues ship the template as a folder of files). A .cls makes it
    LaTeX; a .dotx/.docx that is not the manuscript makes it Word; neither is 'none'."""
    if not tdir.is_dir():
        return "none", {}
    cls = sorted(tdir.rglob("*.cls"))
    if cls:
        return "latex", {
            "cls": cls[0],
            "bst": next(iter(sorted(tdir.rglob("*.bst"))), None),
            "sty": sorted(tdir.rglob("*.sty")),
        }
    word = [p for p in sorted(tdir.rglob("*.dotx")) + sorted(tdir.rglob("*.docx"))]
    if word:
        return "word", {"ref": word[0]}
    return "none", {}


def _find_manuscript(project_dir: Path, cfg: ProjectConfig, venue: str) -> Path | None:
    """The approved manuscript release for this venue — the bare per-venue release.

    A release with the venue token but NO deliverable word (onepager/venue/outline are
    rungs, not the manuscript). Newest wins."""
    from .naming import deliverable_dir
    out = deliverable_dir(project_dir / "paper", "manuscript", venue) / "output"
    best: tuple[float, Path] | None = None
    for p in out.glob("*.docx") if out.is_dir() else []:
        parsed = hnaming.parse(p, cfg.short_title)
        if not parsed:
            continue
        _, chain, _ = parsed
        if not hnaming.is_release(chain):
            continue
        if hnaming.venue_of(p, cfg.short_title) != venue:
            continue
        if any(w in (c.lower() for c in chain) for w in hnaming.DELIVERABLE_WORDS):
            continue
        t = p.stat().st_mtime
        if best is None or t > best[0]:
            best = (t, p)
    return best[1] if best else None


def _pandoc_body(manuscript: Path, subdir: Path) -> str | None:
    """The approved .docx as a LaTeX fragment, figures and all — returned, not written.

    ``--extract-media`` pulls the images pandoc embedded at genesis back out into
    ``media/`` so ``\\includegraphics`` resolves them when the .tex compiles. Baked
    references cross over as text — the venue's .bst is a camera-ready concern the
    author handles later, not a blocker to the first compiled PDF."""
    if not shutil.which("pandoc"):
        log("[warn] pandoc not found — cannot convert the manuscript to LaTeX")
        return None
    scratch = subdir / ".body.tex"
    try:
        # The manuscript's own H1 title drops out of the fragment (we set \title in the
        # wrapper), and its ## sections promote from \subsection to \section — LNCS wants
        # top-level sections, not the demoted level a Word Heading-2 would otherwise give.
        subprocess.run(
            ["pandoc", str(manuscript), "-t", "latex", "-o", scratch.name,
             "--shift-heading-level-by=-1", "--extract-media=media"],
            cwd=subdir, check=True, capture_output=True)
        return scratch.read_text(encoding="utf-8", errors="replace")
    except subprocess.CalledProcessError as e:
        log(f"[warn] pandoc failed to convert the manuscript: {e.stderr.decode()[:200]}")
        return None
    finally:
        scratch.unlink(missing_ok=True)


_TODO = "%% TODO — replace before submission"

# ONE .tex, and it still refreshes. The submission used to be submission.tex + an \input'd
# body.tex, split so that re-running could regenerate the manuscript without clobbering the
# preamble the human edits. A single file has to keep that property some other way, so this
# line is the seam: everything above it is the human's and is carried forward verbatim;
# everything below it is the converted manuscript and is rewritten every run.
_BODY_MARK = ("%% ===== the approved manuscript, converted to LaTeX =====\n"
              "%% Regenerated on every package run. Edit ABOVE this line; edits below it\n"
              "%% are overwritten. To change the prose, change the manuscript.")

# pdflatex has no text-mode definition for a mathematical Unicode character, and one of
# them anywhere in the prose is a fatal error and no PDF. The css2026 submission died on a
# single "≥" in "large neighbour radii (≥6)" — found after a package run, on the last rung
# before the paper goes out. These are the characters scientific prose actually reaches
# for; each would otherwise have cost its own run to discover.
_UNICODE_MATH = {
    0x2265: r"\ensuremath{\geq}",   0x2264: r"\ensuremath{\leq}",
    0x2248: r"\ensuremath{\approx}", 0x00B1: r"\ensuremath{\pm}",
    0x00D7: r"\ensuremath{\times}",  0x00F7: r"\ensuremath{\div}",
    0x2212: r"\ensuremath{-}",        0x2192: r"\ensuremath{\rightarrow}",
    0x21D2: r"\ensuremath{\Rightarrow}", 0x223C: r"\ensuremath{\sim}",
    0x2260: r"\ensuremath{\neq}",    0x221E: r"\ensuremath{\infty}",
    0x00B0: r"\ensuremath{^\circ}",  0x00B7: r"\ensuremath{\cdot}",
    0x2032: r"\ensuremath{\prime}",  0x2033: r"\ensuremath{\prime\prime}",
    0x03B1: r"\ensuremath{\alpha}",  0x03B2: r"\ensuremath{\beta}",
    0x03B3: r"\ensuremath{\gamma}",  0x03B4: r"\ensuremath{\delta}",
    0x03BC: r"\ensuremath{\mu}",     0x03C3: r"\ensuremath{\sigma}",
    0x03C4: r"\ensuremath{\tau}",    0x03C9: r"\ensuremath{\omega}",
    0x0394: r"\ensuremath{\Delta}",  0x03A9: r"\ensuremath{\Omega}",
    0x2013: "--",                     0x2014: "---",
    0x2018: "`", 0x2019: "'", 0x201C: "``", 0x201D: "\'\'",
    0x2026: r"\ldots{}",             0x00A0: "~",
}

_UNICODE_PREAMBLE = "\n".join(
    ["%% Unicode the prose uses and pdflatex has no text-mode definition for.",
     "%% One of these, anywhere, is a fatal error and no PDF at all."]
    + [f"\\DeclareUnicodeCharacter{{{code:04X}}}{{{tex}}}"
       for code, tex in sorted(_UNICODE_MATH.items())])


def _latex_escape(text: str) -> str:
    """A plain string as LaTeX. For values taken from the manifest, not from prose."""
    out = []
    for ch in text:
        if ch in "&%$#_{}":
            out.append("\\" + ch)
        elif ch == "~":
            out.append(r"\textasciitilde{}")
        elif ch == "^":
            out.append(r"\textasciicircum{}")
        elif ch == "\\":
            out.append(r"\textbackslash{}")
        else:
            out.append(ch)
    return "".join(out)


def _latex_escape(value: str) -> str:
    """A plain string as LaTeX. For manifest values and abstract prose, not for markup."""
    out = []
    for ch in value:
        if ch in "&%$#_{}":
            out.append("\\" + ch)
        elif ch == "~":
            out.append("\\textasciitilde{}")
        elif ch == "^":
            out.append("\\textasciicircum{}")
        elif ch == "\\":
            out.append("\\textbackslash{}")
        else:
            out.append(ch)
    return "".join(out)


def _authors_for_latex(project_dir: Path, is_lncs: bool,
                       anonymized: bool = False) -> tuple[str, str]:
    """``(\\author, \\institute)`` from the PROJECT MANIFEST.

    Authorship is project-level data read at render time — that is the rule every other
    rung already follows. This one shipped ``Author Name`` and ``Affiliation, City,
    Country`` behind a TODO, on the last rung before the paper leaves, which is the worst
    possible place to be retyping a byline from memory.

    An anonymized venue gets a stated anonymous byline rather than a TODO: withheld is the
    correct final value there, not an unfinished one.
    """
    if anonymized:
        return "Anonymous Author(s)", "Affiliation withheld for review"
    try:
        from haarpi import project as hproject
        root = hproject.find_root(project_dir)
        people = hproject.authors(hproject.load_manifest(root)) if root else []
    except Exception as e:  # noqa: BLE001 — a manifest must never fail a package run
        log(f"[warn] could not read the author list ({e}) — leaving the byline to you")
        people = []
    if not people:
        return "", ""
    affils: list[str] = []
    for a in people:
        for aff in hproject.author_affiliations(a):
            if aff not in affils:
                affils.append(aff)
    names = []
    for a in people:
        nm = _latex_escape(a.get("name", ""))
        marks = [str(affils.index(x) + 1) for x in hproject.author_affiliations(a)]
        names.append(f"{nm}\\inst{{{','.join(marks)}}}" if is_lncs and marks else nm)
    inst = " \\and ".join(_latex_escape(a) for a in affils)
    return " \\and ".join(names), inst


def _abstract_for_latex(manuscript: Path | None) -> str:
    """The APPROVED manuscript's abstract, as LaTeX.

    The abstract is written, guarded for length and human-approved three rungs up. Asking
    for it again in a TODO invites a fourth version that no guard ever measured.
    """
    if manuscript is None or not manuscript.exists():
        return ""
    try:
        from haarpi.redline import read_release
        body = guards.abstract_body(read_release(manuscript))
    except Exception as e:  # noqa: BLE001 — same reason as the byline
        log(f"[warn] could not read the abstract ({e}) — leaving it to you")
        return ""
    body = re.sub(r"\*\*(.+?)\*\*", r"\1", body)          # the **Abstract** label's kin
    body = " ".join(body.split())
    return _latex_escape(body)


def _latex_wrapper(cfg: ProjectConfig, cls_stem: str, cls_text: str,
                   project_dir: Path | None = None, manuscript: Path | None = None,
                   anonymized: bool = False) -> str:
    """The submission.tex: the venue's class, the real title, the recorded authors and the
    approved abstract, plus \\input{body}.

    Everything here that the pipeline already holds as data is written as data. What is
    left behind a TODO is only what no rung has ever recorded — the venue's keywords.

    LNCS-aware (its \\institute / in-abstract \\keywords are proven to compile); a class
    that defines neither gets a bare title/author/abstract scaffold instead of macros it
    would choke on."""
    is_lncs = cls_stem == "llncs" or "\\institute" in cls_text
    opts = "[runningheads]" if is_lncs else ""
    title = (cfg.title or cfg.short_title or "Title").replace("{", "").replace("}", "")
    head = [f"\\documentclass{opts}{{{cls_stem}}}",
            "\\usepackage{graphicx}",
            "\\usepackage[T1]{fontenc}",
            "\\usepackage[utf8]{inputenc}",
            "", _UNICODE_PREAMBLE, "",
            "\\begin{document}",
            f"\\title{{{title}}}"]
    author, inst = _authors_for_latex(project_dir or Path("."), is_lncs, anonymized)
    fallback = "Author Name\\inst{1}" if is_lncs else "Author Name"
    head.append(f"\\author{{{author or fallback}}}"
                + ("" if author else f"  {_TODO}: authors"))
    if is_lncs:
        head.append(f"\\institute{{{inst or 'Affiliation, City, Country'}}}"
                    + ("" if inst else f"  {_TODO}: affiliations"))
    abstract = _abstract_for_latex(manuscript)
    head += ["\\maketitle", "",
             "\\begin{abstract}",
             abstract or f"Placeholder abstract. {_TODO}: the abstract."]
    if is_lncs or "\\keywords" in cls_text:
        head.append(f"\\keywords{{First \\and Second \\and Third}}  {_TODO}: keywords")
    head += ["\\end{abstract}", "", _BODY_MARK]
    return "\n".join(head)


_SECTION_RE = re.compile(r"\\(?:part|chapter|section|subsection)\*?\{")


def _body_after_front_matter(tex: str) -> str:
    """The converted manuscript from its first section on.

    A manuscript carries its own front matter — byline, affiliations, corresponding author,
    the abstract under a bold label — because it is a document a human reads. The wrapper
    sets all four as LaTeX, from the manifest and the release, so leaving them in the
    fragment too puts every one of them in the PDF twice. It always did; splitting the file
    just meant nobody read the two halves together.

    A fragment with no sectioning command at all is returned whole: an empty document is a
    worse answer than a duplicated byline."""
    m = _SECTION_RE.search(tex)
    if not m:
        return tex.strip()
    start = m.start()
    # pandoc wraps each heading as \hypertarget{id}{%\n\section{...}} — cutting between the
    # two would leave an unclosed group.
    hyper = tex.rfind("\\hypertarget{", 0, start)
    if hyper != -1 and tex[hyper:start].rstrip().endswith("{%"):
        start = hyper
    return tex[start:].strip()


# "Figure 1:" / "Fig. 1." / "Figure 1 -" at the head of a caption.
_CAPTION_LABEL_RE = re.compile(
    r"(\\caption\{)\s*(?:Figure|Fig)\.?\s*\d+\s*[.:—–-]\s*", re.IGNORECASE)


def _strip_caption_labels(tex: str) -> str:
    """Drop the manuscript's own "Figure N:" from each \\caption.

    The manuscript numbers its figures in the text because a .docx has no \\caption — the
    author has to read a number somewhere. LaTeX numbers them itself, so the two collide on
    the page as "Fig. 1. Figure 1: Chords as quarter-notes…". Worse, the typed number is
    fixed at conversion and LaTeX's is not: move a figure and they disagree.

    LaTeX only. The Word template path has no \\caption to number for it, and there the
    typed label is the only label there is."""
    return _CAPTION_LABEL_RE.sub(r"\1", tex)


def _head_of(wrapper: Path) -> str | None:
    """The human's half of an existing submission.tex — everything through the body marker.

    None if there is nothing to carry forward: no file yet (write a fresh one) or a file
    with no marker in it (leave it alone; see the caller)."""
    if not wrapper.exists():
        return None
    text = wrapper.read_text(encoding="utf-8", errors="replace")
    i = text.find(_BODY_MARK)
    return text[:i + len(_BODY_MARK)] if i != -1 else None


def _retire_split_body(subdir: Path, project_dir: Path) -> None:
    """Move a body.tex left by the two-file layout out of the way.

    Nothing inputs it any more, so it is a stale copy of the manuscript sitting beside the
    real one under an authoritative name — exactly the second source of truth this pipeline
    keeps removing. Moved, never deleted."""
    stale = subdir / "body.tex"
    if not stale.exists():
        return
    attic = subdir / "old"
    attic.mkdir(exist_ok=True)
    dest = attic / "body.tex"
    if dest.exists():
        return
    stale.rename(dest)
    log(f"[note] submission.tex is now self-contained; moved the old "
        f"{stale.relative_to(project_dir)} to {dest.relative_to(project_dir)}")


def _compile(subdir: Path, texname: str) -> Path | None:
    """Compile to PDF where a TeX toolchain exists; otherwise say so and leave the
    complete project for the author to compile elsewhere (a TeX install, or Overleaf)."""
    pdf = subdir / (Path(texname).stem + ".pdf")
    if shutil.which("latexmk"):
        cmd = ["latexmk", "-pdf", "-interaction=nonstopmode", "-halt-on-error", texname]
    elif shutil.which("pdflatex"):
        cmd = ["pdflatex", "-interaction=nonstopmode", "-halt-on-error", texname]
    else:
        log("[note] no TeX toolchain here — assembled the project but did not compile. "
            "Install TeX (or open the folder in Overleaf) to build the PDF.")
        return None
    try:
        subprocess.run(cmd, cwd=subdir, check=True, capture_output=True)
    except subprocess.CalledProcessError:
        # A converted manuscript can hit a package the human must resolve; the .tex is
        # still there to read the error and fix. Point at the log, do not raise.
        log(f"[warn] the submission did not compile cleanly — read {subdir.name}/"
            f"{Path(texname).stem}.log and fix, then re-run latexmk in that folder.")
        return pdf if pdf.exists() else None
    return pdf if pdf.exists() else None


def _package_latex(project_dir: Path, cfg: ProjectConfig, venue: str,
                   manuscript: Path, assets: dict) -> None:
    subdir = project_dir / "paper" / "submission" / venue
    subdir.mkdir(parents=True, exist_ok=True)

    # the venue's class, style and any packages — copied so the project compiles standalone
    for key in ("cls", "bst"):
        if assets.get(key):
            shutil.copy2(assets[key], subdir / assets[key].name)
    for sty in assets.get("sty", []):
        shutil.copy2(sty, subdir / sty.name)
    # the corpus bib, for the author's camera-ready citation work (not used by the compile)
    refs = project_dir / cfg.litrev_dir / "output" / "refs.bib" if cfg.litrev_dir else None
    if refs and refs.exists():
        shutil.copy2(refs, subdir / "refs.bib")

    converted = _pandoc_body(manuscript, subdir)
    if converted is None:
        raise SystemExit(1)
    body = _strip_caption_labels(_body_after_front_matter(converted))

    cls_path = assets["cls"]
    wrapper = subdir / "submission.tex"
    head = _head_of(wrapper)
    if head is None and wrapper.exists():
        # A .tex the human has restructured past recognition. Half-rewriting it is worse
        # than not refreshing it; the assets are updated and they compile when ready.
        log(f"[warn] {wrapper.relative_to(project_dir)} has no body marker — left it "
            "alone. Delete it to regenerate, or paste the manuscript in yourself.")
    else:
        fresh = head is None
        if fresh:
            spec = cfg.venue(venue)
            head = _latex_wrapper(cfg, cls_path.stem,
                                  cls_path.read_text(encoding="utf-8", errors="ignore"),
                                  project_dir=project_dir, manuscript=manuscript,
                                  anonymized=bool(getattr(spec, "anonymized", False)))
        wrapper.write_text(f"{head}\n\n{body.strip()}\n\n\\end{{document}}\n",
                           encoding="utf-8")
        log(f"[raconteur] {'wrote' if fresh else 'refreshed'} "
            f"{wrapper.relative_to(project_dir)} — one self-contained .tex"
            + (" (authors, affiliations and abstract filled from the project; "
               "add keywords)" if fresh else "; your preamble is untouched"))
        _retire_split_body(subdir, project_dir)

    pdf = _compile(subdir, "submission.tex")
    if pdf and pdf.exists():
        log(f"[raconteur] built {pdf.relative_to(project_dir)} — read it, then edit "
            f"{wrapper.relative_to(project_dir)} and re-run to rebuild.")
    else:
        log(f"[raconteur] assembled {subdir.relative_to(project_dir)} — everything is in "
            "place; compile it where TeX lives to get the PDF.")


def _package_word(project_dir: Path, cfg: ProjectConfig, venue: str,
                  manuscript: Path, assets: dict) -> None:
    subdir = project_dir / "paper" / "submission" / venue
    subdir.mkdir(parents=True, exist_ok=True)
    out = subdir / f"{manuscript.stem}_submission.docx"
    if not shutil.which("pandoc"):
        log("[warn] pandoc not found — cannot render through the Word template")
        raise SystemExit(1)
    try:
        subprocess.run(
            ["pandoc", str(manuscript), "--reference-doc", str(assets["ref"]),
             "-o", str(out)], check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        log(f"[warn] pandoc failed on the Word template: {e.stderr.decode()[:200]}")
        raise SystemExit(1)
    log(f"[raconteur] wrote {out.relative_to(project_dir)} — the approved manuscript in "
        f"{Path(assets['ref']).name}'s styling. Review and submit.")


def run(project_dir: Path, venue: str = "") -> None:
    if not ProjectConfig.exists(project_dir):
        log("[error] no paper/raconteur.yaml found — run 'raconteur init' first")
        raise SystemExit(1)
    cfg = ProjectConfig.load(project_dir)
    venue = slate.resolve(cfg, venue)
    if not venue:
        log("[error] no venue to package for — select one on the slate first")
        raise SystemExit(1)

    manuscript = _find_manuscript(project_dir, cfg, venue)
    if manuscript is None:
        log(f"[error] no approved manuscript release for {venue} yet — take the paper "
            "stage to a clean manuscript gate before packaging.")
        raise SystemExit(1)

    kind, assets = _classify_template(_template_dir(project_dir, venue))
    if kind == "latex":
        log(f"[raconteur] packaging {manuscript.name} for {venue} "
            f"(LaTeX: {assets['cls'].name})")
        _package_latex(project_dir, cfg, venue, manuscript, assets)
    elif kind == "word":
        log(f"[raconteur] packaging {manuscript.name} for {venue} "
            f"(Word template: {Path(assets['ref']).name})")
        _package_word(project_dir, cfg, venue, manuscript, assets)
    else:
        log(f"[raconteur] {venue} has no template in paper/templates/{venue}/ — the "
            f"approved release {manuscript.name} IS the submission. Nothing to package.")

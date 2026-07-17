"""raconteur package: lay the approved manuscript into a venue's submission template.

The final mile. The paper stage produced an APPROVED manuscript — a release .docx with
the human's edits baked in — and a venue requires that manuscript in ITS template. This
assembles a complete, compilable submission project under ``paper/submission/<venue>/``
and, where a TeX toolchain is present, compiles it to a PDF the author reads while they
finish the venue-specific blocks in the .tex.

Two template kinds, detected from ``paper/templates/<venue>/``:

  * a Word template (.dotx/.docx) -> pandoc ``--reference-doc``: unattended, a submission
    .docx styled by the venue's own template.
  * a LaTeX class (.cls) -> attended: the manuscript becomes a LaTeX ``body.tex`` (figures
    extracted from the .docx), wrapped in the venue's ``\\documentclass`` with placeholder
    title/author/abstract, the class + style + figures copied alongside, and compiled.

An empty slot is an honest outcome, not a failure: the approved release IS the submission.

Re-run safe: ``body.tex`` and the copied assets refresh every run, but ``submission.tex``
— the wrapper the human edits — is written once and never clobbered.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .log import log
from .config import ProjectConfig
from . import slate

from haarpi import naming as hnaming


def _template_dir(project_dir: Path, venue: str) -> Path:
    return project_dir / "paper" / "templates" / venue


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
    out = project_dir / "paper" / "output"
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


def _pandoc_body(manuscript: Path, subdir: Path) -> bool:
    """Convert the approved .docx to a LaTeX body fragment, figures and all.

    ``--extract-media`` pulls the images pandoc embedded at genesis back out into
    ``media/`` so ``\\includegraphics`` resolves them when the .tex compiles. Baked
    references cross over as text — the venue's .bst is a camera-ready concern the
    author handles later, not a blocker to the first compiled PDF."""
    if not shutil.which("pandoc"):
        log("[warn] pandoc not found — cannot convert the manuscript to LaTeX")
        return False
    try:
        # The manuscript's own H1 title drops out of the fragment (we set \title in the
        # wrapper), and its ## sections promote from \subsection to \section — LNCS wants
        # top-level sections, not the demoted level a Word Heading-2 would otherwise give.
        subprocess.run(
            ["pandoc", str(manuscript), "-t", "latex", "-o", "body.tex",
             "--shift-heading-level-by=-1", "--extract-media=media"],
            cwd=subdir, check=True, capture_output=True)
        return True
    except subprocess.CalledProcessError as e:
        log(f"[warn] pandoc failed to convert the manuscript: {e.stderr.decode()[:200]}")
        return False


_TODO = "%% TODO — replace before submission"


def _latex_wrapper(cfg: ProjectConfig, cls_stem: str, cls_text: str) -> str:
    """The submission.tex the human finishes: the venue's class, real title, and
    placeholder author/abstract that COMPILE as they stand, plus \\input{body}.

    LNCS-aware (its \\institute / in-abstract \\keywords are proven to compile); a class
    that defines neither gets a bare title/author/abstract scaffold instead of macros it
    would choke on."""
    is_lncs = cls_stem == "llncs" or "\\institute" in cls_text
    opts = "[runningheads]" if is_lncs else ""
    title = (cfg.title or cfg.short_title or "Title").replace("{", "").replace("}", "")
    head = [f"\\documentclass{opts}{{{cls_stem}}}",
            "\\usepackage{graphicx}",
            "\\usepackage[T1]{fontenc}",
            "\\begin{document}",
            f"\\title{{{title}}}"]
    if is_lncs:
        head += [f"\\author{{Author Name\\inst{{1}}}}  {_TODO}: authors",
                 f"\\institute{{Affiliation, City, Country}}  {_TODO}: affiliations"]
    else:
        head += [f"\\author{{Author Name}}  {_TODO}: authors"]
    head += ["\\maketitle", "",
             "\\begin{abstract}",
             f"Placeholder abstract. {_TODO}: the abstract."]
    if is_lncs or "\\keywords" in cls_text:
        head.append(f"\\keywords{{First \\and Second \\and Third}}  {_TODO}: keywords")
    head += ["\\end{abstract}", "",
             "%% The approved manuscript, converted to LaTeX (regenerated each package run):",
             "\\input{body}", "",
             "\\end{document}", ""]
    return "\n".join(head)


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

    if not _pandoc_body(manuscript, subdir):
        raise SystemExit(1)

    cls_path = assets["cls"]
    cls_stem = cls_path.stem
    wrapper = subdir / "submission.tex"
    if not wrapper.exists():                    # never clobber the human's edits
        wrapper.write_text(_latex_wrapper(cfg, cls_stem, cls_path.read_text(
            encoding="utf-8", errors="ignore")), encoding="utf-8")
        log(f"[raconteur] wrote {wrapper.relative_to(project_dir)} "
            "(fill in author, affiliations, abstract, keywords)")
    else:
        log(f"[raconteur] kept your {wrapper.relative_to(project_dir)}; "
            "refreshed body.tex and the copied assets")

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

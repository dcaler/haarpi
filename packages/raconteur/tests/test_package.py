"""raconteur package: the approved manuscript, laid into a venue's submission template.

The final mile. A LaTeX venue (a .cls in the template slot) gets the approved manuscript
converted to a LaTeX body, wrapped in the venue's class with placeholder author/abstract,
the class copied alongside, and — where TeX exists — compiled to a PDF the author reads
while finishing the .tex. Re-running refreshes the body but never clobbers that wrapper.
"""

from __future__ import annotations

import shutil
import subprocess
import types
from pathlib import Path

import pytest

from raconteur import package

HAVE_PANDOC = shutil.which("pandoc") is not None
HAVE_TEX = bool(shutil.which("latexmk") or shutil.which("pdflatex"))


def _cls(dirpath: Path, stem: str = "miniconf", body: str = "\\LoadClass{article}") -> None:
    dirpath.mkdir(parents=True, exist_ok=True)
    (dirpath / f"{stem}.cls").write_text(
        f"\\ProvidesClass{{{stem}}}\n{body}\n", encoding="utf-8")


class TestClassifyTemplate:
    def test_a_cls_makes_it_latex(self, tmp_path):
        _cls(tmp_path)
        kind, assets = package._classify_template(tmp_path)
        assert kind == "latex" and assets["cls"].name == "miniconf.cls"

    def test_a_docx_template_makes_it_word(self, tmp_path):
        (tmp_path / "house-style.dotx").write_bytes(b"PK\x03\x04")
        assert package._classify_template(tmp_path)[0] == "word"

    def test_a_slot_with_only_a_readme_is_none(self, tmp_path):
        (tmp_path / "README.md").write_text("fetch the template here", encoding="utf-8")
        assert package._classify_template(tmp_path)[0] == "none"

    def test_a_missing_slot_is_none(self, tmp_path):
        assert package._classify_template(tmp_path / "nope")[0] == "none"


class TestFindManuscript:
    def test_the_bare_per_venue_release_is_the_manuscript(self, tmp_path):
        out = tmp_path / "paper" / "css2026" / "manuscript" / "output"
        out.mkdir(parents=True)
        for name in ("260717_Chords_css2026.docx",          # the manuscript itself
                     "260717_Chords_css2026_outline.docx",   # a ladder rung, not it
                     "260717_Chords_jasss.docx",             # a different venue
                     "260717_Chords_css2026_ra.docx"):       # live markup, not a release
            (out / name).write_bytes(b"x")
        cfg = types.SimpleNamespace(short_title="Chords")
        got = package._find_manuscript(tmp_path, cfg, "css2026")
        assert got is not None and got.name == "260717_Chords_css2026.docx"

    def test_no_manuscript_is_none(self, tmp_path):
        (tmp_path / "paper" / "css2026" / "manuscript" / "output").mkdir(parents=True)
        cfg = types.SimpleNamespace(short_title="Chords")
        assert package._find_manuscript(tmp_path, cfg, "css2026") is None


class TestLatexWrapper:
    def test_an_lncs_class_gets_institute_and_keywords(self):
        cfg = types.SimpleNamespace(title="My Paper", short_title="X")
        tex = package._latex_wrapper(cfg, "llncs", "\\institute macro \\keywords macro")
        assert "\\documentclass[runningheads]{llncs}" in tex
        assert "\\institute" in tex and "\\keywords" in tex
        assert "My Paper" in tex and "\\input{body}" in tex

    def test_a_generic_class_omits_macros_it_would_choke_on(self):
        cfg = types.SimpleNamespace(title="P", short_title="X")
        tex = package._latex_wrapper(cfg, "miniconf", "\\LoadClass{article}")
        assert "\\documentclass{miniconf}" in tex
        assert "\\institute" not in tex and "\\keywords" not in tex


def _seed_project(tmp_path: Path) -> Path:
    (tmp_path / "paper" / "css2026" / "manuscript" / "output").mkdir(parents=True)
    (tmp_path / "litReview" / "output").mkdir(parents=True)
    (tmp_path / "litReview" / "output" / "refs.bib").write_text(
        "@misc{k, title={T}, author={A}, year={2020}}\n", encoding="utf-8")
    (tmp_path / "paper" / "raconteur.yaml").write_text(
        "short_title: Chords\ntitle: My Title\nlitrev_dir: litReview\n"
        "venues:\n  css2026:\n    name: CSS2026\n    status: selected\n", encoding="utf-8")
    md = tmp_path / "m.md"
    md.write_text("# My Title\n\n**Abstract**\n\nAbs.\n\n## Intro\n\nBody text.\n",
                  encoding="utf-8")
    subprocess.run(["pandoc", str(md), "-o",
                    str(tmp_path / "paper" / "css2026" / "manuscript" / "output" / "260717_Chords_css2026.docx")],
                   check=True, capture_output=True)
    _cls(tmp_path / "paper" / "css2026" / "templates")
    return tmp_path


@pytest.mark.skipif(not HAVE_PANDOC, reason="pandoc required")
class TestEndToEnd:
    def test_it_assembles_the_submission_project(self, tmp_path):
        _seed_project(tmp_path)
        package.run(tmp_path, venue="css2026")
        sub = tmp_path / "paper" / "submission" / "css2026"
        assert (sub / "submission.tex").exists()      # the wrapper the human edits
        assert (sub / "body.tex").exists()            # the regenerated content
        assert (sub / "miniconf.cls").exists()        # the venue class, copied
        assert (sub / "refs.bib").exists()            # the corpus bib, for camera-ready
        # sections promoted to \section (not the demoted \subsection a Heading-2 gives)
        assert "\\section{Intro}" in (sub / "body.tex").read_text()

    @pytest.mark.skipif(not HAVE_TEX, reason="TeX toolchain required")
    def test_it_compiles_a_pdf(self, tmp_path):
        _seed_project(tmp_path)
        package.run(tmp_path, venue="css2026")
        assert (tmp_path / "paper" / "submission" / "css2026" / "submission.pdf").exists()

    def test_a_second_run_refreshes_the_body_but_keeps_the_wrapper(self, tmp_path):
        _seed_project(tmp_path)
        package.run(tmp_path, venue="css2026")
        sub = tmp_path / "paper" / "submission" / "css2026"
        edited = "\\documentclass{miniconf}\n\\begin{document}MY EDIT\\input{body}\\end{document}\n"
        (sub / "submission.tex").write_text(edited, encoding="utf-8")
        (sub / "body.tex").write_text("stale", encoding="utf-8")
        package.run(tmp_path, venue="css2026")
        assert (sub / "submission.tex").read_text() == edited      # human edits preserved
        assert (sub / "body.tex").read_text() != "stale"           # content refreshed

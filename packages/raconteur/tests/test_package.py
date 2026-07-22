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
        assert "My Paper" in tex and tex.endswith(package._BODY_MARK)

    def test_a_generic_class_omits_macros_it_would_choke_on(self):
        cfg = types.SimpleNamespace(title="P", short_title="X")
        tex = package._latex_wrapper(cfg, "miniconf", "\\LoadClass{article}")
        assert "\\documentclass{miniconf}" in tex
        assert "\\institute" not in tex and "\\keywords" not in tex


class TestUnicodeSurvivesTheCompile:
    """A single "≥" in "large neighbour radii (≥6)" ended the css2026 package run with
    "Fatal error occurred, no output PDF file produced". pdflatex has no text-mode
    definition for a mathematical Unicode character, and prose written in Word is full of
    them: en dashes, curly quotes, degrees, Greek. Declaring them costs nothing; finding
    them one fatal compile at a time costs a run each, on the last rung before submission.
    """

    def test_the_character_that_killed_the_submission_is_declared(self):
        cfg = types.SimpleNamespace(title="P", short_title="X")
        tex = package._latex_wrapper(cfg, "llncs", "\\institute")
        assert "\\DeclareUnicodeCharacter{2265}{\\ensuremath{\\geq}}" in tex

    def test_the_declarations_follow_inputenc(self):
        """\\DeclareUnicodeCharacter is inputenc's macro; before the \\usepackage it is
        undefined and the preamble itself is the error."""
        tex = package._latex_wrapper(
            types.SimpleNamespace(title="P", short_title="X"), "llncs", "\\institute")
        assert tex.index("inputenc") < tex.index("\\DeclareUnicodeCharacter")

    def test_typography_word_emits_is_covered_too(self):
        tex = package._latex_wrapper(
            types.SimpleNamespace(title="P", short_title="X"), "llncs", "\\institute")
        for code in ("2013", "2014", "2019", "201C", "00B1", "00D7", "03BC"):
            assert f"\\DeclareUnicodeCharacter{{{code}}}" in tex


class TestTheWrapperIsFilledFromTheProject:
    """Author, affiliations and abstract are data the pipeline already holds — the manifest
    and the approved manuscript. Shipping them as TODO placeholders on the last rung asked
    the human to retype, from memory, a byline the skeleton and the outline stopped carrying
    precisely so it could not drift, and an abstract that a guard had already measured."""

    def _manifest(self, tmp_path: Path) -> None:
        from haarpi import project as hproject
        hproject.save_manifest(hproject.Manifest(
            name="Chords", short_title="Chords",
            authors=[{"name": "D. Cale Reeves", "affiliations": ["Alpha", "Beta"]},
                     {"name": "J. Rodenberg", "affiliations": ["Beta"]}]), tmp_path)

    def test_the_byline_comes_from_the_manifest(self, tmp_path):
        self._manifest(tmp_path)
        author, inst = package._authors_for_latex(tmp_path, is_lncs=True)
        assert author == "D. Cale Reeves\\inst{1,2} \\and J. Rodenberg\\inst{2}"
        assert inst == "Alpha \\and Beta"

    def test_a_generic_class_gets_names_without_institute_marks(self, tmp_path):
        self._manifest(tmp_path)
        author, _ = package._authors_for_latex(tmp_path, is_lncs=False)
        assert author == "D. Cale Reeves \\and J. Rodenberg"

    def test_an_anonymized_venue_names_nobody(self, tmp_path):
        """A desk reject on a rule the CFP stated plainly. Withheld is the FINAL value
        here, so it carries no TODO either."""
        self._manifest(tmp_path)
        author, inst = package._authors_for_latex(tmp_path, is_lncs=True, anonymized=True)
        assert "Reeves" not in author and "Alpha" not in inst

    def test_no_manifest_leaves_the_todo_standing(self, tmp_path):
        """raconteur runs standalone. A paper with no recorded authors gets the placeholder
        it always got — the tool never invents a byline."""
        cfg = types.SimpleNamespace(title="P", short_title="X")
        tex = package._latex_wrapper(cfg, "llncs", "\\institute", project_dir=tmp_path)
        assert "Author Name" in tex and "TODO" in tex

    def test_a_malformed_manifest_does_not_cost_the_package(self, tmp_path):
        (tmp_path / "haarpi.yaml").write_text("authors: [oh no: [\n", encoding="utf-8")
        assert package._authors_for_latex(tmp_path, is_lncs=True) == ("", "")

    def test_a_name_with_latex_syntax_in_it_is_escaped(self, tmp_path):
        from haarpi import project as hproject
        hproject.save_manifest(hproject.Manifest(
            name="C", short_title="C",
            authors=[{"name": "A & B_C", "affiliations": ["100% Lab"]}]), tmp_path)
        author, inst = package._authors_for_latex(tmp_path, is_lncs=False)
        assert author == "A \\& B\\_C" and inst == "100\\% Lab"

    def test_the_abstract_comes_from_the_approved_manuscript(self, tmp_path):
        md = tmp_path / "m.md"
        md.write_text("# T\n\n**Abstract**\n\nThe approved words.\n\n## 1. Intro\n\nBody.\n",
                      encoding="utf-8")
        assert package._abstract_for_latex(md) == "The approved words."

    def test_the_abstract_stops_at_the_first_section(self, tmp_path):
        md = tmp_path / "m.md"
        md.write_text("# T\n\n**Abstract**\n\nAbs.\n\n## 1. Intro\n\nNot the abstract.\n",
                      encoding="utf-8")
        assert "Not the abstract" not in package._abstract_for_latex(md)

    def test_no_manuscript_leaves_the_placeholder(self, tmp_path):
        assert package._abstract_for_latex(tmp_path / "nope.docx") == ""
        assert package._abstract_for_latex(None) == ""

    def test_keywords_remain_the_one_honest_todo(self, tmp_path):
        """Nothing in the pipeline records them, so nothing can fill them."""
        self._manifest(tmp_path)
        md = tmp_path / "m.md"
        md.write_text("# T\n\n**Abstract**\n\nAbs.\n", encoding="utf-8")
        tex = package._latex_wrapper(
            types.SimpleNamespace(title="P", short_title="X"), "llncs", "\\institute",
            project_dir=tmp_path, manuscript=md)
        assert tex.count(package._TODO) == 1
        assert "keywords" in tex.split(package._TODO)[1]


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
    def test_it_assembles_one_self_contained_tex(self, tmp_path):
        _seed_project(tmp_path)
        package.run(tmp_path, venue="css2026")
        sub = tmp_path / "paper" / "submission" / "css2026"
        assert (sub / "miniconf.cls").exists()        # the venue class, copied
        assert (sub / "refs.bib").exists()            # the corpus bib, for camera-ready
        assert not (sub / "body.tex").exists()        # one document, not two
        tex = (sub / "submission.tex").read_text()
        # sections promoted to \section (not the demoted \subsection a Heading-2 gives)
        assert "\\section{Intro}" in tex
        assert "\\input{body}" not in tex
        assert tex.rstrip().endswith("\\end{document}")

    @pytest.mark.skipif(not HAVE_TEX, reason="TeX toolchain required")
    def test_it_compiles_a_pdf(self, tmp_path):
        _seed_project(tmp_path)
        package.run(tmp_path, venue="css2026")
        assert (tmp_path / "paper" / "submission" / "css2026" / "submission.pdf").exists()

    def test_a_second_run_refreshes_the_body_and_keeps_the_preamble(self, tmp_path):
        """The property the two-file layout existed for, kept in one file: the marker line
        is the seam. Above it is the human's and survives; below it is the manuscript and
        is rewritten from the approved release."""
        _seed_project(tmp_path)
        package.run(tmp_path, venue="css2026")
        tex_path = tmp_path / "paper" / "submission" / "css2026" / "submission.tex"
        head, mark, _ = tex_path.read_text().partition(package._BODY_MARK)
        edited = head.replace("\\begin{document}", "\\usepackage{amsmath}\n\\begin{document}")
        tex_path.write_text(edited + mark + "\nSTALE BODY\n\\end{document}\n",
                            encoding="utf-8")
        package.run(tmp_path, venue="css2026")
        after = tex_path.read_text()
        assert "\\usepackage{amsmath}" in after          # the preamble edit survived
        assert "STALE BODY" not in after                 # the manuscript was rewritten
        assert "\\section{Intro}" in after

    def test_a_tex_with_no_marker_is_left_alone(self, tmp_path):
        """A .tex restructured past recognition. Half-rewriting someone's document is worse
        than not refreshing it."""
        _seed_project(tmp_path)
        package.run(tmp_path, venue="css2026")
        tex_path = tmp_path / "paper" / "submission" / "css2026" / "submission.tex"
        mine = "\\documentclass{miniconf}\n\\begin{document}ALL MINE\\end{document}\n"
        tex_path.write_text(mine, encoding="utf-8")
        package.run(tmp_path, venue="css2026")
        assert tex_path.read_text() == mine

    def test_the_front_matter_does_not_appear_twice(self, tmp_path):
        """The manuscript carries a byline and an abstract of its own — it is a document a
        human reads. The wrapper sets both as LaTeX from the manifest and the release, so
        the converted fragment must start at its first section. It always should have; two
        files just meant nobody read the halves together, and every submission PDF this
        pipeline built carried its abstract twice."""
        _seed_project(tmp_path)
        package.run(tmp_path, venue="css2026")
        tex = (tmp_path / "paper" / "submission" / "css2026" / "submission.tex").read_text()
        body = tex.split(package._BODY_MARK)[1]
        assert "\\section{Intro}" in body
        assert "Abs." not in body and "\\textbf{Abstract}" not in body
        assert tex.count("Abs.") == 1                 # only the \begin{abstract} copy

    def test_captions_are_not_numbered_twice(self):
        """LNCS prints "Fig. 1." itself, so the manuscript's own label lands beside it as
        "Fig. 1. Figure 1: Chords as…". The .docx had to carry a typed number — there is no
        \\caption in Word — but a typed number is also fixed at conversion while LaTeX's is
        not, so moving a figure makes the two disagree."""
        for label in ("Figure 1: ", "Fig. 2. ", "figure 10 - ", "Figure 3 — "):
            out = package._strip_caption_labels("\\caption{" + label + "Chords on a lattice}")
            assert out == "\\caption{Chords on a lattice}", label

    def test_a_caption_that_merely_mentions_a_figure_is_untouched(self):
        tex = "\\caption{Compared with Figure 2, the band is narrower}"
        assert package._strip_caption_labels(tex) == tex

    def test_a_fragment_with_no_sections_is_kept_whole(self):
        """An empty document is a worse answer than a duplicated byline."""
        assert package._body_after_front_matter("Just prose.\n") == "Just prose."

    def test_the_hypertarget_group_is_not_cut_in_half(self):
        """pandoc wraps each heading as \\hypertarget{id}{%\\n\\section{...}}; cutting
        between the two leaves an unclosed group and a compile that fails on a brace."""
        frag = "Byline.\n\n\\hypertarget{intro}{%\n\\section{Intro}\\label{intro}}\n\nText.\n"
        out = package._body_after_front_matter(frag)
        assert out.startswith("\\hypertarget{intro}{%") and "Byline" not in out

    def test_a_body_tex_from_the_old_layout_is_moved_not_deleted(self, tmp_path):
        _seed_project(tmp_path)
        package.run(tmp_path, venue="css2026")
        sub = tmp_path / "paper" / "submission" / "css2026"
        (sub / "body.tex").write_text("the old split", encoding="utf-8")
        package.run(tmp_path, venue="css2026")
        assert not (sub / "body.tex").exists()
        assert (sub / "old" / "body.tex").read_text() == "the old split"

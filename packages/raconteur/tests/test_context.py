"""Upstream-output discovery: the loaders must find the purpose-built writeups."""

from pathlib import Path

from raconteur.context import (
    find_methods_file, find_results_file, load_methods, load_results,
)


def _mk(p: Path, text: str = "x") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


# ── methods writeup ───────────────────────────────────────────────────────────

def test_methods_matches_the_project_stem_form(tmp_path):
    # raster's real output carries the project stem between date and infix
    f = _mk(tmp_path / "260707_schellingchords_methods_ra.md", "methods body")
    assert find_methods_file(tmp_path) == f
    assert load_methods(tmp_path) == "methods body"


def test_methods_matches_the_bare_form_too(tmp_path):
    f = _mk(tmp_path / "260707_methods_ra.md")
    assert find_methods_file(tmp_path) == f


def test_methods_prefers_the_newest_datestamp(tmp_path):
    _mk(tmp_path / "260701_proj_methods_ra.md")
    newer = _mk(tmp_path / "260707_proj_methods_ra_DCR.md")
    assert find_methods_file(tmp_path) == newer


def test_methods_prefers_code_output_over_legacy_root(tmp_path):
    # raster handoff now writes to code/output/; legacy root copies still count
    # but only when no stage-dir copy exists
    _mk(tmp_path / "260707_proj_methods_ra.md", "legacy root")
    staged = _mk(tmp_path / "code" / "output" / "260701_proj_methods_ra.md", "staged")
    assert find_methods_file(tmp_path) == staged
    assert load_methods(tmp_path) == "staged"


# ── results digest ────────────────────────────────────────────────────────────

def test_results_prefers_the_chained_digest(tmp_path):
    res = tmp_path / "results"
    _mk(res / "RESULTS.md", "working writeup")
    digest = _mk(res / "260704_proj_results_ra.md", "chained digest")
    assert find_results_file(res) == digest
    assert load_results(tmp_path) == "chained digest"


def test_results_release_form_qualifies(tmp_path):
    # a minted release's .md sibling has no initials chain at all
    res = tmp_path / "results"
    rel = _mk(res / "260704_proj_results.md", "release digest")
    assert find_results_file(res) == rel


def test_results_falls_back_to_working_writeup(tmp_path):
    res = tmp_path / "results"
    _mk(res / "RESULTS.md", "working writeup")
    _mk(res / "data" / "run1.csv", "a,b\n1,2")
    assert load_results(tmp_path) == "working writeup"


def test_results_crawls_when_no_digest_exists(tmp_path):
    res = tmp_path / "results"
    _mk(res / "findings.json", '{"k": 1}')
    out = load_results(tmp_path)
    assert "findings.json" in out and '"k": 1' in out


def test_figure_manifest_skips_nas_litter(tmp_path):
    from raconteur.context import load_figure_manifest
    res = tmp_path / "results"
    real = _mk(res / "figures" / "E1_0_recovery.png", "png")
    _mk(res / "figures" / "@eaDir" / "E1_0_recovery.png" / "SYNOFILE_THUMB_M.png")
    _mk(res / "figures" / ".hidden" / "x.png")
    figs = load_figure_manifest(tmp_path)
    assert [f.path for f in figs] == [str(real.relative_to(tmp_path))]


def test_figure_manifest_prefers_rayleighs_captions(tmp_path):
    """rayleigh names the axes and the colour encoding. raconteur used to glob for .png
    files and hand the writer nothing but filenames — so it invented the axes."""
    import json

    from raconteur.context import load_figure_manifest
    res = tmp_path / "results"
    _mk(res / "figures" / "E1_0_recovery.png", "png")
    _mk(res / "figures" / "E1_0_recovery.svg", "svg")   # the same plot, no caption
    (res / "findings.json").write_text(json.dumps({
        "experiments": [{"figures": [{
            "path": "figures/E1_0_recovery.png",
            "caption": "Distance to the target over tolerance x radius (blue = closer).",
        }]}]
    }), encoding="utf-8")

    figs = load_figure_manifest(tmp_path)
    assert [f.path for f in figs] == ["results/figures/E1_0_recovery.png"]
    assert "blue = closer" in figs[0].caption, "the writer must see what the figure shows"


def test_a_captionless_twin_is_never_offered(tmp_path):
    """The same plot exists as .png, .svg and .eps. Offering all three lets the writer pick
    a format rayleigh never described."""
    from raconteur.context import load_figure_manifest
    res = tmp_path / "results"
    for ext in ("png", "svg", "eps"):
        _mk(res / "figures" / f"E1_0_recovery.{ext}", ext)
    figs = load_figure_manifest(tmp_path)          # no findings.json → glob fallback
    assert len(figs) == 1


def test_results_crawl_keeps_an_oversize_first_file(tmp_path):
    # a sole candidate bigger than the whole budget must yield truncated
    # content, not an empty context (the old break-before-append lost it)
    res = tmp_path / "results"
    _mk(res / "big.txt", "wide " * 2000)  # one 10k-char line, over the 4k budget
    out = load_results(tmp_path)
    assert "big.txt" in out and "[truncated]" in out
    assert len(out) <= 4200  # budget respected (plus the truncation marker)

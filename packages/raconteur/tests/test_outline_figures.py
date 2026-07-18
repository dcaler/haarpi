"""An outline that never names a figure leaves the draft to guess where they go.

rayleigh's figures (path + caption) are carried into the structural analysis as
``key_figures``, so every outline pass — draft, critique, revise, refresh — sees which
figures exist and can place each in the Results subsection it shows. Pins the
SchellingChords miss: the figures were in the results digest, but the outline dropped
them because nothing told it to place them and the analysis never mentioned them.
"""

from __future__ import annotations

import json
import types

from raconteur import outline
from raconteur.context import Figure

FIGS = [
    Figure("results/figures/E1_0_recovery-landscape.png", "PRIMARY: recovery landscape."),
    Figure("results/figures/E2_0_the-fair-fight.png", "The fair fight: Schelling vs monkey."),
]


def _brain(analysis_json: str = '{"contribution": "x"}'):
    return types.SimpleNamespace(coordinator=lambda *a, **k: analysis_json)


def test_figures_are_carried_into_the_analysis_as_key_figures():
    out = outline._analyze_structure(_brain(), "desc", litrev="", code="", results="",
                                     figures=FIGS)
    # the analysis string ends in the parsed JSON — key_figures must be in it, exactly
    parsed = json.loads(out.split("\n\n", 1)[1])
    assert parsed["key_figures"] == [
        {"path": "results/figures/E1_0_recovery-landscape.png",
         "caption": "PRIMARY: recovery landscape.", "origin": "results"},
        {"path": "results/figures/E2_0_the-fair-fight.png",
         "caption": "The fair fight: Schelling vs monkey.", "origin": "results"},
    ]


def test_an_author_illustration_carries_its_origin_and_section(tmp_path):
    """The placement rule branches on origin — a results figure follows its finding, an
    author illustration stays in the section the author named. Without these keys in
    key_figures the rule has nothing to branch on and a schematic lands in Results."""
    (tmp_path / "paper" / "figures").mkdir(parents=True)
    (tmp_path / "illustrations").mkdir()
    (tmp_path / "illustrations" / "s.png").write_bytes(b"x")
    (tmp_path / "paper" / "figures" / "figures.yaml").write_text(
        "- path: illustrations/s.png\n"
        "  caption: A schematic of the lattice and its move rule.\n"
        "  section: 2.1 The Model\n")
    from raconteur.context import load_author_figures
    figs = load_author_figures(tmp_path)
    out = outline._analyze_structure(_brain(), "desc", litrev="", code="", results="",
                                     figures=figs, project_dir=tmp_path)
    kf = json.loads(out.split("\n\n", 1)[1])["key_figures"]
    assert kf == [{"path": "illustrations/s.png",
                   "caption": "A schematic of the lattice and its move rule.",
                   "origin": "author", "section": "2.1 The Model"}]


def test_no_figures_means_no_key_figures_key():
    out = outline._analyze_structure(_brain(), "desc", litrev="", code="", results="",
                                     figures=[])
    parsed = json.loads(out.split("\n\n", 1)[1])
    assert "key_figures" not in parsed


def test_the_draft_prompt_instructs_figure_placement():
    # the rule and the exact line form must be in the prompt the model actually sees
    assert "key_figures" in outline._DRAFT_PROMPT
    assert "Figure N:" in outline._DRAFT_PROMPT
    # numbered from 1 in order — the paper stage has no numbering rule of its own, so an
    # unnumbered outline yields captions no prose can refer to ("Figure 3 shows …")
    assert "numbered from 1" in outline._DRAFT_PROMPT
    # results figures follow their finding; author illustrations stay where the author put
    # them. A model schematic dragged into Results is the defect this guards against.
    assert "origin" in outline._DRAFT_PROMPT
    assert "never move it into Results" in outline._DRAFT_PROMPT


def test_the_critique_enforces_figure_placement():
    assert "key_figures" in outline._CRITIQUE_PROMPT


def test_the_draft_prompt_does_not_resend_raw_code_or_results():
    # The raw methods writeup and results digest are distilled into the analysis; re-sending
    # them overran num_ctx and, because the analysis sits at the top, it was the analysis
    # (and its figure paths) Ollama discarded. The outline plans from the distilled analysis.
    assert "{code_section}" not in outline._DRAFT_PROMPT
    assert "{results_section}" not in outline._DRAFT_PROMPT
    # the grounding rules must point at the analysis's distilled keys, not a raw dump
    assert "key_findings" in outline._DRAFT_PROMPT and "key_equations" in outline._DRAFT_PROMPT

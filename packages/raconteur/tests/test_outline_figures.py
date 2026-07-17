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
         "caption": "PRIMARY: recovery landscape."},
        {"path": "results/figures/E2_0_the-fair-fight.png",
         "caption": "The fair fight: Schelling vs monkey."},
    ]


def test_no_figures_means_no_key_figures_key():
    out = outline._analyze_structure(_brain(), "desc", litrev="", code="", results="",
                                     figures=[])
    parsed = json.loads(out.split("\n\n", 1)[1])
    assert "key_figures" not in parsed


def test_the_draft_prompt_instructs_figure_placement():
    # the rule and the exact bullet form must be in the prompt the model actually sees
    assert "key_figures" in outline._DRAFT_PROMPT
    assert "- Figure:" in outline._DRAFT_PROMPT


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

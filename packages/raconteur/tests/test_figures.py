"""A figure owes its reader three things, and all three are checkable.

  * a NUMBER, so the prose can point at it;
  * an INTRODUCTION in the prose — "Figure 1 shows …" — or the reader meets an image with
    no idea why it is there;
  * a caption they can read the plot BY: the axes, the encoding, what to look for.

The delivered one-pager had none of them. Its caption — "Recovery landscape showing optimal
distance at moderate tolerance and high radius" — names no axis and no colour, because the
writer had never seen either: it was handed a list of .png filenames.
"""

from __future__ import annotations

import pytest

from raconteur import guards

GOOD_CAPTION = ("Figure 1: distance to Beethoven 5-1 over tolerance x radius; blue is "
                "closer to the target phrase, with a settling band at moderate tolerance")


def _kinds(text):
    return {f.kind for f in guards.figure_findings(text)}


def test_prose_with_no_figure_is_not_a_figure_problem():
    assert guards.figure_findings("Just prose, no figures here.") == []


def test_a_well_formed_figure_passes():
    text = f"Figure 1 shows the recovery landscape.\n\n![{GOOD_CAPTION}](results/f/a.png)"
    assert guards.figure_findings(text) == []


def test_an_unnumbered_caption_is_caught():
    text = "Figure 1 shows it.\n\n![Recovery landscape showing optimal distance](f/a.png)"
    assert "unnumbered-figure" in _kinds(text)


def test_a_figure_the_prose_never_introduces_is_caught():
    """The one the reviewer actually asked for."""
    text = f"The simulation reveals a landscape.\n\n![{GOOD_CAPTION}](results/f/a.png)"
    assert "unintroduced-figure" in _kinds(text)


def test_a_thin_caption_is_caught():
    text = "Figure 1 shows it.\n\n![Figure 1: recovery landscape](f/a.png)"
    assert "thin-caption" in _kinds(text)


def test_figures_must_be_numbered_in_order():
    text = (f"Figure 1 shows one. Figure 2 shows the other.\n\n"
            f"![{GOOD_CAPTION}](f/a.png)\n\n"
            f"![Figure 3: the fair fight, Schelling recovery minus the time-matched blind "
            f"monkey baseline, positive only in the settling band](f/b.png)")
    assert "misnumbered-figure" in _kinds(text)


@pytest.mark.parametrize("intro", [
    "Figure 1 shows the landscape.",
    "The landscape settles (Fig. 1).",
    "As figure 1 makes plain, the band is narrow.",
])
def test_the_introduction_may_be_phrased_naturally(intro):
    assert "unintroduced-figure" not in _kinds(f"{intro}\n\n![{GOOD_CAPTION}](f/a.png)")

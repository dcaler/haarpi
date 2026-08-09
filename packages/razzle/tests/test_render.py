"""razzle.render — the python-pptx render core.

A deck spec + the neutral house master + a figure → a .pptx: layouts cloned by role, text
placeholders filled, the figure placed as a picture in the content area, speaker notes set. Logos
are wired but skipped when absent. The real master/logos live neutral (~/.config/haarpi/razzle/) and
never in the repo, so the render test is skip-guarded on their presence.
"""

from __future__ import annotations

import shutil

import pytest

from razzle import assets, render

_HAS_DOT = shutil.which("dot") is not None
_MASTER = assets.master_pptx("default")
_DESC = assets.descriptor("default")


def test_example_descriptor_documents_the_format():
    import yaml
    from importlib.resources import files
    d = yaml.safe_load((files("razzle") / "layouts" / "example.yaml").read_text())
    assert d["size"] == "16:9"
    assert d["roles"]["figure"]["picture"] == {"figure": 1}
    assert d["roles"]["title"]["logos"] == [14]


def test_logos_for_is_a_list_from_the_registries():
    got = assets.logos_for(affiliations=["Nowhere University"], funders=[])
    assert isinstance(got, list)                       # unmatched → skipped, never a crash


@pytest.mark.skipif(not (_MASTER and _DESC and _HAS_DOT),
                    reason="neutral house master or graphviz absent")
def test_render_deck_against_the_house_master(tmp_path):
    from haarpi import figure, project
    figure.write_figure(tmp_path, "demo", figure.stage_dag(project.DEFAULT_STAGES))
    png = figure.resolve(tmp_path, "demo", "stageLadder", "png", width=1200)

    spec = [
        {"role": "title", "title": "A research talk", "subtitle": "the through-line"},
        {"role": "figure", "title": "The pipeline", "figure": "ladder",
         "citation": "[Ref 1]", "notes": "walk the ladder"},
        {"role": "content", "title": "Takeaways", "body": ["it composes", "it's grounded"]},
    ]
    out = render.render_deck(spec, _DESC["master_path"], _DESC, tmp_path / "deck.pptx",
                             figures={"ladder": png})

    from pptx import Presentation
    prs = Presentation(str(out))
    assert len(prs.slides) == 3
    assert any("A research talk" in (sh.text_frame.text if sh.has_text_frame else "")
               for sh in prs.slides[0].shapes)
    assert any(sh.shape_type == 13 for sh in prs.slides[1].shapes)     # 13 == PICTURE
    assert "walk the ladder" in prs.slides[1].notes_slide.notes_text_frame.text

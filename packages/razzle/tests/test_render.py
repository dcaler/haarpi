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
    # `split` puts a point beside its evidence. Its indices are NOT layout 1's: idx13 is the
    # right-hand figure box and idx14 the caption, the reverse of the figure role above.
    split = d["roles"]["split"]
    assert split["layout"] == 2
    assert split["picture"] == {"figure": 13}
    assert split["text"] == {"title": 0, "body": 1, "citation": 14}


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
        {"role": "figure", "title": "The pipeline", "figure": "ladder", "citation": "[Ref 1]"},
        {"role": "split", "title": "The point, beside its evidence", "figure": "ladder",
         "body": ["it composes", "it's grounded"], "citation": "[Ref 2]"},
        {"role": "content", "title": "Takeaways", "body": ["it composes", "it's grounded"]},
    ]
    out = render.render_deck(spec, _DESC["master_path"], _DESC, tmp_path / "deck.pptx",
                             figures={"ladder": png})

    from pptx import Presentation
    prs = Presentation(str(out))
    assert len(prs.slides) == 4
    assert any("A research talk" in (sh.text_frame.text if sh.has_text_frame else "")
               for sh in prs.slides[0].shapes)
    assert any(sh.shape_type == 13 for sh in prs.slides[1].shapes)     # 13 == PICTURE
    # the split slide carries BOTH its bullets and its figure — the whole reason the role exists
    texts = " ".join(sh.text_frame.text for sh in prs.slides[2].shapes if sh.has_text_frame)
    assert "it composes" in texts and "[Ref 2]" in texts
    assert any(sh.shape_type == 13 for sh in prs.slides[2].shapes)


def test_a_deck_carries_no_speaker_notes(tmp_path):
    """Notes are where the essay goes when the slide refuses it. A `notes` key on a slide must not
    reach the .pptx even if something upstream puts one there."""
    spec = [{"role": "content", "title": "T", "body": ["a"], "notes": "an essay"}]
    out = render.render_deck(spec, _DESC["master_path"], _DESC, tmp_path / "d.pptx")

    from pptx import Presentation
    prs = Presentation(str(out))
    assert not prs.slides[0].has_notes_slide

"""razzle.render — the python-pptx render core.

A deck spec + the neutral house master + a figure → a .pptx: layouts cloned by role, text
placeholders filled, the figure placed as a picture in the content area. Logos are wired but skipped
when absent. The real master/logos live neutral (~/.config/haarpi/razzle/) and
never in the repo, so the render test is skip-guarded on their presence.
"""

from __future__ import annotations

import importlib.util
import shutil

import pytest

from razzle import assets, render

_HAS_DOT = shutil.which("dot") is not None
# a figure reaches a slide as a PNG, so a rasteriser is as much a prerequisite as graphviz — without
# one `figure.resolve(..., "png")` returns None and the picture assertion below fails for a reason
# that has nothing to do with razzle
_HAS_RASTER = importlib.util.find_spec("cairosvg") is not None or shutil.which("rsvg-convert")
_MASTER = assets.master_pptx("default")
_DESC = assets.descriptor("default")


def test_example_descriptor_documents_the_format():
    import yaml
    from importlib.resources import files
    d = yaml.safe_load((files("razzle") / "layouts" / "example.yaml").read_text())
    assert d["size"] == "16:9"
    assert d["roles"]["figure"]["picture"] == {"figure": 1}
    # idx14 on the title layout is the VENUE/DATE line, not a logo strip. The title slide carries
    # NO marks at all: the logos get their own explicit box on the acknowledgements slide, because
    # a placeholder holds exactly one picture and there may be several marks.
    assert d["roles"]["title"]["text"]["venue"] == 14
    assert "logos" not in d["roles"]["title"] and "logo_strip" not in d["roles"]["title"]
    ack = d["roles"]["acknowledgements"]
    assert set(ack["logo_strip"]) >= {"left", "top", "width", "height"}
    assert {"footer", "contact"} <= set(ack["text"])
    # the running furniture is mapped on every non-title role, so it is filled instead of stripped
    for role in ("figure", "content", "split"):
        assert {"footer", "contact"} <= set(d["roles"][role]["text"])
    # `split` puts a point beside its evidence. Its indices are NOT layout 1's: idx13 is the
    # right-hand figure box and idx14 the caption, the reverse of the figure role above.
    split = d["roles"]["split"]
    assert split["layout"] == 2
    assert split["picture"] == {"figure": 13}
    assert split["text"] == {"title": 0, "body": 1, "citation": 14, "footer": 15, "contact": 16}


def test_logos_for_is_a_list_from_the_registries():
    got = assets.logos_for(affiliations=["Nowhere University"], funders=[])
    assert isinstance(got, list)                       # unmatched → skipped, never a crash


@pytest.mark.skipif(not (_MASTER and _DESC and _HAS_DOT and _HAS_RASTER),
                    reason="neutral house master, graphviz or SVG→PNG rasteriser absent")
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


# ------------------------------------------------------- the master's running furniture
def _texts(slide):
    return " | ".join(sh.text_frame.text for sh in slide.shapes if sh.has_text_frame)


@pytest.mark.skipif(not (_MASTER and _DESC), reason="neutral house master absent")
def test_deck_level_furniture_fills_the_strips_instead_of_being_stripped(tmp_path):
    """venue/footer/contact are the same on every slide, so they are NOT in the spec — they come
    from `furniture`. Before this they were mapped nowhere and _strip_unused deleted the master's
    footer and contact strips off every slide."""
    spec = [{"role": "title", "title": "A talk", "subtitle": "Ada, Bo"},
            {"role": "content", "title": "Points", "body": ["a"]}]
    out = render.render_deck(spec, _DESC["master_path"], _DESC, tmp_path / "d.pptx",
                             furniture={"venue": "CSS2026 · 31 Oct", "footer": "CSS2026 | A talk",
                                        "contact": "ada@ucb.edu"})
    from pptx import Presentation
    prs = Presentation(str(out))
    assert "CSS2026 · 31 Oct" in _texts(prs.slides[0])          # the title slide's venue/date line
    assert "CSS2026 | A talk" in _texts(prs.slides[1])          # the running footer
    assert "ada@ucb.edu" in _texts(prs.slides[1])               # the running contact


@pytest.mark.skipif(not (_MASTER and _DESC), reason="neutral house master absent")
def test_a_slide_value_beats_the_deck_furniture(tmp_path):
    spec = [{"role": "content", "title": "T", "body": ["a"], "footer": "this slide only"}]
    out = render.render_deck(spec, _DESC["master_path"], _DESC, tmp_path / "d.pptx",
                             furniture={"footer": "the deck's"})
    from pptx import Presentation
    assert "this slide only" in _texts(Presentation(str(out)).slides[0])


@pytest.mark.skipif(not (_MASTER and _DESC), reason="neutral house master absent")
def test_every_slide_after_the_opening_one_gets_the_masters_slide_number(tmp_path):
    """python-pptx never CLONES a slide-number placeholder (it is latent), so it has to be added.
    The clone carries the master's <a:fld type="slidenum">, which numbers live rather than baking
    a string that would go wrong the moment slides are reordered."""
    spec = [{"role": "content", "title": str(i), "body": ["x"]} for i in range(3)]
    out = render.render_deck(spec, _DESC["master_path"], _DESC, tmp_path / "d.pptx")
    from pptx import Presentation
    from pptx.oxml.ns import qn
    for slide in list(Presentation(str(out)).slides)[1:]:
        flds = slide.shapes._spTree.findall(f".//{qn('a:fld')}")
        assert any(f.get("type") == "slidenum" for f in flds)


@pytest.mark.skipif(not (_MASTER and _DESC), reason="neutral house master absent")
def test_only_the_opening_slide_is_unnumbered_even_when_a_later_slide_is_a_title(tmp_path):
    """A title page carries no page number — the OPENING one, by position.

    Keyed on the role instead, a closing "thank you" slide the composer had also written as a
    title dropped out of the numbering: a live deck ran 11, 12, blank, 14."""
    spec = [{"role": "title", "title": "A talk", "subtitle": "Ada"},
            {"role": "content", "title": "Points", "body": ["a"]},
            {"role": "title", "title": "Thanks", "subtitle": "Thank you"}]
    out = render.render_deck(spec, _DESC["master_path"], _DESC, tmp_path / "d.pptx")
    from pptx import Presentation
    from pptx.oxml.ns import qn
    prs = Presentation(str(out))

    def _numbered(slide):
        return any(f.get("type") == "slidenum"
                   for f in slide.shapes._spTree.findall(f".//{qn('a:fld')}"))

    assert not _numbered(prs.slides[0])
    assert _numbered(prs.slides[1])
    assert _numbered(prs.slides[2])


@pytest.mark.skipif(not (_MASTER and _DESC), reason="neutral house master absent")
def test_the_slide_number_clone_gets_a_fresh_shape_id(tmp_path):
    """The clone arrives carrying the LAYOUT's shape id, which collides with a shape already on the
    slide — a split slide ended up with two shapes numbered 7. Ids must be unique per spTree."""
    spec = [{"role": "split", "title": "T", "body": ["a"], "citation": "[R]"},
            {"role": "content", "title": "U", "body": ["b"]}]
    out = render.render_deck(spec, _DESC["master_path"], _DESC, tmp_path / "d.pptx")
    from pptx import Presentation
    from pptx.oxml.ns import qn
    for slide in Presentation(str(out)).slides:
        ids = [e.get("id") for e in slide.shapes._spTree.iter(qn("p:cNvPr"))]
        assert len(ids) == len(set(ids)), ids


@pytest.mark.skipif(not (_MASTER and _DESC), reason="neutral house master absent")
def _strip(path):
    """(placeholder-ish view of) the footer and contact on the first rendered slide."""
    from pptx import Presentation
    out = {}
    for sh in Presentation(str(path)).slides[0].shapes:
        if not sh.has_text_frame:
            continue
        t = sh.text_frame.text
        if "@" in t:
            out["contact"] = (sh, t)
        elif "|" in t:
            out["footer"] = (sh, t)
    return out


def _pt(sh):
    sz = sh.text_frame.paragraphs[0].font.size
    return sz.pt if sz else None


@pytest.mark.skipif(not (_MASTER and _DESC), reason="neutral house master absent")
def test_the_footer_and_the_address_are_rendered_at_one_size(tmp_path):
    """They are one line, read as one line, so they get one size.

    Sized separately against an ASSUMED 12pt master, a 44-character address came out at 9.8pt
    beside a 22pt footer — the complaint was that it read as a smudge, and it did.
    """
    out = render.render_deck([{"role": "content", "title": "T", "body": ["a"]}],
                             _DESC["master_path"], _DESC, tmp_path / "d.pptx",
                             furniture={"contact": "d.cale.reeves@computationalsocialscience.org",
                                        "footer": "CSS2026 | Sense of Schelling"})
    st = _strip(out)
    assert _pt(st["contact"][0]) == _pt(st["footer"][0])
    assert _pt(st["contact"][0]) >= 18            # the master's own size, not a shrunken stand-in


@pytest.mark.skipif(not (_MASTER and _DESC), reason="neutral house master absent")
def test_the_address_takes_the_room_it_needs_from_the_footers_slack(tmp_path):
    """The boxes move before the type does: the contact keeps its right edge and grows left into a
    footer that is nowhere near its 9.5 inches."""
    long_ = "d.cale.reeves@computationalsocialscience.org"
    out = render.render_deck([{"role": "content", "title": "T", "body": ["a"]}],
                             _DESC["master_path"], _DESC, tmp_path / "d.pptx",
                             furniture={"contact": long_, "footer": "CSS2026 | Sense of Schelling"})
    st = _strip(out)
    contact, footer = st["contact"][0], st["footer"][0]
    size = _pt(contact)
    inner = (contact.width - contact.text_frame.margin_left
             - contact.text_frame.margin_right) / 12700
    assert inner >= render._need_pt(long_, size)          # it fits on ONE line
    assert contact.left > footer.left + footer.width      # and it does not sit on the footer


@pytest.mark.skipif(not (_MASTER and _DESC), reason="neutral house master absent")
def test_a_strip_that_cannot_fit_shrinks_together_not_one_half_of_it(tmp_path):
    """A footer carrying a full paper title leaves no slack. Both halves step down as a pair."""
    out = render.render_deck([{"role": "content", "title": "T", "body": ["a"]}],
                             _DESC["master_path"], _DESC, tmp_path / "d.pptx",
                             furniture={"contact": "d.cale.reeves@computationalsocialscience.org",
                                        "footer": "CSS2026 | A New Sense of Schelling Segregation"})
    st = _strip(out)
    assert _pt(st["contact"][0]) == _pt(st["footer"][0]) < 22
    assert _pt(st["contact"][0]) >= render._MIN_RUNNING_PT


@pytest.mark.skipif(not (_MASTER and _DESC), reason="neutral house master absent")
def test_an_over_long_title_is_shrunk_rather_than_clipped(tmp_path):
    """The title box is one line tall and anchored to its bottom, so a title that wraps grows UPWARD
    and its first line is cut off by the top of the slide — as a live deck's 54-character title was.
    """
    long_ = "Rhythm remains unrecovered despite harmonic clustering"
    out = render.render_deck([{"role": "content", "title": long_, "body": ["a"]},
                              {"role": "content", "title": "Short one", "body": ["a"]}],
                             _DESC["master_path"], _DESC, tmp_path / "d.pptx")
    from pptx import Presentation
    prs = Presentation(str(out))

    def _title(slide, needle):
        for sh in slide.shapes:
            if sh.has_text_frame and needle in sh.text_frame.text:
                return sh
        raise AssertionError(needle)

    big = _title(prs.slides[0], long_)
    inner = (big.width - big.text_frame.margin_left - big.text_frame.margin_right) / 12700
    assert render._need_pt(long_, _pt(big)) <= inner              # one line, inside the box
    assert _pt(_title(prs.slides[1], "Short one")) is None        # a short title is left alone


@pytest.mark.skipif(not (_MASTER and _DESC), reason="neutral house master absent")
def test_an_illustration_brief_is_the_only_thing_in_the_notes(tmp_path):
    """Notes carry no speech — but a slide with nothing to show may brief the picture it wants,
    addressed to whoever draws it. A slide that already has a figure gets no brief."""
    spec = [{"role": "content", "title": "T", "body": ["a"],
             "illustration": "a piano roll with the target phrase above a scrambled one"},
            {"role": "content", "title": "U", "body": ["b"]}]
    out = render.render_deck(spec, _DESC["master_path"], _DESC, tmp_path / "d.pptx")
    from pptx import Presentation
    prs = Presentation(str(out))
    assert prs.slides[0].notes_slide.notes_text_frame.text == (
        "ILLUSTRATION: a piano roll with the target phrase above a scrambled one")
    assert not prs.slides[1].has_notes_slide


@pytest.mark.skipif(not (_MASTER and _DESC), reason="neutral house master absent")
def test_the_logo_strip_shows_every_mark_not_just_the_first(tmp_path):
    """A placeholder holds ONE picture, which is why `logos: [idx]` could only ever show the first
    affiliation. The strip lays out as many as the interview chose."""
    logos = sorted((assets.home() / "logos").glob("*.png"))[:2]
    if len(logos) < 2:
        pytest.skip("need two logo files in the neutral home")
    spec = [{"role": "acknowledgements", "title": "Acknowledgements"}]
    out = render.render_deck(spec, _DESC["master_path"], _DESC, tmp_path / "d.pptx", logos=logos)
    from pptx import Presentation
    pics = [sh for sh in Presentation(str(out)).slides[0].shapes if sh.shape_type == 13]
    assert len(pics) == 2
    assert pics[0].left < pics[1].left          # laid out in a row, in order


@pytest.mark.skipif(not (_MASTER and _DESC), reason="neutral house master absent")
def test_an_unregistered_affiliation_is_set_in_text_not_dropped(tmp_path):
    """The registries have always promised "degrades to text (name only)" and never done it — an
    affiliation the author picked in the interview just vanished."""
    spec = [{"role": "acknowledgements", "title": "Acknowledgements"}]
    out = render.render_deck(spec, _DESC["master_path"], _DESC, tmp_path / "d.pptx",
                             logos=[{"name": "Nowhere University", "logo": None}])
    from pptx import Presentation
    assert "Nowhere University" in _texts(Presentation(str(out)).slides[0])


# ------------------------------------------------------- registry name matching
def test_an_alias_reaches_the_logo_filed_under_a_short_name(tmp_path, monkeypatch):
    """Manifests write the official long name; the registry files the logo under the short one."""
    home = tmp_path / "home"
    (home / "logos").mkdir(parents=True)
    (home / "logos" / "v.png").write_bytes(b"x")
    (home / "affiliations.yaml").write_text(
        'VCUarts Qatar:\n  logo: logos/v.png\n  aliases:\n    - "VCU School of the Arts, Qatar"\n')
    monkeypatch.setenv("RAZZLE_HOME", str(home))

    assert assets.logos_for(["VCUarts Qatar"])[0].name == "v.png"          # the key
    assert assets.logos_for(["VCU School of the Arts, Qatar"])[0].name == "v.png"   # an alias
    assert assets.logos_for(["  vcuarts   qatar  "])[0].name == "v.png"    # case/whitespace
    # an unmatched name keeps its place, with no logo, so it can be set in text
    entries = assets.logo_entries(["Nowhere University"])
    assert entries == [{"name": "Nowhere University", "logo": None}]


@pytest.mark.skipif(not (_MASTER and _DESC), reason="neutral house master absent")
def test_recutting_a_strip_box_keeps_its_vertical_geometry(tmp_path):
    """A placeholder inherits its geometry and carries no `a:xfrm` of its own, so setting ONE
    dimension materialises that element with the other three at zero. The address was re-cut to
    the right width and rendered at the top of the slide with no height at all."""
    out = render.render_deck([{"role": "split", "title": "T", "body": ["a"]}],
                             _DESC["master_path"], _DESC, tmp_path / "d.pptx",
                             furniture={"contact": "d.cale.reeves@computationalsocialscience.org",
                                        "footer": "CSS2026 | Sense of Schelling"})
    st = _strip(out)
    for sh, _ in st.values():
        assert sh.top > 0 and sh.height > 0


@pytest.mark.skipif(not (_MASTER and _DESC), reason="neutral house master absent")
def test_a_two_digit_slide_number_stays_on_one_line(tmp_path):
    """The number box is half an inch wide with a tenth of an inch of inset a side — 21pt for the
    number. One digit fitted; two wrapped, and every slide from ten on showed a stacked number with
    its second digit hanging off the bottom of the slide."""
    spec = [{"role": "title", "title": "A talk"}] + [
        {"role": "content", "title": f"Slide {i}", "body": ["x"]} for i in range(12)]
    out = render.render_deck(spec, _DESC["master_path"], _DESC, tmp_path / "d.pptx")
    from pptx import Presentation
    from pptx.oxml.ns import qn
    for slide in list(Presentation(str(out)).slides)[1:]:
        for sh in slide.shapes:
            if "slidenum" not in sh._element.xml.lower():
                continue
            body = sh._element.find(".//" + qn("a:bodyPr"))
            assert body.get("wrap") == "none"
            assert body.get("lIns") == "0" and body.get("rIns") == "0"

"""razzle.render — the python-pptx render core: a deck spec + a master + figures/logos → .pptx.

The master owns the look. razzle clones the master's layouts by ROLE (from a layout descriptor),
fills the text placeholders, and places figures/logos as pictures fitted into the descriptor-named
placeholder boxes — python-pptx cannot insert a picture into an OBJECT placeholder, so we `add_picture`
at the placeholder's geometry and drop the empty placeholder. Deterministic; the deck spec is the
durable artifact, the `.pptx` the output the author polishes.

A deck spec is a list of slides:
    {"role": "title", "title": "...", "subtitle": "..."}
    {"role": "figure", "title": "...", "figure": <figure-id>, "citation": "..."}
    {"role": "split",   "title": "...", "body": [...], "figure": <figure-id>, "citation": "..."}
    {"role": "content", "title": "...", "body": ["bullet", "bullet"]}

A deck carries no SPEAKER notes — what does not fit on the slide is spoken, not written. The notes
pane is used for one other thing entirely: a slide with no figure may carry an `illustration`, a one
line brief for a picture that does not exist yet. That is a production TODO addressed to whoever
builds the artwork, not a script addressed to the speaker, which is why it may live there when a
sentence may not.
"""

from __future__ import annotations

import copy
import math
from pathlib import Path

from pptx import Presentation
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

# The running strips (footer/contact/venue) are ONE line in a fixed box, so they are the one place
# text can overflow silently — the box does not grow and PowerPoint does not wrap what cannot wrap.
# A long contact address is the standing example: 44 characters into a 3" strip runs off the slide.
#
# They are also ONE strip, read as one line, so they get ONE size. The first attempt shrank each
# box's text on its own against an ASSUMED 12pt master, and this master sets them at 22 — so the
# address came out at 9.8pt beside a 22pt footer, a smudge next to its own neighbour. The size is
# now read from the master and shared, and it is the BOXES that move: the contact keeps its right
# edge and grows leftward into the footer's slack, since a footer reading "venue | short title" is
# nowhere near its 9.5 inches. Only when the whole strip still will not fit does the size drop, and
# then it drops for every member at once so they stay a matched pair.
_RUNNING_SLOTS = {"footer", "contact", "venue"}
_STRIP_SLOTS = ("footer", "contact")   # the bottom strip, laid out together, left to right
_MIN_RUNNING_PT = 11.0    # below this it is not a contact address, it is a smudge
_STRIP_GAP = Inches(0.25)  # the least air between the footer and the address
_MIN_TITLE_PT = 20.0      # a title smaller than this is not a title, it is a caption
_DEFAULT_PT = 18.0        # only when a master declares no size of its own
_ADVANCE = 0.55           # mean glyph advance as a fraction of point size, proportional sans;
                          # measured 0.53 on this master's footer, rounded up so the estimate errs
                          # toward a box that is too wide rather than text that is too long


def _clear_slides(prs) -> None:
    """Start from the master's layouts + theme, not its example slides. Drops each slide's
    RELATIONSHIP too (not just the sldId), so the orphaned slide parts are not re-serialised — a bare
    sldId removal leaves duplicate slide parts that corrupt the deck."""
    lst = prs.slides._sldIdLst
    for sid in list(lst):
        prs.part.drop_rel(sid.get(qn("r:id")))
        lst.remove(sid)


def _set_text(ph, value) -> None:
    if isinstance(value, (list, tuple)):
        tf = ph.text_frame
        tf.clear()
        for i, line in enumerate(value):
            para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            para.text = str(line)
    else:
        ph.text = str(value)


def _layout_pt(layout, idx: int, default: float) -> float:
    """The size the MASTER sets for this placeholder, in points.

    Read, never assumed. A slide placeholder cloned by `add_slide` carries no size of its own — it
    inherits the layout's `a:lvl1pPr/a:defRPr/@sz` — so anything that reasons about the rendered
    size has to look there. Assuming a number instead is what put a 9.8pt address next to a 22pt
    footer on a master that sets both at 22.
    """
    for ph in layout.placeholders:
        if ph.placeholder_format.idx == idx:
            lvl1 = ph._element.find(".//" + qn("a:lstStyle") + "/" + qn("a:lvl1pPr"))
            d = lvl1.find(qn("a:defRPr")) if lvl1 is not None else None
            if d is not None and d.get("sz"):
                return int(d.get("sz")) / 100
    return default


def _usable_pt(ph) -> float:
    """The placeholder's inner width in points — the box less its own left/right insets.

    The insets are 0.1" a side on this master: 14.4pt of the budget, which is the difference
    between an address that fits and one the renderer wraps onto a second line.
    """
    tf = ph.text_frame
    return max(0.0, (ph.width - (tf.margin_left or 0) - (tf.margin_right or 0)) / 12700)


def _need_pt(text: str, size: float) -> float:
    """Estimated rendered width of one line, in points. No font stack here to measure with, so
    this is an estimate — deliberately a generous one (see `_ADVANCE`)."""
    return _ADVANCE * len(text) * size


def _set_size(ph, size: float) -> None:
    tf = ph.text_frame
    tf.word_wrap = False
    for para in tf.paragraphs:
        para.font.size = Pt(size)
        for run in para.runs:
            run.font.size = Pt(size)


def _fit_running_text(ph, text: str, size: float) -> None:
    """A lone running strip (the title slide's venue line): shrink only if it overflows its box."""
    box, need = _usable_pt(ph), _need_pt(text, size)
    if not (box and need):
        return
    if need > box:
        size = max(_MIN_RUNNING_PT, round(size * box / need, 1))
    _set_size(ph, size)


def _fit_strip(members: list[tuple], size: float) -> None:
    """Lay out the bottom strip — footer then contact — as ONE line at ONE size.

    `members` is [(placeholder, text)] in left-to-right order. Each box is re-cut to what its own
    text needs: the last member keeps its right edge and grows leftward, the ones before it start
    at their own left edge and take what is left. The size drops only when the strip as a whole
    will not fit, and then it drops for everyone, so the footer and the address are never rendered
    at two different sizes beside each other.
    """
    members = [(ph, t) for ph, t in members if ph is not None and t]
    if not members:
        return
    inset = sum(m[0].width - int(_usable_pt(m[0]) * 12700) for m in members)
    span = members[-1][0].left + members[-1][0].width - members[0][0].left
    room = span - inset - _STRIP_GAP * (len(members) - 1)
    chars = _ADVANCE * sum(len(t) for _, t in members)
    if chars > 0 and room > 0:
        size = min(size, max(_MIN_RUNNING_PT, round(room / 12700 / chars, 1)))
    # The last member is right-aligned to the strip's fixed right edge and grows leftward into the
    # slack; everyone before it keeps their own left edge and takes what remains.
    def _wants(ph, text) -> int:
        # ceil, not truncate: a box a hair narrower than the line it holds wraps the line, and the
        # whole point of moving the box was to keep the address on one of them.
        return math.ceil(_need_pt(text, size) * 12700) + (ph.width - int(_usable_pt(ph) * 12700))

    def _place(ph, left: int, width: int) -> None:
        """Re-cut the box, writing ALL FOUR values.

        A placeholder inherits its geometry from the layout and carries no `a:xfrm` of its own.
        Setting one dimension materialises that element with the other three at zero — the address
        was re-cut to the right width and landed at the top of the slide with no height at all.
        """
        top, height = ph.top, ph.height          # read the inherited values BEFORE writing any
        ph.left, ph.width, ph.top, ph.height = left, width, top, height

    edge = members[-1][0].left + members[-1][0].width
    last, last_text = members[-1]
    _set_size(last, size)
    width = min(max(_wants(last, last_text), last.width), edge - members[0][0].left)
    _place(last, edge - width, width)
    for ph, text in members[:-1]:
        _set_size(ph, size)
        _place(ph, ph.left, max(0, min(_wants(ph, text), last.left - _STRIP_GAP - ph.left)))


def _fit_title(ph, text: str, size: float) -> None:
    """Shrink an over-long title until it fits its box on one line.

    The title box is one line tall and anchored to its bottom, so a title that wraps grows UPWARD
    and the first line is clipped by the top of the slide — the audience reads the bottom half of
    the letters. `compose` budgets titles in characters to stop this at the source; this is the
    backstop for the one that still arrives too long.
    """
    box, need = _usable_pt(ph), _need_pt(text, size)
    if box and need > box:
        _set_size(ph, max(_MIN_TITLE_PT, round(size * box / need, 1)))


def _strip_unused(slide, filled_idxs: set) -> None:
    """Remove the master's placeholders this slide didn't fill, so they don't render as "click to
    add text". Only the UNFILLED ones go: a master's footer and contact strips are furniture the
    descriptor is expected to fill, not litter to sweep up.

    There is deliberately no slide-number branch here. python-pptx treats DATE/FOOTER/SLIDE_NUMBER
    as *latent* placeholders and never clones them onto a new slide, so a guard preserving one
    could never fire — the number has to be ADDED (see `_add_slide_number`), not kept.
    """
    for ph in list(slide.placeholders):
        if ph.placeholder_format.idx in filled_idxs:
            continue
        ph._element.getparent().remove(ph._element)


def _next_shape_id(slide) -> int:
    ids = [int(e.get("id")) for e in slide.shapes._spTree.iter(qn("p:cNvPr"))
           if (e.get("id") or "").isdigit()]
    return max(ids, default=1) + 1


def _add_slide_number(slide, layout) -> None:
    """Give the slide the master's slide-number placeholder.

    `add_slide` skips it (latent), so we deep-copy the layout's own <p:sp> onto the slide. It
    carries a <a:fld type="slidenum"> field, so PowerPoint numbers it live and the number stays
    right when slides are reordered — which a literal string would not.

    The copy arrives carrying the LAYOUT's shape id, which collides with a shape already on the
    slide (a split slide ends up with two shapes numbered 7). Shape ids must be unique within a
    slide's spTree, so the clone is renumbered before it is appended.
    """
    for ph in layout.placeholders:
        if ph.element.ph_type == "sldNum" or "SLIDE_NUMBER" in str(ph.placeholder_format.type):
            el = copy.deepcopy(ph._element)
            for cnv in el.iter(qn("p:cNvPr")):
                cnv.set("id", str(_next_shape_id(slide)))
            # The box is half an inch wide with a tenth of an inch of inset each side, leaving
            # 21pt for the number. One digit sat in it comfortably; two wrapped, so every slide
            # from ten on showed its number stacked with the second digit hanging off the bottom
            # of the slide. The insets buy a page number nothing, so they go — that is 36pt of
            # room in the same box, and the renderer is told not to wrap in it either.
            for body in el.iter(qn("a:bodyPr")):
                body.set("wrap", "none")
                body.set("lIns", "0")
                body.set("rIns", "0")
            slide.shapes._spTree.append(el)
            return


def _set_illustration_note(slide, brief: str) -> None:
    """The ONE thing the notes pane carries: a brief for a picture this slide wants and does not
    have. Nothing a speaker would read aloud ever goes here."""
    slide.notes_slide.notes_text_frame.text = f"ILLUSTRATION: {brief}"


def _place_picture(slide, ph, img: Path) -> None:
    """Add a picture fitted (aspect-preserved) inside the placeholder box, centred, then remove the
    now-empty placeholder so it doesn't render 'click to add'."""
    left, top, bw, bh = ph.left, ph.top, ph.width, ph.height
    pic = slide.shapes.add_picture(str(img), left, top, width=bw)
    if pic.height > bh:                       # too tall for the box — refit by height
        pic._element.getparent().remove(pic._element)
        pic = slide.shapes.add_picture(str(img), left, top, height=bh)
    pic.left = left + (bw - pic.width) // 2   # centre in the box
    pic.top = top + (bh - pic.height) // 2
    ph._element.getparent().remove(ph._element)


def _logo_items(logos) -> list[dict]:
    """Accept either bare image paths or {name, logo} entries, and normalise to the latter — an
    entry with no logo is a name that will be SET IN TEXT rather than dropped."""
    out = []
    for item in logos or []:
        if isinstance(item, dict):
            out.append({"name": item.get("name", ""), "logo": item.get("logo")})
        elif item:
            out.append({"name": "", "logo": item})
    return out


def _place_logo_strip(slide, box: dict, items: list[dict]) -> None:
    """Lay every logo out in a row inside an explicit box (inches), fitted to its height and centred
    as a group; a name with no registered logo is set as text in the same row.

    A single placeholder can hold ONE picture, which is why the old `logos: [idx]` mapping could
    never show a second affiliation — it silently kept the first and dropped the rest. A strip is
    the shape the thing actually is: a row of marks whose count is not known until the interview
    has run.
    """
    if not items:
        return
    left, top = Inches(box.get("left", 0)), Inches(box.get("top", 0))
    width, height = Inches(box.get("width", 1)), Inches(box.get("height", 0.4))
    gap = Inches(box.get("gap", 0.25))

    placed = []
    for it in items:
        if it.get("logo"):
            pic = slide.shapes.add_picture(str(it["logo"]), left, top, height=height)
            placed.append(pic)
        elif it.get("name"):
            tb = slide.shapes.add_textbox(left, top, Inches(2), height)
            tf = tb.text_frame
            tf.word_wrap = False
            tf.text = it["name"]
            for r in tf.paragraphs[0].runs:
                r.font.size = Pt(10)
            tb.width = max(Inches(0.5), Inches(0.09 * len(it["name"])))   # rough text advance
            placed.append(tb)

    total = sum(sh.width for sh in placed) + gap * max(0, len(placed) - 1)
    x = left + max(0, (width - total) // 2)          # centre the row in its box
    for sh in placed:
        sh.left = x
        sh.top = top + (height - sh.height) // 2     # vertical middle, whatever each one's height
        x += sh.width + gap


def render_deck(spec: list[dict], master: str, descriptor: dict, out_path: Path, *,
                figures: dict | None = None, logos: list | None = None,
                furniture: dict | None = None) -> Path:
    """Render the deck spec onto the branded master.

    `figures` maps a slide's figure-id → an image path. `logos` is the ordered list of logo image
    paths (or {name, logo} entries) for a role's logo slots or its `logo_strip`. `furniture` is the
    deck-level running text — venue/date, the running footer, the contact address — which is the
    same on every slide and so is NOT in the spec: the composer never sees it and cannot invent it.
    A text slot takes the slide's own value first and falls back to the furniture.

    A slide with an `illustration` and no figure gets that brief in its notes pane — the only thing
    notes ever hold. The opening slide carries no page number, as a title page never does.

    A missing figure or logo is simply skipped (the box stays empty) — never a crash.
    """
    prs = Presentation(str(master))
    _clear_slides(prs)
    roles = descriptor.get("roles", {})
    figures = figures or {}
    furniture = furniture or {}
    items = _logo_items(logos)
    for n, slide in enumerate(spec):
        rdef = roles.get(slide.get("role", "figure"))
        if rdef is None:
            continue
        layout = prs.slide_layouts[rdef["layout"]]
        s = prs.slides.add_slide(layout)
        phs = {ph.placeholder_format.idx: ph for ph in s.placeholders}
        text_slots = rdef.get("text") or {}
        filled: set = set()
        strip: list[tuple] = []
        for slot, idx in text_slots.items():
            val = slide.get(slot) or furniture.get(slot)     # the slide's own, else the deck's
            if val and idx in phs:
                _set_text(phs[idx], val)
                size = _layout_pt(layout, idx, _DEFAULT_PT)
                if slot in _STRIP_SLOTS and isinstance(val, str):
                    strip.append((slot, phs[idx], val, size))
                elif slot in _RUNNING_SLOTS and isinstance(val, str):
                    _fit_running_text(phs[idx], val, size)
                elif slot == "title" and isinstance(val, str):
                    _fit_title(phs[idx], val, size)
                filled.add(idx)
        if strip:      # footer and contact are one line and take one size — see _fit_strip
            strip.sort(key=lambda m: _STRIP_SLOTS.index(m[0]))
            _fit_strip([(ph, val) for _, ph, val, _ in strip], min(m[3] for m in strip))
        for slot, idx in (rdef.get("picture") or {}).items():
            img = figures.get(slide.get(slot))
            if img and idx in phs:
                _place_picture(s, phs[idx], Path(img))
                filled.add(idx)
        for i, idx in enumerate(rdef.get("logos") or []):      # one placeholder, one logo
            if i < len(items) and items[i].get("logo") and idx in phs:
                _place_picture(s, phs[idx], Path(items[i]["logo"]))
                filled.add(idx)
        _strip_unused(s, filled)      # drop the empty master placeholders this slide didn't use
        if rdef.get("logo_strip"):    # after the strip, so it is not swept up as unfilled
            _place_logo_strip(s, rdef["logo_strip"], items)
        if slide.get("illustration") and not slide.get("figure"):
            _set_illustration_note(s, str(slide["illustration"]))
        if n:      # the OPENING slide is never numbered — by position, not by role. Keying on
                   # the role dropped the number from a closing "thank you" slide the composer
                   # had also written as a title, so the deck skipped from 12 to 14.
            _add_slide_number(s, layout)
    prs.save(str(out_path))
    return out_path

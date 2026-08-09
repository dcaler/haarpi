"""razzle.render — the python-pptx render core: a deck spec + a master + figures/logos → .pptx.

The master owns the look. razzle clones the master's layouts by ROLE (from a layout descriptor),
fills the text placeholders, and places figures/logos as pictures fitted into the descriptor-named
placeholder boxes — python-pptx cannot insert a picture into an OBJECT placeholder, so we `add_picture`
at the placeholder's geometry and drop the empty placeholder. Deterministic; the deck spec is the
durable artifact, the `.pptx` the output the author polishes.

A deck spec is a list of slides:
    {"role": "title", "title": "...", "subtitle": "..."}
    {"role": "figure", "title": "...", "figure": <figure-id>, "caption": "...", "notes": "..."}
    {"role": "content", "title": "...", "body": ["bullet", "bullet"], "notes": "..."}
"""

from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.oxml.ns import qn


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


def render_deck(spec: list[dict], master: str, descriptor: dict, out_path: Path, *,
                figures: dict | None = None, logos: list | None = None) -> Path:
    """Render the deck spec onto the branded master. `figures` maps a slide's figure-id → an image
    path; `logos` is the ordered list of logo image paths to drop into a role's logo slots. A missing
    figure or logo is simply skipped (the box stays empty) — never a crash."""
    prs = Presentation(str(master))
    _clear_slides(prs)
    roles = descriptor.get("roles", {})
    figures = figures or {}
    for slide in spec:
        rdef = roles.get(slide.get("role", "figure"))
        if rdef is None:
            continue
        s = prs.slides.add_slide(prs.slide_layouts[rdef["layout"]])
        phs = {ph.placeholder_format.idx: ph for ph in s.placeholders}
        for slot, idx in (rdef.get("text") or {}).items():
            val = slide.get(slot)
            if val and idx in phs:
                _set_text(phs[idx], val)
        for slot, idx in (rdef.get("picture") or {}).items():
            img = figures.get(slide.get(slot))
            if img and idx in phs:
                _place_picture(s, phs[idx], Path(img))
        for i, idx in enumerate(rdef.get("logos") or []):
            if logos and i < len(logos) and logos[i] and idx in phs:
                _place_picture(s, phs[idx], Path(logos[i]))
        if slide.get("notes"):
            s.notes_slide.notes_text_frame.text = str(slide["notes"])
    prs.save(str(out_path))
    return out_path

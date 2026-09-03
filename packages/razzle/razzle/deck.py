"""razzle.deck — the orchestrator: gather a project's inputs → compose the deck spec → render the
branded .pptx, sized to a presentation format. One call from a project root to a deck.

The output lands in `slides/{venue}/{date}_{short}_deck_ra.pptx`. The folder is the VENUE — that is
what a deck is browsed by, and the format is a property of the talk rather than a way to find it —
so the filename infix stays `deck` (the stage's infix is what `latest_release` filters on). The `_ra`
draft is a first-class revision-chain artifact: the author reviews it IN PLACE with PowerPoint
comments, and `haarpi next` mints it to the token-free `{date}_{short}_deck.pptx`. Figures are exported
to PNG on demand from the pool; logos come from the neutral registries. Best-effort: a missing
master/renderer degrades to writing the spec.
"""

from __future__ import annotations

import json
from pathlib import Path

from haarpi import figure as _figure
from haarpi import naming as _naming

from razzle import assets, compose, formats, gather, render


def build_deck(root: Path, fmt: str, brain, *, master: str = "default",
               out: Path | None = None, bundle: dict | None = None) -> dict:
    """Gather → compose → render a deck for `fmt`. Returns {spec, pptx} (pptx None if no master).

    `bundle` lets a caller that has already gathered (the CLI reports what it found before
    authoring) hand it in rather than have the manuscript read a second time.
    """
    if fmt not in formats.FORMATS:
        raise ValueError(f"unknown format {fmt!r} — one of {sorted(formats.FORMATS)}")
    b = bundle if bundle is not None else gather.bundle(root, fmt)
    spec = compose.compose(brain, b["narrative"], b["figures"], b["claims"],
                           manuscript=b.get("manuscript", ""), fmt=fmt)
    # The facts the composer does not get to write: the paper's title, every author credited with
    # exactly one contact address, and the closing acknowledgements. The title goes on FIRST — the
    # running footer is built from it below.
    gather.apply_title(spec, b.get("title", ""))
    gather.apply_byline(spec, b["byline"], b["email"])
    spec = gather.apply_acknowledgements(spec, gather.acknowledgements(root, fmt))

    slides_dir = gather.deck_dir(root, fmt)
    slides_dir.mkdir(parents=True, exist_ok=True)
    # the durable artifact: the spec (editable, re-renderable)
    (slides_dir / "spec.json").write_text(json.dumps(spec, indent=2), encoding="utf-8")

    desc = assets.descriptor(master)
    if desc is None or not desc.get("master_path"):
        return {"spec": spec, "pptx": None}   # no master to render against — spec is written

    # export each referenced figure to PNG for embedding
    fig_paths: dict[str, str] = {}
    for s in spec:
        fid = s.get("figure")
        if fid and fid not in fig_paths:
            png = _figure.resolve(root, b["short_title"], fid, "png", width=1600)
            if png:
                fig_paths[fid] = str(png)

    out = out or (slides_dir / _naming.major_name(b["short_title"], "pptx", infix="deck"))
    render.render_deck(spec, desc["master_path"], desc, out, figures=fig_paths, logos=b["logos"],
                       furniture=gather.furniture(root, fmt, spec))
    return {"spec": spec, "pptx": out}

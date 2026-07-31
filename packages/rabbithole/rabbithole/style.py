"""rabbitHole's binding to the shared style engine (haarpi.style).

The engine owns the profile format, the retrain decision, and the training loop — the machinery
that used to live here in a copy that drifted from raconteur's and carried a fix the other never
got. This module supplies only what is rabbitHole's: its Zotero library, how it pulls prose from
a PDF, and its brain. See DESIGN_style_engine.md.

Consuming the profile: ``load_style_profile`` now returns the MEASURED style block (a palette of
the author's transitions, hedges and rhythm, plus passages of the real prose) rather than an
analysis-only body dump — so rabbitHole synthesises in the same signature-backed voice raconteur
drafts in. The interface is unchanged (a string for the synthesis prompt); only the content got
better.
"""

from __future__ import annotations

import sys
from pathlib import Path

from haarpi import style as _engine
from haarpi import voice
# Re-exported for existing call sites and tests: the neutral profile path, the shared retrain
# decision, the frontmatter reader (old private name), and the label helper.
from haarpi.style import (  # noqa: F401
    STYLE_PROFILE_PATH, needs_training,
    load_meta as _load_existing_meta,
    item_label as _item_label,
)

from . import runlog
from .brain import Brain
from .config import load_global, load_project, save_project


def load_style_profile() -> str:
    """The author's voice for the synthesis prompt: a measured palette + passages of real prose."""
    return _engine.load_block()


class RabbitHoleStylePolicy:
    """rabbitHole's half of the contract: measure the author's PDFs, in rabbitHole's library."""

    def __init__(self, directory: str, cfg, gc):
        self._dir = directory
        self._cfg = cfg
        self._gc = gc
        self.author = cfg.style_author
        self.requested_keys = list(cfg.style_paper_keys or [])
        from . import zotero as _zotero
        self._zc = _zotero.ZoteroClient(gc)

    def items_by_keys(self, keys: list[str]) -> list[dict]:
        return self._zc.items_by_keys(keys)

    def search_author(self, name: str) -> list[dict]:
        return self._zc.search_by_author(name)

    def select_items(self, items: list[dict]) -> list[dict]:
        return items                        # rabbitHole trains on every match, no prompt

    def prose_for(self, item: dict, tmpdir: Path) -> str:
        """The author's prose, read from the PDF by layout block (falling back to the index)."""
        key = item.get("data", {}).get("key", "")
        att_key = self._zc.pdf_attachment_key(key)
        if not att_key:
            return ""
        pdf = Path(tmpdir) / f"{key}.pdf"
        prose = ""
        if self._zc.download_attachment(att_key, pdf):
            prose = voice.pdf_prose(pdf)
        if not prose:                       # no file, or an image-only scan
            prose = voice.clean_prose(self._zc.fulltext(att_key))
        return prose

    def analyze(self, author: str, exemplars: list[str]) -> str:
        """Best-effort prose analysis — the signature and exemplars carry the voice regardless."""
        try:
            brain = Brain(self._cfg.brain, self._gc)
            return brain.coordinator(
                _engine._ANALYZE_STYLE_PROMPT.format(
                    author=author,
                    excerpts="\n\n".join(f"--- excerpt ---\n{e}" for e in exemplars)),
                system=_engine._SYSTEM, num_ctx=16384)
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] style analysis failed ({e}) — the measured signature and exemplars "
                  f"are written regardless; they are what matters")
            return ""

    def save_keys(self, keys: list[str]) -> None:
        self._cfg.style_paper_keys = list(keys)
        save_project(self._cfg, self._dir)

    def log(self, msg: str) -> None:
        print(f"  {runlog.stamp()}{msg}", flush=True)


def run(directory: str = ".") -> int:
    runlog.start()
    gc = load_global()
    if not gc.have_zotero:
        print("[error] ZOTERO_API_KEY and ZOTERO_LIBRARY_ID must be set", file=sys.stderr)
        return 1
    try:
        cfg = load_project(directory)
    except FileNotFoundError:
        print("[error] no litrev.yaml — run 'rabbitHole init' first", file=sys.stderr)
        return 1
    if not cfg.style_author:
        print("[error] no style_author in litrev.yaml — run 'rabbitHole init' first",
              file=sys.stderr)
        return 1
    return _engine.run(RabbitHoleStylePolicy(directory, cfg, gc))

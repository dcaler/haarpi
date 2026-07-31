"""raconteur's binding to the shared style engine (haarpi.style).

The engine owns the profile format, the retrain decision, and the training loop — all the
machinery that used to be duplicated here and in rabbitHole, drift and all. This module supplies
only what is raconteur's: its Zotero library, how it pulls prose from a PDF, its brain, and its
interactive paper selection. See DESIGN_style_engine.md.
"""

from __future__ import annotations

from pathlib import Path

from haarpi import style as _engine
from haarpi import voice
# Re-exported for existing call sites and tests: the neutral profile path, the format check,
# and the shared label helper (under its old private name).
from haarpi.style import (  # noqa: F401
    STYLE_PROFILE_PATH, profile_is_current, read_profile,
    item_label as _item_label,
)

from .brain import Brain
from .config import GlobalConfig, ProjectConfig, ZoteroConfig
from .log import log
from .zotero import ZoteroClient


def _load_existing_profile(project_dir: Path | None = None) -> dict:
    """The profile's frontmatter (empty if never trained). Kept for wizard.py."""
    return _engine.load_meta()


class RaconteurStylePolicy:
    """Raconteur's half of the contract: measure the author's PDFs, in raconteur's library."""

    def __init__(self, cfg: ProjectConfig, gcfg: GlobalConfig, author: str):
        self._cfg = cfg
        self._gcfg = gcfg
        self.author = author
        self.requested_keys = list(cfg.style_paper_keys or [])
        self._zc = ZoteroClient(ZoteroConfig.from_env())

    def items_by_keys(self, keys: list[str]) -> list[dict]:
        return self._zc.items_by_keys(keys)

    def search_author(self, name: str) -> list[dict]:
        return self._zc.search_by_author(name)

    def select_items(self, items: list[dict]) -> list[dict]:
        """Interactive confirmation — raconteur lets the author exclude papers from the search."""
        print(f"\nFound {len(items)} paper(s) by '{self.author}':")
        for i, item in enumerate(items, 1):
            print(f"  {i:2}. {_item_label(item)}")
        print()
        sel = input(
            "Confirm papers to train on (Enter = all, or comma-separated numbers to exclude): "
        ).strip()
        if sel:
            exclude = {int(x.strip()) - 1 for x in sel.split(",") if x.strip().isdigit()}
            return [item for i, item in enumerate(items) if i not in exclude]
        return items

    def prose_for(self, item: dict, tmpdir: Path) -> str:
        """The author's prose, read from the PDF by layout block (falling back to the index)."""
        key = item.get("data", {}).get("key", "")
        att_key = self._zc.pdf_attachment_key(key)
        if not att_key:
            return ""
        pdf = Path(tmpdir) / f"{key}.pdf"
        prose = ""
        if self._zc.download(att_key, pdf):
            prose = voice.pdf_prose(pdf)
        if not prose:                       # no file, or an image-only scan
            prose = voice.clean_prose(self._zc.fulltext(att_key))
        return prose

    def analyze(self, author: str, exemplars: list[str]) -> str:
        """Best-effort prose analysis — the measured signature and exemplars carry the voice, so
        a brain that is busy or absent must not cost the author their profile."""
        try:
            brain = Brain(self._gcfg, coordinator=self._cfg.brain.coordinator_model)
            return brain.coordinator(
                _engine._ANALYZE_STYLE_PROMPT.format(
                    author=author,
                    excerpts="\n\n".join(f"--- excerpt ---\n{e}" for e in exemplars)),
                system=_engine._SYSTEM, num_ctx=16384)
        except Exception as e:  # noqa: BLE001
            log(f"[warn] the style analysis pass failed ({e}) — the measured signature and the "
                f"exemplars are written regardless; they are what matters")
            return ""

    def save_keys(self, keys: list[str]) -> None:
        self._cfg.style_paper_keys = list(keys)

    def log(self, msg: str) -> None:
        log(f"[raconteur] {msg}")

    def close(self) -> None:
        self._zc.close()


def run(project_dir: Path) -> None:
    if not ProjectConfig.exists(project_dir):
        log("[error] no paper/raconteur.yaml — run 'raconteur init' first")
        raise SystemExit(1)

    cfg = ProjectConfig.load(project_dir)
    gcfg = GlobalConfig.load()
    if not ZoteroConfig.from_env().available:
        log("[error] ZOTERO_API_KEY and ZOTERO_LIBRARY_ID must be set")
        raise SystemExit(1)

    author = cfg.style_author
    if not author:
        author = input("Author name to search in Zotero: ").strip()
        if not author:
            raise SystemExit(0)

    policy = RaconteurStylePolicy(cfg, gcfg, author)
    try:
        rc = _engine.run(policy)
    finally:
        policy.close()
    if rc:
        raise SystemExit(rc)

    if author != cfg.style_author or not cfg.use_style:
        cfg.style_author = author
        cfg.use_style = True
    cfg.save(project_dir)

"""Paper cards: what claim grounding learns about one paper, kept once for every project.

A card belongs to the paper, not to a project (PLAN_claim_grounding.html §2): what a paper
claims, and whether its own words say it, does not depend on who cites it. So cards live in
one store in the HAARPi tree (`cards/`, kept out of git: they hold the papers' text) and a
project that cites a paper another project already carded pays nothing for it.

One directory per paper, one file per step and model, so the two verifiers can run side by
side on two cards without writing the same file:

    cards/<citekey>/card.json          the paper (label, title, abstract, links) and its claims
    cards/<citekey>/text.txt           the [p.N]-marked text the card was made from
    cards/<citekey>/kind_<model>.json  review or not
    cards/<citekey>/reread_<model>.json, confirm_<model>.json, own_<model>.json
    cards/<citekey>/located.json       the verifiers' spans, found in the text (code)
    cards/<citekey>/verdict.json       each claim confirmed, or where it failed (code)

A card keeps the text it was made from, so its passages and offsets stay true when the
extraction changes later (`text_version` records which). A card is reused unless it was made by
other models; a newer text version alone does not redo it.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

HAARPI_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DIR = HAARPI_ROOT / "cards"
DEFAULT_EXTRACTOR = "llama3.3:latest"
DEFAULT_VERIFIERS = ("gemma4:31b-it-q4_K_M", "gpt-oss:20b")
DEFAULT_KIND_MODELS = ("gemma4:31b-it-q4_K_M", "qwen3.6:27b-16k")
# Scores each confirmed claim against the project's topics. The fact base scored with the
# coordinator (qwen3.6), so the scores mean what they meant there.
DEFAULT_TOPIC_MODEL = "qwen3.6:27b-16k"


@dataclass
class CardsConfig:
    dir: Path = DEFAULT_DIR
    extractor: str = DEFAULT_EXTRACTOR
    verifiers: tuple = DEFAULT_VERIFIERS
    kind_models: tuple = DEFAULT_KIND_MODELS
    topic_model: str = DEFAULT_TOPIC_MODEL
    ollama_url: str = ""
    extra: dict = field(default_factory=dict)


def load_config() -> CardsConfig:
    """`[cards]` in ~/.config/haarpi/config.toml: dir, extractor, verifiers, kind_models,
    topic_model."""
    from haarpi import config as haarpi_config
    from . import config as rhconfig
    data = haarpi_config.merged_config("rabbithole", rhconfig._legacy_normalized()).get("cards", {})
    cfg = CardsConfig(
        dir=Path(os.environ.get("HAARPI_CARDS") or data.get("dir") or DEFAULT_DIR).expanduser(),
        extractor=data.get("extractor", DEFAULT_EXTRACTOR),
        verifiers=tuple(data.get("verifiers") or DEFAULT_VERIFIERS),
        kind_models=tuple(data.get("kind_models") or DEFAULT_KIND_MODELS),
        topic_model=data.get("topic_model") or DEFAULT_TOPIC_MODEL,
        ollama_url=rhconfig.load_global().ollama_url)
    if len(cfg.verifiers) != 2:
        raise ValueError(f"[cards] verifiers must name two models, not {list(cfg.verifiers)}")
    return cfg


def slug(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", model)


def _dump(fp: Path, obj) -> None:
    fp.parent.mkdir(parents=True, exist_ok=True)
    tmp = fp.with_name(fp.name + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(fp)                     # never a half-written card, even if the task is killed


def _load(fp: Path, default=None):
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


class Card:
    """One paper's directory in the store."""

    def __init__(self, store: Path, citekey: str):
        self.citekey = citekey
        self.dir = Path(store) / citekey

    # the paper and its claims
    def meta(self) -> dict | None:
        return _load(self.dir / "card.json")

    def write_meta(self, meta: dict) -> None:
        _dump(self.dir / "card.json", meta)

    def text(self) -> str:
        try:
            return (self.dir / "text.txt").read_text(encoding="utf-8")
        except OSError:
            return ""

    def write_text(self, text: str) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "text.txt").write_text(text, encoding="utf-8")

    def claims(self) -> list[dict]:
        return (self.meta() or {}).get("claims") or []

    def extracted_by(self, extractor: str) -> bool:
        m = self.meta() or {}
        return m.get("extractor") == extractor and "claims" in m and not m.get("error")

    # one file per step and model
    def step(self, name: str, model: str | None = None) -> dict:
        return _load(self._step_fp(name, model), {}) or {}

    def write_step(self, name: str, obj: dict, model: str | None = None) -> None:
        _dump(self._step_fp(name, model), obj)

    def _step_fp(self, name: str, model: str | None) -> Path:
        return self.dir / (f"{name}_{slug(model)}.json" if model else f"{name}.json")


def card(cfg: CardsConfig, citekey: str) -> Card:
    return Card(cfg.dir, citekey)

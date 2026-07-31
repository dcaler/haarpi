"""Shared style engine: train one voice profile from the author's own publications, and
read it back during synthesis. Used by BOTH tools that write in the author's voice —
rabbitHole (lit reviews) and raconteur (papers).

Why this is shared (see DESIGN_style_engine.md): the two tools had independent copies of this
subsystem that COLLIDED on one file and DRIFTED. A fix that stopped a train-on-every-run loop
landed in only one copy, so the other kept re-arming it. The profiles were not even peers —
raconteur measured a voice SIGNATURE (rhythm, closed-class palettes) and picked exemplars,
while rabbitHole wrote only a prose analysis. rabbitHole could read raconteur's richer profile
perfectly; raconteur could not read rabbitHole's. So there is one canonical profile — the
MEASURED one — produced here and read by both.

Neutral territory. The profile lives at ``~/.config/haarpi/style_profile.md`` — the pipeline's
PII boundary (see haarpi.config), not under any single tool's name. Reads fall back to the
legacy ``~/.config/raconteur/style_profile.md`` during the transition; writes only ever go to
the neutral path. The profile never enters a git tree — it has always lived in ~/.config.

Engine owns the invariant; a per-tool ``StylePolicy`` injects the tool-specific I/O (which
Zotero library, how to pull prose from a PDF, which brain, how to log). Same law as the redline
engine: the engine never imports a tool.
"""

from __future__ import annotations

import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Protocol

import yaml

from haarpi import config as _hconfig
from haarpi import voice


# ── where the profile lives ──────────────────────────────────────────────────
# Neutral: ~/.config/haarpi/. Legacy read-fallbacks are honored (not written to) so a profile
# trained before this consolidation is still found. Writes go to STYLE_PROFILE_PATH only.

STYLE_PROFILE_PATH = _hconfig.config_root() / "haarpi" / "style_profile.md"
LEGACY_PROFILE_PATHS = (_hconfig.config_root() / "raconteur" / "style_profile.md",)


def _read_path() -> Path | None:
    """The path a read should come from: the neutral one if present, else a legacy fallback."""
    if STYLE_PROFILE_PATH.exists():
        return STYLE_PROFILE_PATH
    for p in LEGACY_PROFILE_PATHS:
        if p.exists():
            return p
    return None


def profile_exists() -> bool:
    """True if a profile exists anywhere we read from — neutral OR a legacy fallback.

    Callers that gate "train if missing" on this stay correct during the transition: a profile
    still sitting at the old raconteur path counts as present, so it is not needlessly retrained.
    """
    return _read_path() is not None


def _migrate_legacy() -> None:
    """One-time, non-destructive: relocate a pre-consolidation profile into neutral territory.

    Copies (does not move) the newest legacy profile to the neutral path when the neutral path
    is absent, so a profile trained under a single tool's name comes to live at the shared,
    neutral location without a retrain. The legacy file is left in place.
    """
    if STYLE_PROFILE_PATH.exists():
        return
    for p in LEGACY_PROFILE_PATHS:
        if p.exists():
            STYLE_PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
            STYLE_PROFILE_PATH.write_text(p.read_text(encoding="utf-8", errors="replace"),
                                          encoding="utf-8")
            return


# ── the analysis prompt (a nicety on top of the measured signature) ──────────

_SYSTEM = (
    "You are an expert academic writing analyst. "
    "You identify the characteristic voice and prose style of academic authors."
)

_ANALYZE_STYLE_PROMPT = """\
Analyze the writing style in these excerpts from academic papers authored by {author}.

Excerpts:
{excerpts}

Write a concise style profile (250–350 words) covering:
1. Sentence structure — typical length, complexity, active vs passive voice balance
2. Paragraph structure — how the author opens, develops, and closes an argument
3. Hedging and certainty — characteristic phrases, how claims are qualified or asserted
4. Transitions — how ideas and sections are connected
5. Evidence handling — how the author introduces, contextualises, and interprets evidence
6. Vocabulary register — technical density, any distinctive terminology patterns

Then provide a section titled "Representative Excerpts" with 3 verbatim passages \
(2–4 sentences each) that best exemplify this author's prose style. \
Choose passages that show the voice most clearly — not boilerplate methodology or \
references sections.

Output format:
## Style Profile
[analysis]

## Representative Excerpts
[3 numbered excerpts]
"""


# ── labels ───────────────────────────────────────────────────────────────────

def item_label(item: dict) -> str:
    d = item.get("data", {})
    creators = d.get("creators", [])
    authors = [
        c.get("lastName", c.get("name", "?"))
        for c in creators if c.get("creatorType") == "author"
    ]
    author_str = ", ".join(authors[:2]) + (" et al." if len(authors) > 2 else "")
    year = d.get("date", "")[:4]
    title = d.get("title", "?")[:70]
    return f"{author_str} ({year}). {title}"


# ── reading the profile ──────────────────────────────────────────────────────

def read_profile() -> tuple[dict, str]:
    """(frontmatter, body). Empty when the profile has never been trained.

    Sources from the neutral path, or a legacy path during transition.
    """
    path = _read_path()
    if path is None:
        return {}, ""
    text = path.read_text(encoding="utf-8", errors="replace")
    meta: dict = {}
    body = text
    if text.startswith("---"):
        end = text.find("\n---\n", 3)
        if end != -1:
            try:
                meta = yaml.safe_load(text[4:end]) or {}
            except yaml.YAMLError:
                meta = {}
            body = text[end + 5:]
    return meta, body.strip()


def load_meta() -> dict:
    return read_profile()[0]


def load_signature() -> dict:
    """The MEASURED voice: rhythm and the closed-class palettes — what style guards check."""
    return read_profile()[0].get("signature") or {}


def _exemplars(body: str) -> list[str]:
    """The author's own passages, from the ``## Voice — exemplars`` section (``> `` quotes)."""
    if "## Voice — exemplars" not in body:
        return []
    section = body.split("## Voice — exemplars", 1)[1].split("## Voice", 1)[0]
    return [ln[1:].strip() for ln in section.splitlines()
            if ln.strip().startswith(">") and len(ln.strip()) > 3]


def load_block(kind: str = "", budget: int = 3800) -> str:
    """The voice, rendered as the drafter must receive it: a measured palette + real prose.

    ``kind`` is accepted for signature parity with raconteur's richer, section-aware loader;
    this shared loader offers all exemplars as candidates.
    """
    meta, body = read_profile()
    if not meta and not body:
        return ""
    sig = meta.get("signature") or {}
    exemplars = _exemplars(body)
    analysis = (body.split("## Voice — analysis", 1)[1].strip()
                if "## Voice — analysis" in body else "")
    block = voice.style_block(sig, exemplars, analysis, budget=budget)
    return block or body[:budget]


def profile_is_current(meta: dict | None = None) -> bool:
    """Whether the profile on disk is the MEASURED, tagged format the drafters can use.

    Two things must hold: a ``signature`` (the measured palette and rhythm the style guards
    check) AND a ``## Voice — exemplars`` section (which raconteur parses for section-aware
    voice). A profile missing either — an old rabbitHole analysis-only profile, or a
    pre-rename ``Representative Excerpts`` one — parses and loads looking healthy while silently
    degraded, and the staleness check compares PAPER KEYS, which a format change leaves
    untouched, so the one profile that most needs retraining reports itself up to date.

    ``write_profile`` emits the tagged section whenever it has a signature, so every profile the
    engine writes is current — the format check only ever fires on an old or foreign one, never
    on a fresh train. That is what keeps this from becoming a new retrain-forever loop.
    """
    if meta is None:
        meta, body = read_profile()
    else:
        body = read_profile()[1]
    return bool(meta.get("signature")) and "## Voice — exemplars" in body


def needs_training(confirmed_keys: list[str] | None, require_format: bool = True) -> bool:
    """True when the profile is absent, names a paper it was never trained against, or is
    written in a format the drafters can't use.

    The subset check compares against the keys the profile was TRAINED AGAINST. ``train`` records
    every key the config REQUESTED — including keys that resolve to no Zotero item at all — so a
    dead key counts as trained-against rather than making the subset check unsatisfiable forever
    (the bug that had pydsk retraining on every run: 21 keys named, 9 resolvable, 12 vanished).
    """
    meta = load_meta()
    if not meta and _read_path() is None:
        return True
    wanted = set(confirmed_keys or [])
    if wanted and not wanted.issubset(set(meta.get("paper_keys", []))):
        return True
    if require_format and not profile_is_current(meta):
        return True
    return False


# ── writing the profile (one canonical, measured format) ─────────────────────

def write_profile(author: str, paper_keys: list[str], papers_used: list[str],
                  papers_skipped: list[str], analysis: str,
                  signature: dict | None = None,
                  exemplars: list[str] | None = None) -> Path:
    """Write the canonical profile: a MEASURED signature, passages of the real prose, and a
    short analysis, in that order of importance.

    ``paper_keys`` records every key the profile was TRAINED AGAINST — including requested keys
    with no retrievable fulltext and requested keys that resolved to no item — because that is
    what ``needs_training`` compares the config against. ``papers_used``/``papers_skipped`` say
    what actually reached the model, so nine papers producing a profile from twenty-one named
    is visible rather than mysterious.
    """
    meta = {
        "author": author,
        "last_updated": date.today().strftime("%y%m%d"),
        "paper_keys": paper_keys,
        "papers_used": papers_used,
        "papers_skipped": papers_skipped or [],
    }
    if signature:
        meta["signature"] = signature
    frontmatter = yaml.safe_dump(meta, default_flow_style=False,
                                 allow_unicode=True, sort_keys=False).strip()

    parts: list[str] = []
    # The tagged section is the marker of a current, measured profile: emit it whenever there is
    # a signature, even if this corpus yielded no exemplar paragraph, so profile_is_current can
    # never mistake a fresh train for a stale one.
    if signature:
        parts.append("## Voice — exemplars\n")
        parts.append("Passages of the author's own published prose. This is the voice.\n")
        for ex in (exemplars or []):
            parts.append(f"> {' '.join(ex.split())}\n")
    if analysis and analysis.strip():
        parts.append("## Voice — analysis\n")
        parts.append(analysis.strip() + "\n")

    content = f"---\n{frontmatter}\n---\n\n" + "\n".join(parts)
    STYLE_PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if STYLE_PROFILE_PATH.exists():
        STYLE_PROFILE_PATH.with_suffix(".md.bak").write_text(
            STYLE_PROFILE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    STYLE_PROFILE_PATH.write_text(content, encoding="utf-8")
    return STYLE_PROFILE_PATH


# ── the per-tool policy ──────────────────────────────────────────────────────

class StylePolicy(Protocol):
    """What a tool must supply for the engine to train and orchestrate. All I/O — Zotero,
    PDFs, the brain, logging, config — is the tool's; the engine owns only the invariant loop.
    """

    author: str
    requested_keys: list[str]

    def items_by_keys(self, keys: list[str]) -> list[dict]: ...
    def search_author(self, name: str) -> list[dict]: ...
    def select_items(self, items: list[dict]) -> list[dict]: ...
    def prose_for(self, item: dict, tmpdir: Path) -> str: ...
    def analyze(self, author: str, exemplars: list[str]) -> str: ...
    def save_keys(self, keys: list[str]) -> None: ...
    def log(self, msg: str) -> None: ...


# ── the invariant loop ───────────────────────────────────────────────────────

def train(policy: StylePolicy, confirmed_items: list[dict],
          requested_keys: list[str] | None) -> Path:
    """Read the author's papers, MEASURE how they write, and write the profile.

    Records as trained-against the UNION of the keys Zotero returned and the keys the config
    requested — so a key that resolved to nothing still counts, and the subset check in
    ``needs_training`` becomes satisfiable. That is the fix the per-tool copies never shared.
    """
    tmp = Path(tempfile.mkdtemp(prefix="haarpi-style-"))
    prose_parts: list[str] = []
    papers_used: list[str] = []
    papers_skipped: list[str] = []
    returned_keys: list[str] = []

    for item in confirmed_items:
        key = item.get("data", {}).get("key", "")
        if key:
            returned_keys.append(key)
        label = item_label(item)
        prose = policy.prose_for(item, tmp)
        if len(prose.split()) < 500:            # no file, image-only scan, or too little prose
            policy.log(f"[style] no usable prose in {label[:50]} — skipping")
            papers_skipped.append(label)
            continue
        prose_parts.append(prose)
        papers_used.append(label)

    if not prose_parts:
        policy.log("[error] no readable papers — cannot train a voice")
        raise SystemExit(1)

    corpus = voice._tidy("\n\n".join(prose_parts))
    sig = voice.signature(corpus, clean=False)
    exemplars = voice.pick_exemplars(corpus, n=4)
    policy.log(f"[style] measured {sig['corpus_words']:,} words from {len(papers_used)} "
               f"paper(s): {sig.get('sentence_words_mean')}-word sentences, "
               f"{len(sig.get('connectives') or {})} transitions in the palette")

    # The analysis is a nicety — the signature and the exemplars carry the voice — so a brain
    # that is busy or absent must not cost the author their profile.
    analysis = policy.analyze(policy.author, exemplars)

    trained_keys = sorted(set(returned_keys) | set(requested_keys or []))
    path = write_profile(policy.author, trained_keys, papers_used, papers_skipped,
                         analysis, signature=sig, exemplars=exemplars)
    policy.log(f"[style] wrote {path}")
    return path


def run(policy: StylePolicy) -> int:
    """Decide whether to (re)train, resolve the papers, and train. Tool-agnostic orchestration."""
    _migrate_legacy()   # a pre-consolidation profile comes to live at the neutral path
    author = policy.author
    if not author:
        print("[error] no style_author configured — run the tool's init first", file=sys.stderr)
        return 1

    meta = load_meta()
    existing_keys: set[str] = set(meta.get("paper_keys", []))
    last_updated = meta.get("last_updated", "")
    current_format = profile_is_current(meta)

    # The profile's own key list is as good a source as the project's, and better when the
    # project never recorded one — a global voice does not need re-confirming per project.
    requested = policy.requested_keys or sorted(existing_keys)
    if requested:
        new_keys = set(requested) - existing_keys
        if existing_keys and not new_keys and current_format:
            policy.log(f"[style] profile is up to date "
                       f"({len(existing_keys)} paper(s), last trained {last_updated})")
            return 0
        if existing_keys and not new_keys:
            policy.log(f"[style] no new papers, but the profile on disk predates the measured "
                       f"format — retraining from the same {len(requested)} paper(s)")
        policy.log(f"[style] fetching {len(requested)} paper(s) from Zotero…")
        confirmed = policy.items_by_keys(requested)
        if not confirmed:
            policy.log("[error] none of the requested paper keys resolved in Zotero")
            return 1
        requested_keys = requested
    else:
        policy.log(f"[style] searching Zotero for author: {author}…")
        items = policy.search_author(author)
        if not items:
            policy.log(f"[style] no papers found for '{author}' in Zotero")
            return 1
        new_keys = {i.get("data", {}).get("key", "") for i in items} - existing_keys
        if existing_keys and not new_keys and current_format:
            policy.log(f"[style] profile is up to date "
                       f"({len(existing_keys)} paper(s), last trained {last_updated})")
            return 0
        confirmed = policy.select_items(items)
        if not confirmed:
            return 0
        requested_keys = [it.get("data", {}).get("key", "") for it in confirmed
                          if it.get("data", {}).get("key")]
        policy.save_keys(requested_keys)

    policy.log(f"[style] training style for '{author}' on {len(confirmed)} paper(s)…")
    train(policy, confirmed, requested_keys)
    return 0

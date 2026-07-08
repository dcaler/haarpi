"""Style profile: train on the author's own publications, then apply during synthesis.

The profile is stored at ~/.config/raconteur/style_profile.md — the same file
raconteur uses, so a profile trained in either tool is automatically shared.
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

import yaml

from . import runlog
from .brain import Brain
from .config import GLOBAL_CONFIG_PATH, load_global, load_project, save_project

STYLE_PROFILE_PATH = Path.home() / ".config" / "raconteur" / "style_profile.md"

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


def _item_label(item: dict) -> str:
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


def _extract_prose(fulltext: str, max_chars: int = 3000) -> str:
    """Extract clean prose paragraphs, skipping references/headers."""
    paras = [p.strip() for p in re.split(r"\n{2,}", fulltext) if p.strip()]
    prose = []
    total = 0
    for p in paras:
        if len(p) < 80:
            continue
        if re.match(r"^\d+\.|^References|^Bibliography|^Abstract|^Keywords", p, re.I):
            continue
        if re.search(r"https?://|doi\.org|\[\d+\]", p):
            continue
        prose.append(p)
        total += len(p)
        if total >= max_chars:
            break
    return "\n\n".join(prose)


def load_style_profile() -> str:
    """Return the profile body (frontmatter stripped), capped at 2000 chars. Empty if absent."""
    if not STYLE_PROFILE_PATH.exists():
        return ""
    text = STYLE_PROFILE_PATH.read_text(encoding="utf-8", errors="replace")
    if text.startswith("---"):
        end = text.find("\n---\n", 3)
        if end != -1:
            text = text[end + 5:]
    text = text.strip()
    if len(text) > 2000:
        text = text[:2000] + "\n[…truncated]"
    return text


def needs_training(confirmed_keys: list[str] | None) -> bool:
    """True when the style profile is absent, or was never trained against some paper the
    project now names.

    Compares against the keys the profile was TRAINED AGAINST, not the ones that yielded
    usable prose. A paper with no PDF attachment is attempted, skipped, and recorded — if it
    were left out of `paper_keys`, this check would fail on every run and retrain the profile
    forever, refetching the whole Zotero collection each time. Run `rabbitHole style` to force
    a retrain after attaching a PDF that was previously skipped.
    """
    if not STYLE_PROFILE_PATH.exists():
        return True
    wanted = set(confirmed_keys or [])
    if not wanted:
        return False
    return not wanted.issubset(set(_load_existing_meta().get("paper_keys", [])))


def _load_existing_meta() -> dict:
    """Read YAML frontmatter from existing style_profile.md."""
    if not STYLE_PROFILE_PATH.exists():
        return {}
    text = STYLE_PROFILE_PATH.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if m:
        try:
            return yaml.safe_load(m.group(1)) or {}
        except Exception:
            pass
    return {}


def _write_profile(author: str, paper_keys: list[str], papers_used: list[str],
                   analysis: str, papers_skipped: list[str] | None = None) -> Path:
    """Write the profile.

    `paper_keys` records every key the profile was TRAINED AGAINST, including those with no
    retrievable fulltext — that is what `needs_training` compares the config against.
    `papers_used` and `papers_skipped` say what actually reached the model, so a human can
    see why nine papers produced the profile when twenty-one were named.
    """
    today = date.today().strftime("%y%m%d")
    frontmatter = yaml.safe_dump({
        "author": author,
        "last_updated": today,
        "paper_keys": paper_keys,
        "papers_used": papers_used,
        "papers_skipped": papers_skipped or [],
    }, default_flow_style=False, allow_unicode=True).strip()
    content = f"---\n{frontmatter}\n---\n\n{analysis.strip()}\n"
    STYLE_PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STYLE_PROFILE_PATH.write_text(content, encoding="utf-8")
    return STYLE_PROFILE_PATH


def fetch_and_train(gc, cfg, author_name: str, confirmed_items: list[dict]) -> Path:
    """Fetch fulltext for confirmed items, analyse style, write profile."""
    from . import zotero as _zotero
    zc = _zotero.ZoteroClient(gc)

    excerpts_parts: list[str] = []
    # Every key we CONSIDERED, not just the ones that yielded prose. `needs_training` asks
    # whether the config's style_paper_keys are a subset of what the profile was trained
    # against; recording only the usable papers made that subset check unsatisfiable, so a
    # paper with no PDF re-triggered a full retrain on every single run — forever.
    attempted_keys: list[str] = []
    papers_used: list[str] = []
    papers_skipped: list[str] = []

    for item in confirmed_items:
        key = item.get("data", {}).get("key", "")
        label = _item_label(item)
        if key:
            attempted_keys.append(key)
        print(f"  {runlog.stamp()}fetching fulltext: {label[:60]}…", flush=True)
        att_key = zc.pdf_attachment_key(key)
        text = ""
        if att_key:
            text = zc.fulltext(att_key)
        if not text:
            print(f"  [skip] no fulltext for {label[:50]}")
            papers_skipped.append(label)
            continue
        prose = _extract_prose(text)
        if not prose:
            print(f"  [skip] no usable prose in {label[:50]}")
            papers_skipped.append(label)
            continue
        excerpts_parts.append(f"--- From: {label} ---\n{prose[:1500]}")
        papers_used.append(label)

    if not excerpts_parts:
        print("[error] no fulltext retrieved — cannot train style", file=sys.stderr)
        raise SystemExit(1)

    print(f"  {runlog.stamp()}analysing style from {len(excerpts_parts)} paper(s)…",
          flush=True)
    brain = Brain(cfg.brain, gc)
    analysis = brain.coordinator(
        _ANALYZE_STYLE_PROMPT.format(
            author=author_name,
            excerpts="\n\n".join(excerpts_parts),
        ),
        system=_SYSTEM,
        num_ctx=16384,
    )

    path = _write_profile(author_name, attempted_keys, papers_used, analysis, papers_skipped)
    print(f"  wrote {path}")
    return path


def run(directory: str = ".") -> int:
    from . import zotero as _zotero
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

    author_name = cfg.style_author
    if not author_name:
        print("[error] no style_author in litrev.yaml — run 'rabbitHole init' first",
              file=sys.stderr)
        return 1

    existing = _load_existing_meta()
    existing_keys: set[str] = set(existing.get("paper_keys", []))
    last_updated = existing.get("last_updated", "")

    zc = _zotero.ZoteroClient(gc)

    # Use the paper keys confirmed during init if available; otherwise search.
    if cfg.style_paper_keys:
        confirmed_keys = cfg.style_paper_keys
        new_keys = set(confirmed_keys) - existing_keys
        if existing_keys and not new_keys:
            print(f"Style profile is up to date "
                  f"({len(existing_keys)} paper(s), last trained {last_updated})")
            return 0
        print(f"Fetching {len(confirmed_keys)} confirmed paper(s) from Zotero…", flush=True)
        confirmed = zc.items_by_keys(confirmed_keys)
        if not confirmed:
            print("[error] none of the confirmed paper keys could be retrieved from Zotero",
                  file=sys.stderr)
            return 1
    else:
        # No keys saved (e.g. Zotero wasn't available during init) — search now.
        print(f"Searching Zotero for author: {author_name}…", flush=True)
        items = zc.search_by_author(author_name)
        if not items:
            print(f"[warn] no papers found for '{author_name}' in Zotero library")
            return 1
        new_keys = {i.get("data", {}).get("key", "") for i in items} - existing_keys
        if existing_keys and not new_keys:
            print(f"Style profile is up to date "
                  f"({len(existing_keys)} paper(s), last trained {last_updated})")
            return 0
        confirmed = items

    print(f"Training style profile for '{author_name}' on {len(confirmed)} paper(s)…")
    for item in confirmed:
        print(f"  {_item_label(item)}")

    fetch_and_train(gc, cfg, author_name, confirmed)
    return 0

from __future__ import annotations
import re
import sys
from datetime import date
from pathlib import Path

import yaml

from .brain import Brain
from .config import ProjectConfig, GlobalConfig, ZoteroConfig
from .log import log
from .zotero import ZoteroClient

STYLE_PROFILE_PATH = Path("paper") / "style_profile.md"

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
5. Evidence handling — how the author introduces, contextualizes, and interprets evidence
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
    """Extract clean prose paragraphs from raw fulltext, skipping references/headers."""
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


def _load_existing_profile(project_dir: Path) -> dict:
    """Read YAML frontmatter from existing style_profile.md."""
    path = project_dir / STYLE_PROFILE_PATH
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if m:
        try:
            return yaml.safe_load(m.group(1)) or {}
        except Exception:
            pass
    return {}


def _write_profile(project_dir: Path, author: str, paper_keys: list[str],
                   papers_used: list[str], analysis: str) -> Path:
    today = date.today().strftime("%y%m%d")
    frontmatter = yaml.safe_dump({
        "author": author,
        "last_updated": today,
        "paper_keys": paper_keys,
        "papers_used": papers_used,
    }, default_flow_style=False, allow_unicode=True).strip()
    content = f"---\n{frontmatter}\n---\n\n{analysis.strip()}\n"
    path = project_dir / STYLE_PROFILE_PATH
    path.parent.mkdir(exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def fetch_and_train(project_dir: Path, cfg: ProjectConfig, gcfg: GlobalConfig,
                    author_name: str, confirmed_items: list[dict]) -> Path:
    """Fetch fulltext for confirmed items, analyze style, write profile."""
    zcfg = ZoteroConfig.from_env()
    zotero = ZoteroClient(zcfg)

    excerpts_parts: list[str] = []
    paper_keys: list[str] = []
    papers_used: list[str] = []

    for item in confirmed_items:
        key = item.get("data", {}).get("key", "")
        label = _item_label(item)
        log(f"[raconteur] fetching fulltext: {label[:60]}…")
        att_key = zotero.pdf_attachment_key(key)
        text = ""
        if att_key:
            text = zotero.fulltext(att_key)
        if not text:
            log(f"[raconteur] no fulltext for {label[:50]} — skipping")
            continue
        prose = _extract_prose(text)
        if not prose:
            log(f"[raconteur] no usable prose in {label[:50]} — skipping")
            continue
        excerpts_parts.append(f"--- From: {label} ---\n{prose[:1500]}")
        paper_keys.append(key)
        papers_used.append(label)

    zotero.close()

    if not excerpts_parts:
        log("[error] no fulltext retrieved — cannot train style")
        raise SystemExit(1)

    log(f"[raconteur] analysing style from {len(excerpts_parts)} paper(s)…")
    brain = Brain(gcfg, coordinator=cfg.brain.coordinator_model)
    analysis = brain.coordinator(
        _ANALYZE_STYLE_PROMPT.format(
            author=author_name,
            excerpts="\n\n".join(excerpts_parts),
        ),
        system=_SYSTEM,
        num_ctx=16384,
    )

    path = _write_profile(project_dir, author_name, paper_keys, papers_used, analysis)
    log(f"[raconteur] wrote {path.relative_to(project_dir)}")
    return path


def run(project_dir: Path) -> None:
    if not ProjectConfig.exists(project_dir):
        log("[error] no paper/raconteur.yaml — run 'raconteur init' first")
        raise SystemExit(1)

    cfg = ProjectConfig.load(project_dir)
    gcfg = GlobalConfig.load()

    zcfg = ZoteroConfig.from_env()
    if not zcfg.available:
        log("[error] ZOTERO_API_KEY and ZOTERO_LIBRARY_ID must be set")
        raise SystemExit(1)

    author_name = cfg.style_author
    if not author_name:
        author_name = input("Author name to search in Zotero: ").strip()
        if not author_name:
            raise SystemExit(0)

    existing = _load_existing_profile(project_dir)
    existing_keys: set[str] = set(existing.get("paper_keys", []))
    last_updated = existing.get("last_updated", "")

    log(f"[raconteur] searching Zotero for author: {author_name}…")
    zotero = ZoteroClient(zcfg)
    try:
        items = zotero.search_by_author(author_name)
    finally:
        zotero.close()

    if not items:
        log(f"[raconteur] no papers found for '{author_name}' in Zotero library")
        raise SystemExit(1)

    new_keys = {i.get("data", {}).get("key", "") for i in items} - existing_keys
    if existing_keys and not new_keys:
        log(
            f"[raconteur] style profile is up to date "
            f"({len(existing_keys)} papers, last trained {last_updated})"
        )
        return

    print(f"\nFound {len(items)} paper(s) by '{author_name}':")
    for i, item in enumerate(items, 1):
        marker = " [new]" if item.get("data", {}).get("key", "") in new_keys else ""
        print(f"  {i:2}. {_item_label(item)}{marker}")

    print()
    sel = input(
        "Confirm papers to train on (Enter = all, or comma-separated numbers to exclude): "
    ).strip()

    if sel:
        exclude = {int(x.strip()) - 1 for x in sel.split(",") if x.strip().isdigit()}
        confirmed = [item for i, item in enumerate(items) if i not in exclude]
    else:
        confirmed = items

    if not confirmed:
        log("[raconteur] no papers selected")
        raise SystemExit(0)

    print(f"\nTraining on {len(confirmed)} paper(s)…")

    fetch_and_train(project_dir, cfg, gcfg, author_name, confirmed)

    if author_name != cfg.style_author or not cfg.use_style:
        cfg.style_author = author_name
        cfg.use_style = True
        cfg.save(project_dir)
        log("[raconteur] updated raconteur.yaml: style_author + use_style")

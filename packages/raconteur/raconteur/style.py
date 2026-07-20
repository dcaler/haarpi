from __future__ import annotations
import re
import sys
from datetime import date
from pathlib import Path

import yaml

from .brain import Brain
from .config import ProjectConfig, GlobalConfig, ZoteroConfig, GLOBAL_CONFIG_PATH
from .log import log
from .zotero import ZoteroClient

STYLE_PROFILE_PATH = GLOBAL_CONFIG_PATH.parent / "style_profile.md"

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


def _load_existing_profile(project_dir: Path = None) -> dict:
    """Read YAML frontmatter from existing style_profile.md."""
    path = STYLE_PROFILE_PATH
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


def profile_is_current(meta: dict | None = None) -> bool:
    """Whether the profile on disk is in the format the drafter can actually use.

    A profile written by an older version parses, loads, and reaches the prompt looking
    healthy — and is silently degraded: with no ``signature`` the measured palette and the
    rhythm line are empty, ``_exemplars`` finds no ``## Voice — exemplars`` section so the
    per-section ``kind`` is ignored, ``style_block`` returns "" on both counts, and
    ``load_style_profile`` falls back to dumping the raw body. Every section then gets the
    same generic block and ``load_style_signature`` returns {}, so no style guard can fire.

    The staleness check upstream compares PAPER KEYS, which are unchanged by a format
    change — so the one profile that most needs retraining is the one that reports itself
    up to date. That is why this is a separate question from "are there new papers".
    """
    if meta is None:
        meta = _load_existing_profile()
    if not meta.get("signature"):
        return False
    body = STYLE_PROFILE_PATH.read_text(encoding="utf-8") if STYLE_PROFILE_PATH.exists() \
        else ""
    return "## Voice — exemplars" in body


def _write_profile(project_dir: Path, author: str, paper_keys: list[str],
                   papers_used: list[str], analysis: str,
                   signature: dict | None = None,
                   exemplars: list[str] | None = None) -> Path:
    """The profile: a MEASURED signature, passages of the real prose, and a short analysis.

    In that order of importance. The signature is countable and cannot be argued with; the
    exemplars are the voice itself; the analysis is a model's impression of both, and is the
    first thing to cut when the budget runs out — not, as it used to be, the last.
    """
    today = date.today().strftime("%y%m%d")
    meta = {
        "author": author,
        "last_updated": today,
        "paper_keys": paper_keys,
        "papers_used": papers_used,
    }
    if signature:
        meta["signature"] = signature
    frontmatter = yaml.safe_dump(meta, default_flow_style=False,
                                 allow_unicode=True, sort_keys=False).strip()

    body = []
    if exemplars:
        body.append("## Voice — exemplars\n")
        body.append("Passages of the author's own published prose. This is the voice.\n")
        for ex in exemplars:
            body.append(f"> {' '.join(ex.split())}\n")
    if analysis and analysis.strip():
        body.append("## Voice — analysis\n")
        body.append(analysis.strip() + "\n")

    content = f"---\n{frontmatter}\n---\n\n" + "\n".join(body)
    path = STYLE_PROFILE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.with_suffix(".md.bak").write_text(path.read_text(encoding="utf-8"),
                                               encoding="utf-8")
    path.write_text(content, encoding="utf-8")
    return path


def fetch_and_train(project_dir: Path, cfg: ProjectConfig, gcfg: GlobalConfig,
                    author_name: str, confirmed_items: list[dict]) -> Path:
    """Read the author's papers and MEASURE how they write.

    The PDFs themselves, not Zotero's fulltext index. The index is a flat blob — nine papers
    come back as nine paragraphs of 288 sentences each — which is fine to read and useless to
    measure, and a voice signature is nothing but measurement. rabbitHole has read the PDFs
    with PyMuPDF all along; this now does the same, and reads them by LAYOUT BLOCK, so the
    author's paragraph structure survives.
    """
    import tempfile

    from . import voice

    zotero = ZoteroClient(ZoteroConfig.from_env())
    tmp = Path(tempfile.mkdtemp(prefix="raconteur-style-"))

    prose_parts: list[str] = []
    paper_keys: list[str] = []
    papers_used: list[str] = []

    for item in confirmed_items:
        key = item.get("data", {}).get("key", "")
        label = _item_label(item)
        att_key = zotero.pdf_attachment_key(key)
        if not att_key:
            log(f"[raconteur] no PDF for {label[:50]} — skipping")
            continue
        log(f"[raconteur] reading: {label[:60]}…")
        pdf = tmp / f"{key}.pdf"
        prose = ""
        if zotero.download(att_key, pdf):
            prose = voice.pdf_prose(pdf)
        if not prose:                       # no file, or an image-only scan
            prose = voice.clean_prose(zotero.fulltext(att_key))
        if len(prose.split()) < 500:
            log(f"[raconteur] no usable prose in {label[:50]} — skipping")
            continue
        prose_parts.append(prose)
        paper_keys.append(key)
        papers_used.append(label)

    zotero.close()
    if not prose_parts:
        log("[error] no readable papers — cannot train a voice")
        raise SystemExit(1)

    corpus = voice._tidy("\n\n".join(prose_parts))
    signature = voice.signature(corpus, clean=False)
    exemplars = voice.pick_exemplars(corpus, n=4)
    log(f"[raconteur] measured {signature['corpus_words']:,} words from "
        f"{len(paper_keys)} paper(s): {signature.get('sentence_words_mean')}-word sentences, "
        f"{len(signature.get('connectives') or {})} transitions in his palette")

    # The analysis is a nicety now — the signature and the exemplars carry the voice — so a
    # brain that is busy or absent must not cost the author their profile.
    analysis = ""
    try:
        brain = Brain(gcfg, coordinator=cfg.brain.coordinator_model)
        analysis = brain.coordinator(
            _ANALYZE_STYLE_PROMPT.format(
                author=author_name,
                excerpts="\n\n".join(f"--- excerpt ---\n{e}" for e in exemplars),
            ),
            system=_SYSTEM, num_ctx=16384)
    except Exception as e:  # noqa: BLE001
        log(f"[warn] the style analysis pass failed ({e}) — the measured signature and the "
            f"exemplars are written regardless; they are what matters")

    path = _write_profile(project_dir, author_name, paper_keys, papers_used,
                          analysis, signature=signature, exemplars=exemplars)
    log(f"[raconteur] wrote {path}")
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

    existing = _load_existing_profile()
    existing_keys: set[str] = set(existing.get("paper_keys", []))
    last_updated = existing.get("last_updated", "")
    current_format = profile_is_current(existing)

    zotero = ZoteroClient(zcfg)

    # The profile's own key list is as good a source as the project's, and better when the
    # project never recorded one — a global voice does not need re-confirming per project,
    # and falling to the interactive search here is what made a trained profile look absent.
    confirmed_keys = cfg.style_paper_keys or sorted(existing_keys)
    if confirmed_keys:
        new_keys = set(confirmed_keys) - existing_keys
        if existing_keys and not new_keys and current_format:
            log(
                f"[raconteur] style profile is up to date "
                f"({len(existing_keys)} paper(s), last trained {last_updated})"
            )
            zotero.close()
            return
        if existing_keys and not new_keys:
            log(f"[raconteur] no new papers, but the profile on disk predates the measured "
                f"signature and the kind-tagged exemplars — retraining from the same "
                f"{len(confirmed_keys)} paper(s)")
        log(f"[raconteur] fetching {len(confirmed_keys)} confirmed paper(s) from Zotero…")
        confirmed = zotero.items_by_keys(confirmed_keys)
        zotero.close()
        if not confirmed:
            log("[error] none of the confirmed paper keys could be retrieved from Zotero")
            raise SystemExit(1)
    else:
        # No keys saved — do interactive search.
        log(f"[raconteur] searching Zotero for author: {author_name}…")
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
                f"({len(existing_keys)} paper(s), last trained {last_updated})"
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

        cfg.style_paper_keys = [
            it.get("data", {}).get("key", "") for it in confirmed
            if it.get("data", {}).get("key")
        ]

    log(f"[raconteur] training style on {len(confirmed)} paper(s)…")
    fetch_and_train(project_dir, cfg, gcfg, author_name, confirmed)

    if author_name != cfg.style_author or not cfg.use_style:
        cfg.style_author = author_name
        cfg.use_style = True
    cfg.save(project_dir)

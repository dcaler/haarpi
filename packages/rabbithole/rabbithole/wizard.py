"""`rabbitHole init` — short interactive interview, run from inside a project dir.

Intended use: make a project folder (e.g. `260523_SolarAdopt`), `cd` into it,
then run `rabbitHole` (init).

First run (no litrev yet):
  1. Confirm the project name (defaults to the folder name minus any leading
     datestamp, e.g. 260523_SolarAdopt -> SolarAdopt). Also the Zotero collection.
  2. "What do you want to research today?" -> saved verbatim as research_prompt.
  3. How many articles to target.
  Writes litrev.yaml. gather extracts topic/focus from the prompt on first run.

Re-run (a litrev*.yaml exists) — used to declare a new focus:
  - Project name is taken as given from the latest numbered yaml.
  - Shows the current topic + focus.
  - Asks "What do you want to research today?" (Enter keeps the previous topic/
    focus; a new answer is stored verbatim and topic/focus cleared so gather
    re-derives them).
  - Re-asks the target (defaulting to the previous value).
  - Everything else (sources, dates, brain, ranking, collection_key, ...) carries
    over. Writes the next numbered file (litrev_2.yaml, litrev_3.yaml, ...),
    leaving older ones as history. gather/report always use the latest.
"""

from __future__ import annotations

import re
from pathlib import Path

from . import config
from .config import ProjectConfig


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        ans = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        ans = ""
    return ans or default


def _ask_int(prompt: str, default: int) -> int:
    while True:
        raw = _ask(prompt, str(default))
        try:
            return int(raw)
        except ValueError:
            print("  Please enter a number.")


def _ask_yesno(prompt: str, default: bool = True) -> bool:
    d = "Y/n" if default else "y/N"
    raw = _ask(f"{prompt} ({d})").lower()
    if not raw:
        return default
    return raw.startswith("y")


def _default_name(dirname: str) -> str:
    """Strip a leading datestamp like '260523_' from the folder name."""
    stripped = re.sub(r"^\d+[_-]+", "", dirname).strip()
    return stripped or dirname


def _ask_source_types(default_pre: bool = False, default_news: bool = False) -> tuple[bool, bool]:
    """4-way source-type question -> (include_preprints, include_news)."""
    modes = {(False, False): "1", (True, False): "2", (False, True): "3", (True, True): "4"}
    default = modes[(default_pre, default_news)]
    print("\nWhat kinds of sources should I include?")
    print("  1. Peer-reviewed only (journal articles)")
    print("  2. Peer-reviewed + preprints (arXiv, working papers)")
    print("  3. Peer-reviewed + news / trade press")
    print("  4. Peer-reviewed + preprints + news")
    choice = _ask("Choose 1-4", default).strip()
    return {"1": (False, False), "2": (True, False),
            "3": (False, True), "4": (True, True)}.get(choice, (default_pre, default_news))


def run(directory: str = ".") -> int:
    root = Path(directory).resolve()
    root.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print(" rabbitHole — research project")
    print("=" * 60)
    print(f" Folder: {root}")
    print()

    if config.latest_project_file(root) is not None:
        return _rerun(root)
    return _first_run(root)


def _first_run(root: Path) -> int:
    # Ask every question first; the LLM parse happens once, after the interview.
    from haarpi.project import header_defaults
    hdr = header_defaults(root)
    default_name = hdr.get("name") or _default_name(root.name)
    name = _ask(f'Do you want to call this project "{default_name}" or something else?',
                default_name)
    project_name = name.strip() or default_name

    print()
    research = _ask("What do you want to research today?", hdr.get("brief", ""))
    while not research:
        print("  Tell me, in a sentence or two, what you want to look into.")
        research = _ask("What do you want to research today?")

    print()
    target = _ask_int("How many articles do you want to target?", 30)
    include_preprints, include_news = _ask_source_types()

    cfg = ProjectConfig(
        project_name=project_name,
        research_prompt=research,
        include_preprints=include_preprints,
        include_news=include_news,
        target_min=target,
        target_max=target,
    )
    config.project_paths(root).ensure()
    saved = config.save_project(cfg, root)
    print()
    print(f"Project '{project_name}' set up in {root}")
    print(f"  Wrote {saved.relative_to(root)}")
    print(f"  Created: {config.LITREVIEW_DIR}/ (pdfs, work, output)")
    print(f"  Zotero collection: {project_name}")
    _check_style(cfg)
    config.save_project(cfg, root)
    return _finalize(root, cfg)


def _rerun(root: Path) -> int:
    prev = config.load_project(root)
    latest = config.latest_project_file(root)
    print(f"Existing project: {prev.project_name}  (latest: {latest.name})")
    print(f"  Current topic: {prev.topic}")
    print(f"  Current focus: {prev.focus or '(none)'}")
    print()

    research = _ask("What do you want to research today? (Enter = keep the above)")

    print()
    target = _ask_int("How many articles do you want to target?", prev.target_max)
    include_preprints, include_news = _ask_source_types(
        getattr(prev, "include_preprints", False), getattr(prev, "include_news", False))

    if research:
        # New prompt: store it verbatim; clear extracted fields so gather re-derives them.
        prev.research_prompt = research
        prev.topic = ""
        prev.focus = ""
        prev.domain_anchor = ""
        prev.exclude_topics = ""
    else:
        print("\n  Keeping the previous topic and focus.")

    prev.include_preprints = include_preprints
    prev.include_news = include_news
    prev.target_min = target
    prev.target_max = target

    config.project_paths(root).ensure()
    target_fp = config.next_project_file(root)
    saved = config.save_project_to(prev, target_fp)
    print()
    print(f"New focus saved for '{prev.project_name}'.")
    print(f"  Wrote {saved.relative_to(root)}")
    print(f"  Zotero collection: {prev.project_name}")
    _check_style(prev)
    config.save_project_to(prev, target_fp)
    return _finalize(root, prev)


def _check_style(cfg: ProjectConfig) -> None:
    """Collect style preferences and confirm the Zotero publication list.

    The actual LLM training happens headlessly in 'rabbitHole style'; this step
    handles everything interactive: profile check, author name, Zotero search,
    and paper selection. Confirmed Zotero keys are saved to litrev.yaml so
    'rabbitHole style' can run without prompts.
    """
    from .style import STYLE_PROFILE_PATH, _load_existing_meta

    print()

    # If a profile already exists, just ask whether to use it.
    if STYLE_PROFILE_PATH.exists():
        existing = _load_existing_meta()
        author = existing.get("author", "unknown")
        n = len(existing.get("paper_keys", []))
        last = existing.get("last_updated", "?")
        print(f"  Style profile found: {author}, {n} paper(s), last trained {last}")
        cfg.use_style = _ask_yesno("Apply this author style when writing the review?",
                                   default=True)
        if cfg.use_style:
            cfg.style_author = cfg.style_author or author
        return

    if not _ask_yesno("Apply an author style profile when writing the review?", default=False):
        return

    # Need Zotero to find publications.
    from .config import load_global
    gc = load_global()
    if not gc.have_zotero:
        cfg.style_author = _ask("Author name", cfg.style_author)
        cfg.use_style = True
        print("  (Zotero not configured — run 'rabbitHole style' after setting it up.)")
        return

    author_name = _ask("Author name to search in Zotero", cfg.style_author)
    if not author_name:
        return

    print(f"\n  Searching Zotero for '{author_name}'…", flush=True)
    from . import zotero as _zotero
    zc = _zotero.ZoteroClient(gc)
    items = zc.search_by_author(author_name)

    if not items:
        print(f"  No papers found for '{author_name}' in Zotero.")
        cfg.style_author = author_name
        cfg.use_style = True
        print("  Run 'rabbitHole style' after adding publications to Zotero.")
        return

    from .style import _item_label
    print(f"\n  Found {len(items)} paper(s) by '{author_name}':")
    for i, item in enumerate(items, 1):
        print(f"    {i:2}. {_item_label(item)}")

    print()
    sel = input(
        "  Select papers to train on (Enter = all, or comma-separated numbers to exclude): "
    ).strip()
    if sel:
        exclude = {int(x.strip()) - 1 for x in sel.split(",") if x.strip().isdigit()}
        confirmed = [item for i, item in enumerate(items) if i not in exclude]
    else:
        confirmed = items

    if not confirmed:
        print("  No papers selected — skipping style.")
        return

    cfg.style_author = author_name
    cfg.use_style = True
    cfg.style_paper_keys = [it.get("data", {}).get("key", "") for it in confirmed
                             if it.get("data", {}).get("key")]
    print(f"  {len(cfg.style_paper_keys)} paper(s) confirmed. "
          "Run 'rabbitHole style' to train the profile.")


def _finalize(root: Path, cfg: ProjectConfig) -> int:
    from . import notify
    notify.send_email(
        f"rabbitHole: project '{cfg.project_name}' initialized",
        (f"Project '{cfg.project_name}' is set up at {root}.\n\n"
         "Interview summary\n"
         f"  Request: {cfg.research_prompt or '(kept previous topic/focus)'}\n"
         f"  Topic:   {cfg.topic or '(to be extracted by gather)'}\n"
         f"  Focus:   {cfg.focus or '(none)'}\n"
         f"  Target:  {cfg.target_max} articles\n\n"
         "Next: run `rabbitHole gather` to list sources missing from your "
         "Zotero collection."),
        config.load_global(),
    )
    _print_next_steps()
    return 0


def _print_next_steps() -> None:
    print()
    print("Next (from this folder):")
    print("  rabbitHole gather   # find & curate the literature missing from your Zotero collection")
    print("  (then download the PDFs and add them to the Zotero collection)")
    print("  rabbitHole report   # read the Zotero corpus and write the review")

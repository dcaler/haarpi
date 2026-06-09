"""`rabbitHole init` — short interactive interview, run from inside a project dir.

Intended use: make a project folder (e.g. `260523_SolarAdopt`), `cd` into it,
then run `rabbitHole` (init).

First run (no litrev yet):
  1. Confirm the project name (defaults to the folder name minus any leading
     datestamp, e.g. 260523_SolarAdopt -> SolarAdopt). Also the Zotero collection.
  2. "What do you want to research today?" -> a local LLM extracts topic + focus.
  3. How many articles to target.
  Writes litrev.yaml.

Re-run (a litrev*.yaml exists) — used to declare a new focus:
  - Project name is taken as given from the latest numbered yaml.
  - Shows the current topic + focus.
  - Asks "What do you want to research today?" (Enter keeps the previous topic/
    focus; otherwise the answer is re-parsed by the LLM).
  - Re-asks the target (defaulting to the previous value).
  - Everything else (sources, dates, brain, ranking, collection_key, ...) carries
    over. Writes the next numbered file (litrev_2.yaml, litrev_3.yaml, ...),
    leaving older ones as history. gather/report always use the latest.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config
from .brain import Brain
from .config import BrainConfig, ProjectConfig


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


def _parse_json(raw: str) -> dict:
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return {}


_EXTRACT_SYS = """\
You turn a researcher's free-form description into structured fields for an
academic literature search. Respond with ONLY a JSON object, no other text:

{"topic": "...", "focus": "...", "domain_anchor": "...", "exclude_topics": "..."}

- topic: a concise, search-friendly statement of the core subject (one line).
- focus: key subtopics, disciplines, or angles to emphasise (one line; "" if none).
- domain_anchor: one line naming what a paper MUST be about to count as on-topic
  (the specific field/phenomenon), used to filter out adjacent-but-wrong results.
- exclude_topics: one line naming adjacent disciplines or topics to keep OUT, e.g.
  "general health behavior change" for a recycling project ("" if none come to mind).
Base it strictly on what the user wrote; do not invent scope."""


def _extract_topic_focus(research: str, gc) -> tuple[str, str, str, str, str]:
    """Coordinator model -> (topic, focus, domain_anchor, exclude_topics, note).
    Runs in a background thread, so it must not print or read input; any warning
    is returned as `note`."""
    try:
        brain = Brain(BrainConfig(), gc)
        raw = brain.coordinator(research, _EXTRACT_SYS, num_ctx=4096)
        data = _parse_json(raw)
        topic = (data.get("topic") or "").strip()
        focus = (data.get("focus") or "").strip()
        anchor = (data.get("domain_anchor") or "").strip()
        exclude = (data.get("exclude_topics") or "").strip()
        if topic:
            return topic, focus, anchor, exclude, ""
        return (research.strip(), "", "", "",
                "the LLM didn't return a clear topic; using your text as the topic.")
    except Exception as e:  # noqa: BLE001
        return (research.strip(), "", "", "",
                f"couldn't reach the local LLM ({e}); using your text as the topic.")


def _kickoff_extract(research: str):
    """Start topic/focus extraction in the background; returns (pool, future)."""
    gc = config.load_global()
    pool = ThreadPoolExecutor(max_workers=1)
    fut = pool.submit(_extract_topic_focus, research, gc)
    return pool, fut


def _finish_extract(pool, fut) -> tuple[str, str, str, str]:
    """Block on the background extraction (the one pause) and show the result."""
    print("\nParsing your request with the local LLM...")
    topic, focus, anchor, exclude, note = fut.result()
    pool.shutdown(wait=False)
    if note:
        print(f"  [note] {note}")
    print(f"  Topic: {topic}")
    print(f"  Focus: {focus or '(none)'}")
    print(f"  Must be about: {anchor or '(the topic)'}")
    print(f"  Keep out: {exclude or '(nothing specific)'}")
    return topic, focus, anchor, exclude


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
    default_name = _default_name(root.name)
    name = _ask(f'Do you want to call this project "{default_name}" or something else?',
                default_name)
    project_name = name.strip() or default_name

    print()
    research = _ask("What do you want to research today?")
    while not research:
        print("  Tell me, in a sentence or two, what you want to look into.")
        research = _ask("What do you want to research today?")
    pool, fut = _kickoff_extract(research)   # runs while you answer the rest

    print()
    target = _ask_int("How many articles do you want to target?", 30)
    include_preprints, include_news = _ask_source_types()
    run_gather = _ask_yesno("Run gather now?", True)

    topic, focus, anchor, exclude = _finish_extract(pool, fut)

    cfg = ProjectConfig(
        project_name=project_name,
        topic=topic,
        focus=focus,
        domain_anchor=anchor,
        exclude_topics=exclude,
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
    return _finalize(root, cfg, run_gather, research)


def _rerun(root: Path) -> int:
    prev = config.load_project(root)
    latest = config.latest_project_file(root)
    print(f"Existing project: {prev.project_name}  (latest: {latest.name})")
    print(f"  Current topic: {prev.topic}")
    print(f"  Current focus: {prev.focus or '(none)'}")
    print()

    research = _ask("What do you want to research today? (Enter = keep the above)")
    pool = fut = None
    if research:
        pool, fut = _kickoff_extract(research)   # runs while you answer the rest

    print()
    target = _ask_int("How many articles do you want to target?", prev.target_max)
    include_preprints, include_news = _ask_source_types(
        getattr(prev, "include_preprints", False), getattr(prev, "include_news", False))
    run_gather = _ask_yesno("Run gather now?", True)

    if fut is not None:
        topic, focus, anchor, exclude = _finish_extract(pool, fut)
    else:
        print("\n  Keeping the previous topic and focus.")
        topic, focus = prev.topic, prev.focus
        anchor, exclude = prev.domain_anchor, prev.exclude_topics

    # Carry everything else over; change only topic/focus/target/source-types.
    prev.topic = topic
    prev.focus = focus
    prev.domain_anchor = anchor
    prev.exclude_topics = exclude
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
    return _finalize(root, prev, run_gather, research)


def _finalize(root: Path, cfg: ProjectConfig, run_gather: bool, research: str = "") -> int:
    """Always send the init interview summary; then chain into gather if asked,
    otherwise print next steps."""
    closing = ("Running gather now — you'll get a second email when it completes."
               if run_gather else
               "Next: run `rabbitHole gather` to list sources missing from your "
               "Zotero collection.")
    from . import notify
    notify.send_email(
        f"rabbitHole: project '{cfg.project_name}' initialized",
        (f"Project '{cfg.project_name}' is set up at {root}.\n\n"
         "Interview summary\n"
         f"  Request: {research or '(kept previous topic/focus)'}\n"
         f"  Topic:   {cfg.topic}\n"
         f"  Focus:   {cfg.focus or '(none)'}\n"
         f"  Target:  {cfg.target_max} articles\n\n"
         + closing),
        config.load_global(),
    )

    if run_gather:
        print()
        from . import discover
        return discover.run(str(root))   # gather sends its own completion email

    _print_next_steps()
    return 0


def _print_next_steps() -> None:
    print()
    print("Next (from this folder):")
    print("  rabbitHole gather   # find & curate the literature missing from your Zotero collection")
    print("  (then download the PDFs and add them to the Zotero collection)")
    print("  rabbitHole report   # read the Zotero corpus and write the review")

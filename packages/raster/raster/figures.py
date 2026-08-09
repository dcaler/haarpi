"""raster's figure onto the shared chain-named pool (haarpi.figure).

The build stage produces one deterministic figure: the **module graph** from `code/designdocs/
tasks.yaml` — the sequence of build modules and their task counts. Emitted at `handoff`, alongside
the methods digest, onto the project's `figures/` pool for the paper (and the deck) to resolve by id.
No model in the loop — the figure IS the build spec.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from haarpi import figure
from haarpi import project as hproject


def short_title(root: Path, fallback: str) -> str:
    try:
        return hproject.load_manifest(root).short_title or fallback
    except Exception:
        return fallback


def emit_module_graph(root: Path, short: str) -> dict | None:
    """Render the module graph from tasks.yaml onto the pool. No-op if there are no modules yet."""
    tasks_p = root / "code" / "designdocs" / "tasks.yaml"
    if not tasks_p.is_file():
        return None
    spec = yaml.safe_load(tasks_p.read_text()) or {}
    if not spec.get("modules"):
        return None
    return figure.write_figure(root, short, figure.module_graph(spec))

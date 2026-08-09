"""rayleigh's use of the shared figure engine (haarpi.figure).

Two figures come out of rayleigh, both onto the project's chain-named pool (`<root>/figures/`):

 - the DETERMINISTIC **experiment DAG**, from the executable `experiments.yaml` (`rayleigh plan`) —
   no model at all;
 - the CONCEPTUAL **analytical-framework schematic** (`rayleigh init`) — authored as Graphviz DOT by
   the strong Claude design session (rayleigh has no ollama brain of its own), then rendered onto the
   chain. So rayleigh uses the engine's deterministic + render/naming paths, not `compose`.

Consumers (raconteur, razzle) resolve these by id from the same pool.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from haarpi import figure
from haarpi import project as hproject


def short_title(root: Path, fallback: str) -> str:
    """The project's chain short_title (so raconteur/razzle find figures by the same key)."""
    try:
        return hproject.load_manifest(root).short_title or fallback
    except Exception:
        return fallback


def emit_experiment_dag(root: Path, short: str) -> dict | None:
    """Render the experiment DAG from the executable spec onto the pool. Deterministic — no model."""
    spec_path = root / "results" / "designdocs" / "experiments.yaml"
    if not spec_path.is_file():
        return None
    exps = (yaml.safe_load(spec_path.read_text()) or {}).get("experiments") or []
    if not exps:
        return None
    return figure.write_figure(root, short, figure.experiment_dag(exps))


def chain_authored_dot(root: Path, short: str, fig_id: str, dot_path: Path, caption: str,
                       kind: str = "schematic") -> dict | None:
    """Take a DOT figure the design SESSION authored and render+chain it onto the pool as a conceptual
    figure. rayleigh doesn't compose it — Claude did, in-session; the engine renders + versions it."""
    if not dot_path.is_file() or not dot_path.read_text().strip():
        return None
    spec = figure.FigureSpec(
        id=fig_id, kind=kind, format="dot", source=dot_path.read_text(), caption=caption,
        provenance={"mode": "conceptual", "author": "design-session", "from": dot_path.name})
    return figure.write_figure(root, short, spec)

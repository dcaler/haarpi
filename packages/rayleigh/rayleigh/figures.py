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


# Both moved to haarpi.figure when ramus split off — it needs them and must not import
# rayleigh. Re-exported so this module's callers are unchanged.
short_title = figure.project_short_title
chain_authored_dot = figure.chain_authored_dot


def emit_experiment_dag(root: Path, short: str) -> dict | None:
    """Render the experiment DAG from the executable spec onto the pool. Deterministic — no model."""
    spec_path = root / "results" / "designdocs" / "experiments.yaml"
    if not spec_path.is_file():
        return None
    exps = (yaml.safe_load(spec_path.read_text()) or {}).get("experiments") or []
    if not exps:
        return None
    return figure.write_figure(root, short, figure.experiment_dag(exps))

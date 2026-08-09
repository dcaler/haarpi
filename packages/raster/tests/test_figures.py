"""raster's module-graph figure onto the shared chain-named pool (haarpi.figure).

Deterministic — the build's module structure from tasks.yaml, emitted at handoff for the paper/deck.
"""

from __future__ import annotations

import yaml

from haarpi import naming
from raster import figures as rfig


def test_emit_module_graph_from_tasks_yaml(tmp_path):
    dd = tmp_path / "code" / "designdocs"
    dd.mkdir(parents=True)
    (dd / "tasks.yaml").write_text(yaml.safe_dump({"modules": [
        {"id": "M0", "name": "core", "tasks": [{"id": "t1"}]},
        {"id": "M1", "name": "dynamics", "tasks": [{"id": "t2"}, {"id": "t3"}]}]}))

    out = rfig.emit_module_graph(tmp_path, "demo")
    assert out is not None
    src = list((tmp_path / "figures").glob("*_demo_moduleGraph_ra.dot"))
    assert len(src) == 1
    ds, chain, ext = naming.parse(src[0], "demo")
    assert chain == ["moduleGraph", "ra"]
    assert '"M0" -> "M1"' in src[0].read_text()


def test_emit_module_graph_noop_without_modules(tmp_path):
    assert rfig.emit_module_graph(tmp_path, "demo") is None       # no tasks.yaml
    dd = tmp_path / "code" / "designdocs"
    dd.mkdir(parents=True)
    (dd / "tasks.yaml").write_text(yaml.safe_dump({"modules": []}))
    assert rfig.emit_module_graph(tmp_path, "demo") is None       # empty

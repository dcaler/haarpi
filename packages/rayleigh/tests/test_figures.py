"""rayleigh's figures onto the shared chain-named pool (haarpi.figure).

The experiment DAG is deterministic (from experiments.yaml); the framework schematic is authored by
the design session as DOT and rendered onto the chain. Both land in `<root>/figures/` for the paper
and the deck to resolve by id.
"""

from __future__ import annotations

import shutil

import pytest
import yaml

from haarpi import figure, naming
from rayleigh import figures as rfigures

_HAS_DOT = shutil.which("dot") is not None


def test_emit_experiment_dag_from_the_executable_spec(tmp_path):
    dd = tmp_path / "results" / "designdocs"
    dd.mkdir(parents=True)
    (dd / "experiments.yaml").write_text(yaml.safe_dump({"experiments": [
        {"id": "E1", "title": "price sweep", "outputs": [{"kind": "figure", "caption": "curve"}]},
        {"id": "E2", "question": "does it bind?", "outputs": []}]}))

    out = rfigures.emit_experiment_dag(tmp_path, "demo")
    assert out is not None
    src = list((tmp_path / "figures").glob("*_demo_experimentDag_ra.dot"))
    assert len(src) == 1
    ds, chain, ext = naming.parse(src[0], "demo")
    assert chain == ["experimentDag", "ra"]
    assert "E1" in src[0].read_text() and "does it bind?" in src[0].read_text()
    if _HAS_DOT:
        assert out["svg"] is not None and "<svg" in out["svg"].read_text()


def test_emit_experiment_dag_is_a_noop_without_experiments(tmp_path):
    assert rfigures.emit_experiment_dag(tmp_path, "demo") is None      # no experiments.yaml
    dd = tmp_path / "results" / "designdocs"
    dd.mkdir(parents=True)
    (dd / "experiments.yaml").write_text(yaml.safe_dump({"experiments": []}))
    assert rfigures.emit_experiment_dag(tmp_path, "demo") is None      # empty


def test_chain_authored_framework_dot(tmp_path):
    dd = tmp_path / "design" / "designdocs"
    dd.mkdir(parents=True)
    (dd / "framework.dot").write_text("digraph fw { rankdir=LR; Q -> A -> D; }")

    out = rfigures.chain_authored_dot(tmp_path, "demo", "analyticalFramework",
                                      dd / "framework.dot", "The analytical framework.")
    assert out is not None
    src = list((tmp_path / "figures").glob("*_demo_analyticalFramework_ra.dot"))
    assert len(src) == 1 and "Q -> A -> D" in src[0].read_text()
    # a conceptual figure the session authored — provenance says so
    got = figure.resolve(tmp_path, "demo", "analyticalFramework", "source")
    assert got is not None
    if _HAS_DOT:
        assert out["svg"] is not None


def test_chain_authored_is_a_noop_without_the_dot(tmp_path):
    assert rfigures.chain_authored_dot(tmp_path, "demo", "analyticalFramework",
                                       tmp_path / "nope.dot", "cap") is None

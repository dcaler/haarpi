"""The bake-off's scoring code: deterministic, so the models are the only thing that varies."""

from __future__ import annotations

import json

from haarpi import bench


def test_auc_separates_and_ties_count_half():
    assert bench._auc([0.9, 0.8, 0.2, 0.1], [1, 1, 0, 0]) == 1.0
    assert bench._auc([0.5, 0.5], [1, 0]) == 0.5
    assert bench._auc([0.1, 0.9], [1, 0]) == 0.0
    assert bench._auc([0.3], [1]) is None


def test_agreement_is_per_topic_score_with_a_tolerance():
    a = {"f1": [3, 0], "f2": [2, 1]}
    b = {"f1": [2, 0], "f2": [2, 3]}
    assert bench._agree(a, b, 0) == 50.0 and bench._agree(a, b, 1) == 75.0


def test_the_hand_labels_match_the_sample_size():
    assert len(bench._E1_LABELS) == 30 and set(bench._E1_LABELS) == {0, 1}


def test_every_dispute_case_has_both_sentences():
    assert set(bench._B3_FAITHFUL) == set(bench._B3_MISSTATED)


def test_the_report_reads_whatever_results_exist(tmp_path, monkeypatch):
    monkeypatch.setattr(bench, "BENCH_ROOT", tmp_path)
    (tmp_path / "results").mkdir()
    (tmp_path / "results" / "coordinator_m.json").write_text(json.dumps({
        "label": "coordinator_m", "model": "m", "A1": {"verbatim_pct": 91.0},
        "B": {"b1_pct": 80.0, "misses": ["'thin': sources → edit"]},
        "C1": {"error": "RuntimeError: boom"}}))
    html = bench.report().read_text()
    assert "<code>m</code>" in html and "91.0" in html and "RuntimeError" in html
    assert "thin" in html

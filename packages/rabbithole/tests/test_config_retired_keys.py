"""A project file carrying a retired field still loads (DigiPros' methodsreview.yaml, 2026-11-13)."""
from rabbithole import config


def test_a_retired_field_is_dropped_not_fatal(tmp_path):
    (tmp_path / "litrev.yaml").write_text("project_name: P\ntopic: t\nfact_prompt: p0\nfact_think: false\n")
    cfg = config.load_project(str(tmp_path / "litrev.yaml"))
    assert cfg.project_name == "P" and not hasattr(cfg, "fact_prompt") and not hasattr(cfg, "fact_think")

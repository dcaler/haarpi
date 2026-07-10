"""Unified-config precedence: legacy < unified shared < [tools.<tool>]."""

from haarpi import config as hc


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_defaults_when_nothing_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert hc.merged_config("raster") == {}


def test_first_run_writes_template_once(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    p = hc.write_default_unified()
    assert p.exists()
    marker = "already here"
    p.write_text(marker)
    assert hc.write_default_unified().read_text() == marker  # never clobbers


def test_unified_wins_over_legacy_and_tool_section_wins_over_both(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    _write(hc.unified_path(), """
[ollama]
url = "http://unified:11434"
coordinator = "shared-model"

[tools.raster.ollama]
coordinator = "raster-model"
""")
    legacy = {"ollama": {"url": "http://legacy:11434", "worker": "legacy-worker"}}

    merged = hc.merged_config("raster", legacy)
    assert merged["ollama"]["url"] == "http://unified:11434"        # unified beats legacy
    assert merged["ollama"]["worker"] == "legacy-worker"            # legacy fills gaps
    assert merged["ollama"]["coordinator"] == "raster-model"        # tool section beats shared

    # another tool sees the shared value, not raster's override
    assert hc.merged_config("rayleigh", {})["ollama"]["coordinator"] == "shared-model"
    assert "tools" not in merged


def test_template_parses_and_covers_the_schema(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    hc.write_default_unified()
    data = hc.load_toml(hc.unified_path())
    for section in ("ollama", "anthropic", "zotero", "notify", "trundlr", "git", "author"):
        assert section in data, section
    assert data["git"]["co_authorship"] is False

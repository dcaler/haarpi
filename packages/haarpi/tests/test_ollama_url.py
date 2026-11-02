"""Every agent's model calls follow OLLAMA_URL: a GPU-pool runner lane points it at its card."""

from pathlib import Path

import haarpi
from haarpi.brain import DEFAULT_OLLAMA_URL, ollama_url


def test_the_environment_wins_then_the_config_then_localhost(monkeypatch):
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    assert ollama_url() == DEFAULT_OLLAMA_URL
    assert ollama_url({"url": "http://card1:11434"}) == "http://card1:11434"
    monkeypatch.setenv("OLLAMA_URL", "http://lane0:11434")
    assert ollama_url({"url": "http://card1:11434"}) == "http://lane0:11434"


def test_the_planner_classifies_against_the_lanes_server(monkeypatch):
    from haarpi import brain, planner
    seen = []

    class _B:
        def __init__(self, url, *a, **k):
            seen.append(url)

        def coordinator(self, *a, **k):
            return "{}"
    monkeypatch.setattr(brain, "Brain", _B)
    monkeypatch.setenv("OLLAMA_URL", "http://lane1:11434")
    planner.decompose([{"text": "add more on X"}], {"ollama": {"url": "http://cfg:11434"}})
    assert seen == ["http://lane1:11434"]


def test_no_agent_that_builds_a_haarpi_brain_hardcodes_the_server():
    pkgs = Path(haarpi.__file__).resolve().parents[2]
    for f in ("haarpi/haarpi/planner.py", "razzle/razzle/cli.py"):
        assert "localhost:11434" not in (pkgs / f).read_text(), f

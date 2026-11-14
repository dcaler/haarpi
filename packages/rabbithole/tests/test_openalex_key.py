"""rabbitHole sends its OpenAlex key, and stops when OpenAlex's daily budget is spent (2026-11-14).

Without a key a machine shares 1,000 credits a day with its whole IP (a search costs 10); the
DigiPros methods bench spent that in one dry run, and every search after it came back empty,
which read as "nothing found". The key goes in a header, never in a URL, so no logged error
can carry it.
"""
from __future__ import annotations

import httpx
import pytest

from rabbithole import config, sources

KEY = "sekrit-test-key-0123456"


@pytest.fixture
def net(monkeypatch):
    seen: list[httpx.Request] = []
    reply = {"status": 200, "body": {"results": []}, "headers": {}}

    def handle(req):
        seen.append(req)
        return httpx.Response(reply["status"], json=reply["body"], headers=reply["headers"])
    monkeypatch.setattr(sources, "_TRANSPORT", httpx.MockTransport(handle))
    monkeypatch.setattr(sources, "_KEY", [KEY])
    return seen, reply


def test_the_key_is_read_from_the_config_and_the_environment_wins(tmp_path, monkeypatch):
    (tmp_path / "config.toml").write_text('[openalex]\napi_key = "from-file"\n')
    monkeypatch.setattr(config.haarpi_config, "unified_path", lambda: tmp_path / "config.toml")
    monkeypatch.setattr(config, "_legacy_normalized", lambda: {})
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    assert config.load_global().openalex_api_key == "from-file"
    monkeypatch.setenv("OPENALEX_API_KEY", "from-env")
    assert config.load_global().openalex_api_key == "from-env"


def test_openalex_gets_the_key_as_a_header_and_never_in_the_url(net):
    seen, _ = net
    sources.search_openalex("block bootstrap", 5, "")
    sources.openalex_snowball(["10.1/x"], "")
    sources.resolve_by_doi("10.1/x", "")
    assert seen and all(r.headers["authorization"] == f"Bearer {KEY}" for r in seen)
    assert not any(KEY in str(r.url) for r in seen)


def test_no_other_service_is_sent_the_key(net):
    seen, _ = net
    with sources._client("") as c:
        c.get("https://api.crossref.org/works")
    assert "authorization" not in seen[-1].headers


def test_without_a_key_no_header_is_sent(net, monkeypatch):
    seen, _ = net
    monkeypatch.setattr(sources, "_KEY", [""])
    sources.search_openalex("block bootstrap", 5, "")
    assert "authorization" not in seen[-1].headers


def test_a_spent_budget_stops_the_run_instead_of_reading_as_nothing_found(net):
    _, reply = net
    reply.update(status=429, headers={"retry-after": "31387"},
                 body={"error": "Insufficient budget", "message": "free daily budget ..."})
    for call in (lambda: sources.search_openalex("q", 5, ""),
                 lambda: sources.openalex_snowball(["10.1/x"], ""),
                 lambda: sources.resolve_by_doi("10.1/x", "")):
        with pytest.raises(sources.OpenAlexBudgetSpent) as e:
            call()
        assert "budget is used up" in str(e.value) and "8 h" in str(e.value)
        assert KEY not in str(e.value)


def test_an_ordinary_429_is_still_just_a_failed_call(net):
    _, reply = net
    reply.update(status=429, body={"error": "Too many requests"})
    assert sources.search_openalex("q", 5, "") == []

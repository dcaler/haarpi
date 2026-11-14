"""raconteur's venue lookup sends the shared OpenAlex key as a header, to OpenAlex only, and says
when the daily budget is spent rather than reporting the journal unknown (2026-11-14)."""
from __future__ import annotations

import httpx

from raconteur import web

KEY = "sekrit-test-key-0123456"


def _net(monkeypatch, status=200, body=None):
    seen = []
    real = httpx.Client

    def client(*a, **kw):
        def handle(req):
            seen.append(req)
            return httpx.Response(status, json=body if body is not None else {"results": []})
        return real(*a, transport=httpx.MockTransport(handle), **kw)
    monkeypatch.setattr(web.httpx, "Client", client)
    monkeypatch.setenv("OPENALEX_API_KEY", KEY)
    return seen


def test_the_key_goes_to_openalex_as_a_header_never_in_the_url(monkeypatch):
    seen = _net(monkeypatch)
    web.openalex_source("Journal of Econometrics", "")
    assert seen[0].headers["authorization"] == f"Bearer {KEY}" and KEY not in str(seen[0].url)


def test_no_key_no_header(monkeypatch):
    seen = _net(monkeypatch)
    monkeypatch.delenv("OPENALEX_API_KEY")
    monkeypatch.setattr(web, "openalex_headers", lambda: {})
    web.openalex_source("Journal of Econometrics", "")
    assert "authorization" not in seen[0].headers


def test_a_spent_budget_is_said_not_read_as_an_unknown_journal(monkeypatch, capsys):
    _net(monkeypatch, 429, {"error": "Insufficient budget"})
    assert web.openalex_source("Journal of Econometrics", "") == {}
    err = capsys.readouterr().err
    assert "daily budget is used up" in err and KEY not in err

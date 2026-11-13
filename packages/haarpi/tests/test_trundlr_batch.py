"""One re-flow for many writes (trundlr's `skip_reflow`, 2026-11-13).

trundlr re-places the whole board after every create, update and delete. HAARPi queues a
chain or a grounding graph of up to ~20 tasks at once, so inside `client.batch()` each write
skips the re-flow and the block asks for one when it closes.
"""
from __future__ import annotations

import pytest

from haarpi import trundlr


@pytest.fixture
def calls(monkeypatch):
    class Seen(list):
        pass
    seen = Seen()

    def fake(api_url, method, path, body=None, timeout=30):
        seen.append((method, path))
        if path.startswith("/schedule/reflow") and getattr(fake, "fail", False):
            raise trundlr.TrundlrError("POST reflow -> HTTP 500")
        return {"id": len(seen)}
    monkeypatch.setattr(trundlr, "_api", fake)
    seen.fake = fake
    return seen


def test_outside_a_batch_every_write_reflows_as_before(calls):
    c = trundlr.TrundlrClient("http://t")
    c.create_task("a", 1)
    c.update_task(5, duration=2.0)
    assert calls == [("POST", "/tasks/"), ("PATCH", "/tasks/5")]


def test_a_batch_skips_each_reflow_and_asks_for_one_at_the_end(calls):
    c = trundlr.TrundlrClient("http://t")
    with c.batch():
        c.create_task("a", 1)
        c.create_task("b", 1)
        c.update_task(5, duration=2.0)
    assert calls == [("POST", "/tasks/?skip_reflow=true"), ("POST", "/tasks/?skip_reflow=true"),
                     ("PATCH", "/tasks/5?skip_reflow=true"), ("POST", "/schedule/reflow")]


def test_nested_batches_reflow_once_and_an_empty_one_not_at_all(calls):
    c = trundlr.TrundlrClient("http://t")
    with c.batch():
        with c.batch():
            c.create_task("a", 1)
        c.create_task("b", 1)
    assert [p for _, p in calls].count("/schedule/reflow") == 1
    calls.clear()
    with c.batch():
        pass
    assert calls == []


def test_a_failed_write_still_reflows_what_was_queued(calls):
    c = trundlr.TrundlrClient("http://t")
    with pytest.raises(RuntimeError):
        with c.batch():
            c.create_task("a", 1)
            raise RuntimeError("the next create failed")
    assert calls[-1] == ("POST", "/schedule/reflow")


def test_a_failed_reflow_is_reported_not_raised(calls, capsys):
    calls.fake.fail = True
    c = trundlr.TrundlrClient("http://t")
    with c.batch():
        c.create_task("a", 1)
    assert "re-flow failed" in capsys.readouterr().err

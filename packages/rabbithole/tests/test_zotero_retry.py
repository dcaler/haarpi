"""A WiFi blip must cost a Zotero ingest a pause, not the run.

26 Sep: DigiPros' report and elephantRoom's build both died on ONE failed Zotero request
(a DNS failure, a TLS handshake timeout) in an hour when oddjob's WiFi logged 238 connection
timeouts. The old machine's network had worse days; its runs just never landed on one.
"""

import hashlib

import httpx
import pytest

from rabbithole import corpus, pdfs, zotero
from rabbithole.config import GlobalConfig


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(zotero.time, "sleep", lambda s: slept.append(s))
    return slept


def _client(script, monkeypatch):
    """A ZoteroClient whose transport plays `script`: each entry is an exception to raise
    or an httpx.Response to return, one per request."""
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path))
        step = script.pop(0) if script else httpx.Response(200, json=[])
        if isinstance(step, Exception):
            raise step
        return step

    zc = zotero.ZoteroClient(GlobalConfig(zotero_api_key="k", zotero_library_id="123"))
    zc._client._c = httpx.Client(transport=httpx.MockTransport(handler),
                                 headers=zc.headers)
    return zc, seen


def test_a_dns_failure_mid_ingest_is_retried(monkeypatch, capsys, no_sleep):
    zc, seen = _client([httpx.ConnectError("Temporary failure in name resolution"),
                        httpx.ConnectTimeout("The handshake operation timed out"),
                        httpx.Response(200, json=[])], monkeypatch)
    assert zc.item_children("ITEM1") == []
    assert len(seen) == 3 and no_sleep == [5, 10]
    out = capsys.readouterr().out
    assert "[zotero] GET /items/ITEM1/children: ConnectError — retry 1 in 5s" in out
    assert "123" not in out                                  # no library id in the log


def test_http_5xx_and_429_are_retried_and_retry_after_is_honoured(monkeypatch, no_sleep):
    zc, seen = _client([httpx.Response(503), httpx.Response(429, headers={"Retry-After": "30"}),
                        httpx.Response(200, json=[{"key": "C1", "data": {"name": "x"}}])],
                       monkeypatch)
    assert zc.find_collection("x") == "C1"
    assert no_sleep == [5, 30]


def test_a_404_is_an_answer_not_an_outage(monkeypatch, no_sleep):
    zc, seen = _client([httpx.Response(404)], monkeypatch)
    assert zc.fetch_attachment("A1") is None
    assert len(seen) == 1 and no_sleep == []


def test_a_real_outage_gives_up_at_the_deadline(monkeypatch, no_sleep):
    monkeypatch.setenv("HAARPI_ZOTERO_OUTAGE_WAIT", "100")
    t = [0.0]
    monkeypatch.setattr(zotero.time, "monotonic", lambda: t[0])
    monkeypatch.setattr(zotero.time, "sleep", lambda s: (no_sleep.append(s), t.__setitem__(0, t[0] + s)))
    zc, seen = _client([httpx.ConnectError("down")] * 50, monkeypatch)
    with pytest.raises(httpx.ConnectError):
        zc.item_children("ITEM1")
    assert sum(no_sleep) <= 100 and no_sleep[:4] == [5, 10, 20, 40]


def test_a_post_whose_reply_was_lost_is_not_resent(monkeypatch, no_sleep):
    """The request reached Zotero; resending could create the collection twice."""
    zc, seen = _client([httpx.Response(200, json=[]),                 # find_collection: none
                        httpx.ReadTimeout("reply lost")], monkeypatch)
    with pytest.raises(httpx.ReadTimeout):
        zc.create_collection("New")
    assert [m for m, _ in seen] == ["GET", "POST"]


def test_a_post_that_never_left_is_resent(monkeypatch, no_sleep):
    zc, seen = _client([httpx.Response(200, json=[]),
                        httpx.ConnectError("down"),
                        httpx.Response(200, json={"successful": {"0": {"key": "NEW1"}}})],
                       monkeypatch)
    assert zc.create_collection("New") == "NEW1"
    assert [m for m, _ in seen] == ["GET", "POST", "POST"]


# ── when even the retries fail: use the copy already on the NAS ─────────────────

class FailingZotero:
    def __init__(self, md5="abc"):
        self.md5 = md5

    def pdf_attachment(self, item_key):
        return {"key": "ATT1", "md5": self.md5}

    def fetch_attachment(self, key):
        return None                                           # retries exhausted

    def fulltext(self, key):
        return ""


def _pdf_bytes(pages=5):
    import fitz
    doc = fitz.open()
    for i in range(pages):
        doc.new_page().insert_text((72, 72), f"evidence page {i + 1} " * 20)
    data = doc.tobytes()
    doc.close()
    return data


def test_a_failed_download_falls_back_to_the_nas_copy(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pdfs, "CACHE_ROOT", tmp_path / "ssd")
    monkeypatch.setattr(corpus, "_enrich", lambda c, idx: c)
    nas = tmp_path / "nas" / "pdfs"
    nas.mkdir(parents=True)
    (nas / "ITEM1.pdf").write_bytes(_pdf_bytes())
    paths = type("P", (), {"pdfs": nas})()
    it = {"key": "ITEM1", "data": {"itemType": "journalArticle", "title": "Kept paper"}}
    c = corpus._corpus_item_from_zotero(FailingZotero(), it, {}, paths, progress="[7/9]")
    assert c is not None and c.pdf_path.endswith("ITEM1.pdf")
    assert "download failed, using the copy already in pdfs/, 5 pages" in capsys.readouterr().out


def test_with_no_copy_anywhere_the_drop_is_a_named_warning(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pdfs, "CACHE_ROOT", tmp_path / "ssd")
    monkeypatch.setattr(corpus, "_enrich", lambda c, idx: c)
    paths = type("P", (), {"pdfs": tmp_path / "nas" / "pdfs"})()
    it = {"key": "ITEM2", "data": {"itemType": "journalArticle", "title": "Lost paper"}}
    assert corpus._corpus_item_from_zotero(FailingZotero(), it, {}, paths,
                                           progress="[8/9]") is None
    out = capsys.readouterr().out
    assert "[8/9] ITEM2 Lost paper" in out and "[WARN] DROPPED from the corpus" in out

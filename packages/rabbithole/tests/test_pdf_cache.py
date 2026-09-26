"""The local SSD cache under every PDF read, and Zotero downloads that happen only on change.

oddjob reaches the NAS over WiFi. DigiPros' report re-downloaded all 196 Zotero PDFs, wrote
each to the NAS, read it back, and read it again for page-marked text — minutes of NFS before
the GPU saw work. An unchanged corpus should now cost one `stat` per paper over the network.
"""

import hashlib
from pathlib import Path

import fitz
import pytest

from rabbithole import corpus, pdfs
from rabbithole.models import Candidate


def _pdf(path: Path, pages: int = 5, word: str = "evidence") -> bytes:
    doc = fitz.open()
    for i in range(pages):
        doc.new_page().insert_text((72, 72), f"{word} page {i + 1} " * 20)
    data = doc.tobytes()
    doc.close()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


@pytest.fixture()
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(pdfs, "CACHE_ROOT", tmp_path / "ssd")
    return tmp_path / "ssd"


@pytest.fixture()
def nas_reads(monkeypatch):
    """Count whole-file reads of anything outside the cache: the WiFi traffic."""
    reads = []
    real = Path.read_bytes

    def counting(self):
        if "ssd" not in self.parts:
            reads.append(self.name)
        return real(self)
    monkeypatch.setattr(Path, "read_bytes", counting)
    return reads


def test_a_nas_pdf_is_copied_once_and_then_read_locally(tmp_path, cache, nas_reads):
    nas = tmp_path / "nas" / "A.pdf"
    _pdf(nas)
    first = pdfs.local_copy(nas)
    assert cache in first.parents and first.read_bytes() == nas.read_bytes()
    nas_reads.clear()
    assert pdfs.local_copy(nas) == first
    assert nas_reads == []                                   # a stat, not a transfer


def test_a_changed_nas_file_is_copied_again(tmp_path, cache):
    nas = tmp_path / "nas" / "A.pdf"
    _pdf(nas, pages=2)
    pdfs.local_copy(nas)
    _pdf(nas, pages=6, word="revised")
    assert "revised" in pdfs.extract_text(nas)[0]
    assert pdfs.extract_text(nas)[1] == 6


def test_extracted_text_is_cached_per_kind(tmp_path, cache, monkeypatch):
    nas = tmp_path / "nas" / "A.pdf"
    _pdf(nas)
    calls = []
    real_x, real_m = pdfs._extract_text, pdfs._page_marked_text
    monkeypatch.setattr(pdfs, "_extract_text", lambda p: calls.append("x") or real_x(p))
    monkeypatch.setattr(pdfs, "_page_marked_text", lambda p: calls.append("m") or real_m(p))
    text, n = pdfs.extract_text(nas)
    assert n == 5 and "evidence page 1" in text
    assert pdfs.extract_text(nas) == (text, n)
    marked = pdfs.page_marked_text(nas)
    assert marked.startswith("[p.1]") and pdfs.page_marked_text(nas) == marked
    assert calls == ["x", "m"]                               # each extracted once


def test_a_failed_extraction_is_not_pinned(tmp_path, cache, monkeypatch):
    nas = tmp_path / "nas" / "A.pdf"
    _pdf(nas)
    monkeypatch.setattr(pdfs, "_extract_text", lambda p: ("", 0))
    assert pdfs.extract_text(nas) == ("", 0)
    monkeypatch.undo()
    monkeypatch.setattr(pdfs, "CACHE_ROOT", cache)
    assert pdfs.extract_text(nas)[1] == 5                    # retried, not stuck on ""


def test_a_missing_file_falls_straight_through(tmp_path, cache):
    assert pdfs.extract_text(tmp_path / "nope.pdf") == ("", 0)
    assert pdfs.page_marked_text(tmp_path / "nope.pdf") == ""


class FakeZotero:
    def __init__(self, data: bytes, md5: str | None = None):
        self.data, self.fetches = data, 0
        self.md5 = hashlib.md5(data).hexdigest() if md5 is None else md5

    def pdf_attachment(self, item_key):
        return {"key": "ATT1", "md5": self.md5}

    def fetch_attachment(self, key):
        self.fetches += 1
        return self.data

    def fulltext(self, key):
        return ""


def test_zotero_downloads_once_then_trusts_the_md5(tmp_path, cache, nas_reads):
    data = _pdf(tmp_path / "src.pdf")
    zc, dest = FakeZotero(data), tmp_path / "nas" / "pdfs" / "ITEM1.pdf"
    assert corpus._fetch_zotero_pdf(zc, zc.pdf_attachment("ITEM1"), dest).startswith("downloaded")
    assert dest.read_bytes() == data
    nas_reads.clear()
    pdfs.extract_text(dest)
    assert nas_reads == []                                   # seeded from the download
    assert corpus._fetch_zotero_pdf(zc, zc.pdf_attachment("ITEM1"), dest) == "cached"
    assert zc.fetches == 1


def test_a_changed_zotero_attachment_is_fetched_again(tmp_path, cache):
    zc, dest = FakeZotero(_pdf(tmp_path / "v1.pdf")), tmp_path / "nas" / "ITEM1.pdf"
    corpus._fetch_zotero_pdf(zc, zc.pdf_attachment("ITEM1"), dest)
    zc2 = FakeZotero(_pdf(tmp_path / "v2.pdf", pages=7, word="newer"))
    assert corpus._fetch_zotero_pdf(zc2, zc2.pdf_attachment("ITEM1"), dest).startswith("changed")
    assert "newer" in pdfs.extract_text(dest)[0]


def test_without_an_md5_every_run_downloads_as_before(tmp_path, cache):
    zc, dest = FakeZotero(_pdf(tmp_path / "s.pdf"), md5=""), tmp_path / "nas" / "ITEM1.pdf"
    corpus._fetch_zotero_pdf(zc, zc.pdf_attachment("ITEM1"), dest)
    corpus._fetch_zotero_pdf(zc, zc.pdf_attachment("ITEM1"), dest)
    assert zc.fetches == 2


def test_a_stub_download_is_no_pdf(tmp_path, cache):
    zc = FakeZotero(b"%PDF tiny")
    assert corpus._fetch_zotero_pdf(zc, zc.pdf_attachment("I"), tmp_path / "I.pdf") == ""
    assert not (tmp_path / "I.pdf").exists()


def test_each_zotero_item_gets_one_progress_line(tmp_path, cache, capsys, monkeypatch):
    monkeypatch.setattr(corpus, "_enrich", lambda c, idx: c)
    zc = FakeZotero(_pdf(tmp_path / "s.pdf"))
    paths = type("P", (), {"pdfs": tmp_path / "nas" / "pdfs"})()
    it = {"key": "ITEM1", "data": {"itemType": "journalArticle", "title": "A paper"}}
    c = corpus._corpus_item_from_zotero(zc, it, {}, paths, progress="[3/9]")
    assert isinstance(c, Candidate) and c.pdf_path.endswith("ITEM1.pdf")
    corpus._corpus_item_from_zotero(zc, it, {}, paths, progress="[3/9]")
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[0].strip().startswith("[3/9] ITEM1 A paper … downloaded ")
    assert ", 5 pages, " in lines[0]
    assert "… cached, 5 pages, " in lines[1]


def test_a_verified_download_matching_the_nas_copy_is_not_rewritten(tmp_path, cache):
    """First run on a corpus whose PDFs are already on the NAS: nothing is pushed back."""
    data = _pdf(tmp_path / "s.pdf")
    dest = tmp_path / "nas" / "ITEM1.pdf"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(data)
    before = dest.stat().st_mtime_ns
    zc = FakeZotero(data)
    assert corpus._fetch_zotero_pdf(zc, zc.pdf_attachment("ITEM1"), dest).startswith("downloaded")
    assert dest.stat().st_mtime_ns == before
    assert pdfs.extract_text(dest)[1] == 5                   # and the local copy is seeded

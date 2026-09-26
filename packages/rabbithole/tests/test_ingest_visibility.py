"""A report's log must say where its corpus came from, and how far the ingest has got.

DigiPros, September: the runners came back up after a machine rebuild without the shell's
Zotero exports. `report` printed one `[note]`, then nothing for eight minutes while it pulled
209 PDFs over NFS, then replaced the Zotero corpus with a folder one — every citekey
regenerated. Nothing in the log made either the fallback or the progress visible.
"""

from types import SimpleNamespace

import pytest

from rabbithole import corpus
from rabbithole.config import GlobalConfig, ProjectConfig
from rabbithole.models import Candidate


@pytest.fixture()
def folder(tmp_path, monkeypatch):
    pdfs = tmp_path / "pdfs"
    pdfs.mkdir()
    for name in ("A.pdf", "B.pdf", "C.pdf"):
        (pdfs / name).write_bytes(b"x" * 2_000_000)
    monkeypatch.setattr(corpus, "_load_candidate_index", lambda paths: {})
    monkeypatch.setattr(corpus, "extract_text", lambda fp: (f"text of {fp.name}", 12))
    monkeypatch.setattr(corpus, "looks_like_fulltext", lambda text, n: "B.pdf" not in text)
    monkeypatch.setattr(corpus, "_candidate_from_pdf",
                        lambda fp, text: Candidate(title=fp.stem, source="folder"))
    monkeypatch.setattr(corpus, "dedupe_corpus", lambda c: c)
    return SimpleNamespace(pdfs=pdfs)


def test_each_pdf_is_named_with_its_place_size_and_time(folder, capsys):
    out = corpus.ingest_from_folder(folder)
    assert [c.title for c in out] == ["A", "C"]
    lines = [l for l in capsys.readouterr().out.splitlines() if "/3]" in l]
    assert len(lines) == 3, lines
    assert lines[0].strip().startswith("[1/3] A.pdf (2.0 MB) … 12 pages, ")
    assert "[2/3] B.pdf (2.0 MB) … [skip] no usable full text, " in lines[1]
    assert lines[2].strip().startswith("[3/3] C.pdf")


def test_the_file_is_named_before_it_is_read(folder, monkeypatch, capsys):
    """A hang on one file must leave that file's name on screen."""
    seen = {}

    def slow_extract(fp):
        seen.setdefault(fp.name, capsys.readouterr().out)
        return "text", 3
    monkeypatch.setattr(corpus, "extract_text", slow_extract)
    corpus.ingest_from_folder(folder)
    assert seen["B.pdf"].rstrip().endswith("[2/3] B.pdf (2.0 MB) …")


def _cfg(collection_key=""):
    cfg = ProjectConfig()
    cfg.zotero = {"collection_key": collection_key}
    return cfg


def test_status_names_the_collection_when_zotero_is_configured():
    gc = GlobalConfig(zotero_api_key="k", zotero_library_id="1")
    assert corpus.zotero_status(_cfg("ABCD1234"), gc) == "zotero: collection ABCD1234"


def test_status_says_a_collection_will_be_ignored_and_names_what_is_missing():
    gc = GlobalConfig(zotero_api_key="", zotero_library_id="1")
    s = corpus.zotero_status(_cfg("ABCD1234"), gc)
    assert "NOT configured" in s and "ZOTERO_API_KEY" in s
    assert "ZOTERO_LIBRARY_ID" not in s
    assert "IGNORED" in s and "./pdfs/" in s


def test_status_for_a_project_that_never_had_a_collection():
    gc = GlobalConfig()
    assert "no collection" in corpus.zotero_status(_cfg(""), gc)


def test_the_fallback_is_a_warning_that_says_the_corpus_will_be_rebuilt(folder, monkeypatch,
                                                                        capsys):
    monkeypatch.setattr(corpus, "persist", lambda paths, c: None)
    corpus.build(_cfg("ABCD1234"), GlobalConfig(), folder, from_folder=False)
    out = capsys.readouterr().out
    assert "[WARN] Zotero is not configured" in out
    assert "ZOTERO_API_KEY, ZOTERO_LIBRARY_ID unset" in out
    assert "work/corpus.json will be rebuilt" in out


def test_an_explicit_from_folder_run_is_not_warned_about(folder, monkeypatch, capsys):
    monkeypatch.setattr(corpus, "persist", lambda paths, c: None)
    corpus.build(_cfg(""), GlobalConfig(), folder, from_folder=True)
    assert "[WARN]" not in capsys.readouterr().out

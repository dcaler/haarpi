"""One project, one Zotero collection, resolved by key and never invented while a key exists.

gather used to find its collection by name — case-sensitively, first 100 only — and create a fresh
one on a miss, overwriting the config's key. DigiPros' name drifted between "digipros" and
"DigiPros", and each drift split the corpus into a new collection: DigiPros_old, then
DigiPros_lessOld.

Runnable two ways:
    pytest tests/test_zotero_collection.py
    python tests/test_zotero_collection.py
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from rabbithole import config, zotero


class _FakeZotero:
    """Collections only; records creation so a test can prove none happened."""

    def __init__(self, cols: dict[str, str]):
        self.cols = dict(cols)                 # key -> name
        self.created: list[str] = []

    def collections(self):
        return [{"key": k, "data": {"name": n}} for k, n in self.cols.items()]

    def collection_name(self, key):
        return self.cols.get(key)

    find_collection = zotero.ZoteroClient.find_collection
    similar_collections = zotero.ZoteroClient.similar_collections

    def create_collection(self, name):
        key = f"NEW{len(self.created)}"
        self.cols[key] = name
        self.created.append(name)
        return key


def _cfg(name="DigiPros", key=""):
    return SimpleNamespace(project_name=name, zotero={"collection_key": key})


def test_a_configured_key_is_used_even_when_the_name_has_drifted():
    zc = _FakeZotero({"24PRF24C": "digipros"})
    assert zotero.resolve_collection(zc, _cfg("DigiPros", "24PRF24C"), create=True) == "24PRF24C"
    assert zc.created == []


def test_a_missing_configured_key_is_an_error_never_a_new_collection():
    zc = _FakeZotero({"24PRF24C": "DigiPros"})
    with pytest.raises(zotero.CollectionError, match="not creating a replacement"):
        zotero.resolve_collection(zc, _cfg("DigiPros", "GONE1234"), create=True)
    assert zc.created == []


def test_without_a_key_the_name_matches_regardless_of_case():
    zc = _FakeZotero({"24PRF24C": "digipros", "XX": "Other"})
    assert zotero.resolve_collection(zc, _cfg("DigiPros"), create=True) == "24PRF24C"
    assert zc.created == []


def test_two_collections_with_the_same_name_are_refused_not_guessed():
    zc = _FakeZotero({"A": "digipros", "B": "DigiPros "})
    with pytest.raises(zotero.CollectionError, match="2 Zotero collections"):
        zotero.resolve_collection(zc, _cfg("DigiPros"), create=True)


def test_a_collection_is_created_only_when_there_is_none_and_creating_is_allowed():
    zc = _FakeZotero({"XX": "Other"})
    with pytest.raises(zotero.CollectionError, match="Run gather first"):
        zotero.resolve_collection(zc, _cfg("DigiPros"))
    assert zotero.resolve_collection(zc, _cfg("DigiPros"), create=True) == "NEW0"
    assert zc.created == ["DigiPros"]


def test_leftovers_of_a_split_are_named(capsys):
    zc = _FakeZotero({"24PRF24C": "DigiPros", "9TWE": "DigiPros_old",
                      "5D5Z": "DigiPros_lessOld", "XX": "FirmPathways"})
    others = zotero.warn_similar(zc, _cfg("DigiPros"), "24PRF24C")
    assert {k for k, _ in others} == {"9TWE", "5D5Z"}
    assert "9TWE 'DigiPros_old'" in capsys.readouterr().err


def test_the_manifest_pins_the_collection_over_the_review_config(tmp_path, monkeypatch):
    from haarpi import project as hproject
    root = tmp_path / "proj"
    (root / "litReview").mkdir(parents=True)
    m = hproject.Manifest(name="DigiPros", short_title="digipros", zotero_collection="24PRF24C")
    hproject.save_manifest(m, root)
    assert hproject.load_manifest(root).zotero_collection == "24PRF24C"   # persists
    (root / "litReview" / "litrev.yaml").write_text(
        "project_name: digipros\nzotero:\n  collection_key: 5D5ZUNBV\n")
    cfg = config.load_project(root)
    assert cfg.project_name == "DigiPros" and cfg.zotero["collection_key"] == "24PRF24C"


def test_the_first_gather_pins_its_collection_and_never_overwrites_one(tmp_path, monkeypatch):
    from haarpi import project as hproject

    from rabbithole import discover
    root = tmp_path / "proj"
    root.mkdir()
    hproject.save_manifest(hproject.Manifest(name="P", short_title="p"), root)
    monkeypatch.chdir(root)
    discover._pin_collection("FIRST")
    assert hproject.load_manifest(root).zotero_collection == "FIRST"
    discover._pin_collection("SECOND")
    assert hproject.load_manifest(root).zotero_collection == "FIRST"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))

"""The chroma store is worked on locally and written back to the project.

Why: the store is SQLite, the project tree is an NFSv3 mount, and on this box NFS reaches the
server over WiFi. SQLite locking on NFS is a round trip per operation through the server's lock
manager — reliable almost always, and "almost" is a per-operation probability. A `report` over
48 papers makes thousands of them; on 2026-09-08 one was lost and "database is locked" ended an
hour-long run at paper 7 of 48.

The property that matters most is the one staging could REGRESS: today's incremental writes go
straight to the share, so a crash keeps what was indexed. Staging must keep that — hence the
write-back on exception and on SIGTERM, not only on a clean return.

Runnable two ways:
    pytest tests/test_chroma_staging.py
    python tests/test_chroma_staging.py
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path

import pytest

from rabbithole import chroma

chromadb = pytest.importorskip("chromadb")


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Each test gets its own local work root and a clean staging registry."""
    monkeypatch.setattr(chroma, "_WORK_ROOT", tmp_path / "local")
    monkeypatch.setattr(chroma, "_staged", {})
    monkeypatch.setattr(chroma, "_hooks_installed", True)   # don't touch real signal handlers
    yield


def _add(col, cid):
    col.add(ids=[cid], documents=[f"doc {cid}"],
            metadatas=[{"citekey": cid, "page": 1, "chunk_idx": 0}],
            embeddings=[[0.01] * 8])


def _count(store: Path) -> int:
    client = chromadb.PersistentClient(path=str(store))
    return len(client.get_or_create_collection("papers").get(include=[])["ids"])


# ── the store is worked on locally ───────────────────────────────────────────

def test_a_writer_works_on_local_disk_not_the_project(tmp_path):
    remote = tmp_path / "project" / "litReview" / "work" / "chroma"
    remote.mkdir(parents=True)
    chroma.get_collection(remote, writable=True)
    local = chroma._staged.get(remote)
    assert local is not None and local != remote
    assert (tmp_path / "local") in local.parents


def test_a_reader_stages_in_but_is_never_written_back(tmp_path):
    """A reader that copied back could overwrite a writer's work with a stale snapshot, and
    has nothing of its own to save."""
    remote = tmp_path / "project" / "litReview" / "work" / "chroma"
    remote.mkdir(parents=True)
    chroma.get_collection(remote, writable=False)
    assert chroma._staged == {}


def test_existing_content_is_carried_down_to_the_local_copy(tmp_path):
    remote = tmp_path / "p" / "l" / "w" / "chroma"
    remote.mkdir(parents=True)
    _add(chromadb.PersistentClient(path=str(remote)).get_or_create_collection("papers"), "a")
    col = chroma.get_collection(remote, writable=True)
    assert len(col.get(include=[])["ids"]) == 1, "the staged copy holds what the project held"


# ── and written back ─────────────────────────────────────────────────────────

def test_work_done_locally_reaches_the_project(tmp_path):
    remote = tmp_path / "p" / "l" / "w" / "chroma"
    remote.mkdir(parents=True)
    col = chroma.get_collection(remote, writable=True)
    _add(col, "written-while-staged")
    chroma.sync_back()
    assert _count(remote) == 1


def test_sync_back_is_idempotent(tmp_path):
    """It runs from atexit AND from the signal handler; the second must not undo the first."""
    remote = tmp_path / "p" / "l" / "w" / "chroma"
    remote.mkdir(parents=True)
    _add(chroma.get_collection(remote, writable=True), "x")
    chroma.sync_back()
    chroma.sync_back()
    assert _count(remote) == 1


def test_the_previous_store_is_not_left_beside_the_new_one(tmp_path):
    """The swap goes through .new/.old; neither may survive a successful write-back."""
    remote = tmp_path / "p" / "l" / "w" / "chroma"
    remote.mkdir(parents=True)
    _add(chroma.get_collection(remote, writable=True), "x")
    chroma.sync_back()
    siblings = {p.name for p in remote.parent.iterdir()}
    assert siblings == {"chroma"}, siblings


def test_indexing_survives_an_exception(tmp_path):
    """The property staging could have cost. Writes used to land on the share as they happened,
    which is why a crashed run kept its six indexed papers."""
    remote = tmp_path / "p" / "l" / "w" / "chroma"
    remote.mkdir(parents=True)
    col = chroma.get_collection(remote, writable=True)
    _add(col, "indexed-before-the-crash")
    try:
        raise RuntimeError("database is locked")   # the 2026-09-08 failure
    except RuntimeError:
        chroma.sync_back()                          # what the atexit hook does
    assert _count(remote) == 1


# ── never at the cost of running at all ──────────────────────────────────────

def test_a_store_that_cannot_be_staged_falls_back_to_the_share(tmp_path, monkeypatch):
    """A cache that does not work is not a reason for the tool not to run."""
    remote = tmp_path / "p" / "l" / "w" / "chroma"
    remote.mkdir(parents=True)

    def _boom(*a, **kw):
        raise OSError("no space left on device")

    monkeypatch.setattr(chroma.shutil, "copytree", _boom)
    _add(chromadb.PersistentClient(path=str(remote)).get_or_create_collection("papers"), "a")
    assert chroma._stage_in(remote, writable=True) == remote
    assert chroma._staged == {}, "nothing to write back when nothing was staged"


def test_a_failed_write_back_keeps_the_local_copy(tmp_path, monkeypatch):
    """The index is derived data, so losing it costs re-embedding — but the run says where it
    is rather than deleting it out from under a recovery."""
    remote = tmp_path / "p" / "l" / "w" / "chroma"
    remote.mkdir(parents=True)
    chroma.get_collection(remote, writable=True)
    local = chroma._staged[remote]
    monkeypatch.setattr(chroma.shutil, "copytree",
                        lambda *a, **kw: (_ for _ in ()).throw(OSError("share gone")))
    chroma.sync_back()
    assert local.exists(), "the only copy of the work is not deleted on a failed write-back"


def test_a_stale_local_copy_is_discarded_not_reused(tmp_path):
    """The share is the truth. A leftover from a killed run could be older than the project."""
    remote = tmp_path / "p" / "l" / "w" / "chroma"
    remote.mkdir(parents=True)
    stale = chroma._WORK_ROOT / chroma._slug(remote)
    stale.mkdir(parents=True)
    (stale / "leftover.txt").write_text("from a previous run")
    chroma._stage_in(remote, writable=True)
    assert not (stale / "leftover.txt").exists()


def test_two_projects_get_separate_local_stores(tmp_path):
    a = tmp_path / "projA" / "litReview" / "work" / "chroma"
    b = tmp_path / "projB" / "litReview" / "work" / "chroma"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    assert chroma._slug(a) != chroma._slug(b)
    assert "projA" in chroma._slug(a) and "projB" in chroma._slug(b)


# ── the write lock ───────────────────────────────────────────────────────────

def test_a_second_writer_works_on_the_share_rather_than_clobbering(tmp_path):
    """The risk staging CREATED. Two writers each take a snapshot, both copy back, and the
    last one silently discards the other's indexing — where working over the share at least
    had SQLite arbitrating. The second writer is sent back to the share, which is exactly the
    behaviour that was safe all along."""
    remote = tmp_path / "p" / "l" / "w" / "chroma"
    remote.mkdir(parents=True)
    first = chroma.get_collection(remote, writable=True)
    _add(first, "first-writers-work")
    assert chroma._staged.get(remote) is not None

    # A different process arrives at the same store.
    other = tmp_path / "elsewhere"
    chroma._lock_path(remote).write_text(json.dumps(
        {"host": socket.gethostname(), "pid": os.getpid(), "started": "now"}))
    chroma._staged.pop(remote)                       # pretend we are that other process
    assert chroma._acquire(remote) is True, "our own pid re-entering is not a conflict"

    chroma._lock_path(remote).write_text(json.dumps(
        {"host": "some-other-host", "pid": 999999, "started": "now"}))
    assert chroma._stage_in(remote, writable=True) == remote, "sent to the share, not staged"
    assert remote not in chroma._staged, "and so it has nothing to write back"


def test_a_lock_left_by_a_dead_process_is_cleared(tmp_path):
    """A killed run must not wedge the project forever."""
    remote = tmp_path / "p" / "l" / "w" / "chroma"
    remote.mkdir(parents=True)
    chroma._lock_path(remote).write_text(json.dumps(
        {"host": socket.gethostname(), "pid": 2 ** 22, "started": "earlier"}))
    assert chroma._holder(remote) is None
    assert chroma._acquire(remote) is True


def test_another_hosts_lock_is_believed(tmp_path):
    """Liveness is unknowable across hosts. Being wrong costs a slower run; the reverse costs
    two writers overwriting each other."""
    remote = tmp_path / "p" / "l" / "w" / "chroma"
    remote.mkdir(parents=True)
    chroma._lock_path(remote).write_text(json.dumps(
        {"host": "another-box", "pid": 1, "started": "earlier"}))
    assert chroma._acquire(remote) is False


def test_the_lock_is_released_after_the_write_back(tmp_path):
    remote = tmp_path / "p" / "l" / "w" / "chroma"
    remote.mkdir(parents=True)
    chroma.get_collection(remote, writable=True)
    assert chroma._lock_path(remote).exists()
    chroma.sync_back()
    assert not chroma._lock_path(remote).exists()


def test_the_lock_is_released_even_when_the_write_back_fails(tmp_path, monkeypatch):
    """Otherwise one failed run leaves the project locked against every later one."""
    remote = tmp_path / "p" / "l" / "w" / "chroma"
    remote.mkdir(parents=True)
    chroma.get_collection(remote, writable=True)
    monkeypatch.setattr(chroma.shutil, "copytree",
                        lambda *a, **kw: (_ for _ in ()).throw(OSError("share gone")))
    chroma.sync_back()
    assert not chroma._lock_path(remote).exists()


def test_a_reader_takes_no_lock(tmp_path):
    """Readers never write back, so they cannot clobber and must not block a writer."""
    remote = tmp_path / "p" / "l" / "w" / "chroma"
    remote.mkdir(parents=True)
    chroma.get_collection(remote, writable=False)
    assert not chroma._lock_path(remote).exists()


def test_the_lock_sits_beside_the_store_not_inside_it(tmp_path):
    """Inside, it would be copied into the snapshot and written back out again — outliving
    the run that took it."""
    remote = tmp_path / "p" / "l" / "w" / "chroma"
    remote.mkdir(parents=True)
    chroma.get_collection(remote, writable=True)
    assert chroma._lock_path(remote).parent == remote.parent
    assert not any(p.name.endswith(".lock") for p in remote.rglob("*"))


def test_a_released_lock_is_never_someone_elses(tmp_path):
    remote = tmp_path / "p" / "l" / "w" / "chroma"
    remote.mkdir(parents=True)
    chroma._lock_path(remote).write_text(json.dumps(
        {"host": "another-box", "pid": 1, "started": "earlier"}))
    chroma._release(remote)
    assert chroma._lock_path(remote).exists(), "we do not drop a lock we did not take"


if __name__ == "__main__":
    import tempfile, traceback

    class _MP:
        def __init__(self): self._undo = []
        def setattr(self, obj, name, val):
            self._undo.append((obj, name, getattr(obj, name)))
            setattr(obj, name, val)
        def undo(self):
            for o, n, v in reversed(self._undo):
                setattr(o, n, v)

    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v) and k != "test_"]
    failures = 0
    for fn in fns:
        mp = _MP()
        with tempfile.TemporaryDirectory() as td:
            mp.setattr(chroma, "_WORK_ROOT", Path(td) / "local")
            mp.setattr(chroma, "_staged", {})
            mp.setattr(chroma, "_hooks_installed", True)
            try:
                n = fn.__code__.co_argcount
                fn(*([Path(td), mp][:n]))
                print(f"  PASS  {fn.__name__}")
            except Exception:  # noqa: BLE001
                failures += 1
                print(f"  FAIL  {fn.__name__}")
                traceback.print_exc()
            finally:
                mp.undo()
    print(f"\n{len(fns) - failures}/{len(fns)} passed")
    raise SystemExit(1 if failures else 0)

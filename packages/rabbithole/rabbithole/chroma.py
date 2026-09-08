"""ChromaDB helpers: index paper chunks and retrieve by semantic similarity.

Each project gets one persistent collection ("papers") stored at work/chroma/.
Every chunk carries {citekey, page, chunk_idx} metadata so queries can be
scoped to a single paper and results reassembled in reading order.

Papers are identified by their citekey (not corpus list-index): the corpus is
re-gathered and re-keyed between runs, so position is not a stable identity —
keying by citekey keeps the index aligned with the source it actually holds.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import sys
import time
from datetime import datetime
from pathlib import Path

from . import runlog

_CHUNK_CHARS = 1800       # max chars per chunk (≈ 450 tokens — leaves room for query + output)
_COLLECTION_NAME = "papers"
_LOCATE_CANDIDATES = 3    # chunks fetched per claim, so a collision can fall through to the next


def get_collection(chroma_dir, writable: bool = False):
    """The project's collection, worked on from LOCAL disk rather than over the share.

    The store is a SQLite file, and the project tree is an NFSv3 mount that (on this box)
    reaches the server over WiFi. SQLite's locking on NFS goes through the server's lock
    manager on every operation, so each one is a round trip across that link. It is reliable
    almost always — nine projects indexed in three weeks — but "almost" is a per-operation
    probability, and a `report` over a 48-paper corpus makes thousands of them. On 2026-09-08
    one was lost and `chromadb.errors.InternalError: database is locked` ended an hour-long
    run at paper 7 of 48.

    So the store is staged to local disk, worked on there, and copied back. The canonical copy
    stays on the share, where it is synced and backed up with the rest of the project.

    ``writable`` says whether this caller INDEXES. Readers (locate, the bibliography passes)
    stage in and never copy back, which is faster and removes any chance of a reader's stale
    copy overwriting a writer's work.

    Falls back to working directly on the share if staging cannot be set up: a cache that does
    not work is not a reason for the tool not to run.
    """
    import chromadb
    chroma_dir = Path(chroma_dir)
    local = _stage_in(chroma_dir, writable)
    local.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(local))
    return client.get_or_create_collection(_COLLECTION_NAME)


_WORK_ROOT = Path.home() / ".cache" / "haarpi" / "chroma"
_staged: dict[Path, Path] = {}          # remote -> local, for the copy-back
_hooks_installed = False


def _slug(remote: Path) -> str:
    """A stable, readable local directory name for one project's store."""
    h = hashlib.sha1(str(remote.resolve()).encode()).hexdigest()[:10]
    # <project>/litReview/work/chroma -> the project directory is four levels up
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", remote.parents[2].name if len(remote.parents) > 2
                  else remote.name)
    return f"{name}-{h}"


def _lock_path(remote: Path) -> Path:
    """Beside the store, not inside it — a lock copied into the staged snapshot and written
    back out again would outlive the run that took it."""
    return remote.with_name(remote.name + ".lock")


def _holder(remote: Path) -> dict | None:
    """Who holds the write lock on this store, or None if nobody does.

    A lock whose process is gone is not a lock: a killed run must not wedge the project
    forever. Liveness is only checkable for our own host, so another machine's lock is treated
    as live — the cost of being wrong is a slower run, and the cost of the reverse is two
    writers silently overwriting each other.
    """
    fp = _lock_path(remote)
    try:
        held = json.loads(fp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if held.get("host") != socket.gethostname():
        return held
    try:
        os.kill(int(held.get("pid", -1)), 0)
        return held
    except (OSError, ValueError, TypeError):
        print(f"  {runlog.stamp()}clearing a stale chroma lock from pid "
              f"{held.get('pid')} ({held.get('started')})", flush=True)
        fp.unlink(missing_ok=True)
        return None


def _acquire(remote: Path) -> bool:
    """Claim the right to write this store back. Advisory, and deliberately so.

    Staging introduced a clobber path that did not exist before: two writers each take their
    own snapshot, both copy back, and the last one silently discards the other's indexing.
    Working over the share at least had SQLite arbitrating. This is the replacement — and a
    caller refused here does not fail, it works on the share instead, which is exactly the
    behaviour that was safe all along.
    """
    held = _holder(remote)
    if held:
        if held.get("pid") == os.getpid() and held.get("host") == socket.gethostname():
            return True                 # already ours: get_collection called twice in one run
        print(f"  [warn] another run holds the chroma write lock on {remote.name} "
              f"(host {held.get('host')}, pid {held.get('pid')}, since {held.get('started')}) "
              f"— working directly on the share instead of staging, so neither run can "
              f"overwrite the other.", file=sys.stderr)
        return False
    try:
        # O_EXCL so two processes racing here cannot both believe they won.
        fd = os.open(_lock_path(remote), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"host": socket.gethostname(), "pid": os.getpid(),
                       "started": datetime.now().isoformat(timespec="seconds")}, fh)
        return True
    except FileExistsError:
        return False
    except OSError as e:
        print(f"  [warn] could not take the chroma write lock ({e}); working on the share",
              file=sys.stderr)
        return False


def _release(remote: Path) -> None:
    """Drop our lock. Never removes someone else's."""
    held = _holder(remote)
    if held and held.get("pid") == os.getpid() and held.get("host") == socket.gethostname():
        _lock_path(remote).unlink(missing_ok=True)


def _stage_in(remote: Path, writable: bool) -> Path:
    """Copy the store down to local disk and return the local path (or ``remote`` on failure)."""
    if writable and not _acquire(remote):
        return remote                   # a second writer works on the share; nothing to clobber
    try:
        local = _WORK_ROOT / _slug(remote)
        if local.exists():
            shutil.rmtree(local)        # never trust a leftover: the share is the truth
        local.parent.mkdir(parents=True, exist_ok=True)
        if remote.exists() and any(remote.iterdir()):
            t0 = time.time()
            shutil.copytree(remote, local)
            print(f"  {runlog.stamp()}chroma staged to local disk "
                  f"({_size_mb(local):.0f} MB in {time.time() - t0:.0f}s)", flush=True)
        else:
            local.mkdir(parents=True, exist_ok=True)
        if writable:
            _staged[remote] = local
            _install_hooks()
        return local
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] could not stage chroma to local disk ({e}); working on the share",
              file=sys.stderr)
        if writable:
            _release(remote)            # we hold a lock for staging we are not doing
        return remote


def sync_back() -> None:
    """Copy every staged writable store back to the share. Safe to call more than once."""
    for remote, local in list(_staged.items()):
        try:
            if not local.exists():
                continue
            t0 = time.time()
            staging = remote.with_name(remote.name + ".new")
            if staging.exists():
                shutil.rmtree(staging)
            shutil.copytree(local, staging)
            # Two renames rather than a copy over the live store: an interruption leaves the
            # previous store recoverable beside it instead of a half-written one in its place.
            previous = remote.with_name(remote.name + ".old")
            if previous.exists():
                shutil.rmtree(previous)
            if remote.exists():
                remote.rename(previous)
            staging.rename(remote)
            if previous.exists():
                shutil.rmtree(previous, ignore_errors=True)
            print(f"  {runlog.stamp()}chroma written back to the project "
                  f"({_size_mb(remote):.0f} MB in {time.time() - t0:.0f}s)", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] could not write the chroma index back to {remote} ({e}). The "
                  f"local copy is at {local} — the index is derived data and will be rebuilt "
                  f"on the next run, but nothing is lost by keeping it.", file=sys.stderr)
        finally:
            _release(remote)
            _staged.pop(remote, None)


def _install_hooks() -> None:
    """Copy back on normal exit, on an unhandled exception, and on SIGTERM.

    SIGTERM matters: it is how the runner stops a task, and without a handler the default
    disposition would end the process with the run's indexing still only on local disk.
    """
    global _hooks_installed
    if _hooks_installed:
        return
    atexit.register(sync_back)
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            previous = signal.getsignal(sig)

            def _handler(signum, frame, _prev=previous):
                sync_back()
                if callable(_prev):
                    _prev(signum, frame)
                else:
                    raise SystemExit(128 + signum)

            signal.signal(sig, _handler)
        except (ValueError, OSError):
            pass                        # not the main thread: atexit still covers us
    _hooks_installed = True


def _size_mb(p: Path) -> float:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 1e6


def _safe_id(citekey: str) -> str:
    """A chunk-id-safe rendering of a citekey (ChromaDB ids must be strings)."""
    return re.sub(r'[^A-Za-z0-9._-]', '_', citekey) or 'paper'


def is_paper_indexed(collection, citekey: str) -> bool:
    r = collection.get(where={"citekey": citekey}, limit=1, include=[])
    return len(r["ids"]) > 0


def _page_chunks(text: str) -> list[tuple[int, str]]:
    """Split [p.N]-marked text into (page_num, chunk) pairs, each ≤ _CHUNK_CHARS."""
    parts = re.split(r'(\[p\.\d+\])', text)
    result: list[tuple[int, str]] = []
    current_page = 0
    buf = ""

    def flush(page: int, raw: str) -> None:
        raw = raw.strip()
        if not raw:
            return
        prefix = f"[p.{page}]\n"
        available = _CHUNK_CHARS - len(prefix)
        for i in range(0, len(raw), available):
            result.append((page, prefix + raw[i:i + available]))

    for part in parts:
        m = re.match(r'\[p\.(\d+)\]', part)
        if m:
            flush(current_page, buf)
            current_page = int(m.group(1))
            buf = ""
        else:
            buf += part
    flush(current_page, buf)
    return result


def index_paper(collection, brain, citekey: str, text: str) -> int:
    """Chunk, embed, and store a paper. Returns number of chunks indexed."""
    chunks = _page_chunks(text)
    if not chunks:
        return 0
    texts = [ch for _, ch in chunks]
    embeddings = brain.embed_batch(texts)
    ids, docs, metas, embeds = [], [], [], []
    safe = _safe_id(citekey)
    for j, ((page_num, chunk_text), emb) in enumerate(zip(chunks, embeddings)):
        if not emb:
            continue
        ids.append(f"{safe}_{j:04d}")
        docs.append(chunk_text)
        metas.append({"citekey": citekey, "page": page_num, "chunk_idx": j})
        embeds.append(emb)
    if ids:
        collection.add(ids=ids, documents=docs, metadatas=metas, embeddings=embeds)
    return len(ids)


def _paper_chunk_count(collection, citekey: str) -> int:
    r = collection.get(where={"citekey": citekey}, include=[], limit=9999)
    return len(r["ids"])


def query_paper(collection, brain, citekey: str, query: str,
                n_results: int = 4) -> str:
    """Return top-N most relevant chunks for query, reassembled in page order."""
    try:
        q_emb = brain.embed(query)
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] chroma query embed failed: {e}", file=sys.stderr)
        return ""

    n = min(n_results, _paper_chunk_count(collection, citekey))
    if n == 0:
        return ""

    r = collection.query(
        query_embeddings=[q_emb],
        where={"citekey": citekey},
        n_results=n,
        include=["documents", "metadatas"],
    )
    docs = r.get("documents", [[]])[0]
    metas = r.get("metadatas", [[]])[0]
    if not docs:
        return ""
    pairs = sorted(zip(metas, docs), key=lambda x: x[0].get("chunk_idx", 0))
    return "\n\n".join(doc for _, doc in pairs)


def _best_sentence(chunk_text: str, claim: str, max_words: int = 30) -> str:
    """Return the sentence from chunk_text with most word overlap with claim."""
    clean = re.sub(r'^\[p\.\d+\]\n?', '', chunk_text).strip()
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', clean) if len(s.strip()) > 15]
    if not sentences:
        words = clean.split()
        return ' '.join(words[:max_words]) + ('...' if len(words) > max_words else '')
    claim_words = set(re.sub(r'[^\w\s]', '', claim).lower().split())
    best, best_score = sentences[0], -1
    for sent in sentences:
        sent_words = set(re.sub(r'[^\w\s]', '', sent).lower().split())
        score = len(claim_words & sent_words)
        if score > best_score:
            best_score, best = score, sent
    words = best.split()
    return ' '.join(words[:max_words]) + ('...' if len(words) > max_words else '')


def locate_direct(collection, brain, citekey: str, statements: str) -> list[dict]:
    """Locate claims using pure embedding retrieval — no LLM call.

    For each claim sentence: embed → top-1 chunk → best matching sentence as quote.
    Returns the same [{claim, location, quote}] structure as the LLM locate path.
    """
    claim_sents = [s.strip() for s in re.split(r'(?<=[.!?])\s+', statements) if s.strip()]
    if not claim_sents:
        return []
    total = _paper_chunk_count(collection, citekey)
    if total == 0:
        return []
    results = []
    seen_chunks: set[int] = set()
    for claim in claim_sents:
        try:
            q_emb = brain.embed(claim)
        except Exception:  # noqa: BLE001
            continue
        # Ask for several chunks, not one. Two claims about the same finding legitimately
        # retrieve the same top chunk; taking the best UNSEEN one gives the second claim its
        # own passage instead of making it collide.
        r = collection.query(
            query_embeddings=[q_emb],
            where={"citekey": citekey},
            n_results=_LOCATE_CANDIDATES,
            include=["documents", "metadatas"],
        )
        docs = r.get("documents", [[]])[0]
        metas = r.get("metadatas", [[]])[0]
        if not docs:
            continue
        # Prefer the best-ranked chunk nobody has quoted yet; fall back to the top chunk when
        # every candidate is already spoken for. A claim is NEVER dropped for colliding — the
        # annotated bibliography is where this review's facts live, and a repeated passage is
        # a far smaller defect than a fact that silently vanishes.
        pick = 0
        for j, meta in enumerate(metas):
            if meta.get("chunk_idx", 0) not in seen_chunks:
                pick = j
                break
        meta, chunk = metas[pick], docs[pick]
        seen_chunks.add(meta.get("chunk_idx", 0))
        page = meta.get("page", "?")
        quote = _best_sentence(chunk, claim)
        results.append({
            # Stored WHOLE. The store is not the place to lose information; the annotated
            # bibliography truncates for display (see summarize.bibliography), where the cut
            # lands on a word boundary and says so with an ellipsis.
            "claim": claim,
            "location": f"p.{page}",
            "quote": quote,
        })
    return results


def query_paper_multi(collection, brain, citekey: str, queries: list[str],
                      n_per_query: int = 3) -> str:
    """Retrieve chunks across multiple queries, deduplicated and page-ordered.

    Runs one embed + one ChromaDB query per query string, unions the results
    (no duplicate chunks), and returns them sorted by page order. This replaces
    multi-step LLM condensing: N embed calls (fast) instead of N LLM calls.
    """
    total = _paper_chunk_count(collection, citekey)
    if total == 0:
        return ""
    n = min(n_per_query, total)
    seen: set[int] = set()
    pairs: list[tuple[dict, str]] = []
    for q in queries:
        try:
            q_emb = brain.embed(q)
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] multi-query embed failed: {e}", file=sys.stderr)
            continue
        r = collection.query(
            query_embeddings=[q_emb],
            where={"citekey": citekey},
            n_results=n,
            include=["documents", "metadatas"],
        )
        for meta, doc in zip(r.get("metadatas", [[]])[0], r.get("documents", [[]])[0]):
            cid = meta.get("chunk_idx", 0)
            if cid not in seen:
                seen.add(cid)
                pairs.append((meta, doc))
    pairs.sort(key=lambda x: x[0].get("chunk_idx", 0))
    return "\n\n".join(doc for _, doc in pairs)

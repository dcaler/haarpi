"""ChromaDB helpers: index paper chunks and retrieve by semantic similarity.

Each project gets one persistent collection ("papers") stored at work/chroma/.
Every chunk carries {citekey, page, chunk_idx} metadata so queries can be
scoped to a single paper and results reassembled in reading order.

Papers are identified by their citekey (not corpus list-index): the corpus is
re-gathered and re-keyed between runs, so position is not a stable identity —
keying by citekey keeps the index aligned with the source it actually holds.
"""

from __future__ import annotations

import re
import sys

_CHUNK_CHARS = 1800       # max chars per chunk (≈ 450 tokens — leaves room for query + output)
_COLLECTION_NAME = "papers"


def get_collection(chroma_dir):
    import chromadb
    chroma_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(chroma_dir))
    return client.get_or_create_collection(_COLLECTION_NAME)


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
        r = collection.query(
            query_embeddings=[q_emb],
            where={"citekey": citekey},
            n_results=1,
            include=["documents", "metadatas"],
        )
        docs = r.get("documents", [[]])[0]
        metas = r.get("metadatas", [[]])[0]
        if not docs:
            continue
        meta, chunk = metas[0], docs[0]
        chunk_idx = meta.get("chunk_idx", 0)
        if chunk_idx in seen_chunks:
            continue  # skip duplicate chunk across claims
        seen_chunks.add(chunk_idx)
        page = meta.get("page", "?")
        quote = _best_sentence(chunk, claim)
        results.append({
            "claim": claim[:300],
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

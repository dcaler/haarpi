"""PDF helpers: extract text and verify it's real full text.

THE LOCAL CACHE. The corpus PDFs live on the NAS (`litReview/pdfs/`), and oddjob reaches the
NAS over WiFi. A report read every PDF from there several times over — once to extract text,
again for page-marked text each time notes, indexing or locate asked — and DigiPros spent many
minutes in NFS reads before the GPU saw any work. So each NAS PDF gets one copy on the local
SSD, and its extracted text is cached beside it. A copy is trusted only while the NAS file's
size and mtime match what was copied, which costs one `stat` over the network instead of the
whole file. Everything here is best-effort: any cache failure falls back to reading the NAS
file directly, exactly as before.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

CACHE_ROOT = Path(os.environ.get("HAARPI_PDF_CACHE")
                  or Path.home() / ".cache" / "haarpi" / "pdfs")
_TEXT_VERSION = 1      # bump when the extraction changes, so cached text is redone


def _slot(path: Path) -> Path:
    return CACHE_ROOT / hashlib.sha1(os.path.abspath(path).encode()).hexdigest()[:20]


def _read_json(fp: Path) -> dict:
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_json(fp: Path, obj: dict) -> None:
    tmp = fp.with_name(f"{fp.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(obj), encoding="utf-8")
    os.replace(tmp, fp)


def _stat_key(path: Path) -> dict | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return {"src": os.path.abspath(path), "size": st.st_size, "mtime_ns": st.st_mtime_ns}


def adopt(path: Path, data: bytes) -> None:
    """Seed the local copy of a PDF just written to `path`, from the bytes already in hand,
    so it is never read back over the network."""
    key = _stat_key(path)
    if key is None:
        return
    try:
        slot = _slot(path)
        slot.mkdir(parents=True, exist_ok=True)
        tmp = slot / f"file.pdf.{os.getpid()}.tmp"
        tmp.write_bytes(data)
        os.replace(tmp, slot / "file.pdf")
        (slot / "text.json").unlink(missing_ok=True)
        _write_json(slot / "meta.json", key)
    except OSError:
        pass


def local_copy(path: Path) -> Path:
    """The local SSD copy of `path`, refreshed if the NAS file changed. `path` itself when the
    file is missing or the cache cannot be used."""
    path = Path(path)
    key = _stat_key(path)
    if key is None:
        return path
    try:
        slot = _slot(path)
        local = slot / "file.pdf"
        if _read_json(slot / "meta.json") == key and local.exists():
            return local
        slot.mkdir(parents=True, exist_ok=True)
        adopt(path, path.read_bytes())
        return local if _read_json(slot / "meta.json") == key else path
    except OSError:
        return path


def _cached_text(path: Path, kind: str, make):
    """`make(local_pdf)`, memoised beside the local copy for as long as the copy is current."""
    local = local_copy(Path(path))
    if local == Path(path):                 # no usable cache: straight through
        return make(local)
    fp = local.parent / "text.json"
    cache = _read_json(fp)
    if cache.get("v") != _TEXT_VERSION:
        cache = {"v": _TEXT_VERSION}
    if kind in cache:
        return cache[kind]
    val = make(local)
    if val[0] if isinstance(val, list) else val:   # never pin a failed extraction
        cache[kind] = val
        try:
            _write_json(fp, cache)
        except OSError:
            pass
    return val


def _extract_text(path: Path) -> tuple[str, int]:
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return "", 0
    try:
        doc = fitz.open(path)
    except Exception:  # noqa: BLE001
        return "", 0
    pages = [p.get_text() for p in doc]
    n = doc.page_count
    doc.close()
    return "\n".join(pages), n


def extract_text(path: Path) -> tuple[str, int]:
    """Return (text, n_pages). Empty text on failure."""
    text, n = _cached_text(path, "extract", lambda p: list(_extract_text(p)))
    return text, n


def _page_marked_text(path: Path) -> str:
    try:
        import fitz
        doc = fitz.open(path)
    except Exception:  # noqa: BLE001
        return ""
    out = []
    for i, page in enumerate(doc, 1):
        out.append(f"[p.{i}]\n{page.get_text()}")
    doc.close()
    return "\n\n".join(out)


def page_marked_text(path: Path) -> str:
    """Full text with [p.N] markers so the LLM can give page-level pointers."""
    return _cached_text(path, "marked", _page_marked_text)


# What this gate is FOR: rejecting an abstract-only or preview PDF, where we got a
# teaser instead of the paper. It is a capability check, not a source policy — policy
# lives at `gather` (filters.item_type_allowed), and anything a person filed in the
# collection is theirs to decide. So this must not become a way of excluding short
# work: a genuine 3-page paper is a paper.
#
# The REFERENCES SECTION is the discriminator doing the real work. A preview stops
# before the bibliography; a complete short paper carries one. The word count beside it
# is only a floor against a bare abstract that happens to say "references" in passing,
# so it wants to sit above abstract length (150–300 words) and nowhere near paper
# length. It was 600, which rejected a fully-extracted 3-page JASSS corrigendum at 564
# words — a 36-word miss that had nothing to do with whether we had the whole document.
SHORT_PAPER_MIN_WORDS = 400


def looks_like_fulltext(text: str, n_pages: int) -> bool:
    """Heuristic: reject abstract-only / preview PDFs."""
    words = len(text.split())
    if words >= 1500 or n_pages >= 4:
        return True
    # short but has a references section -> probably a short full paper
    if words >= SHORT_PAPER_MIN_WORDS and re.search(r"\b(references|bibliography)\b",
                                                    text, re.I):
        return True
    return False

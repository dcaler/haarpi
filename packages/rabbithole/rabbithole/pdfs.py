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
from collections import Counter
import json
import os
import re
from pathlib import Path

CACHE_ROOT = Path(os.environ.get("HAARPI_PDF_CACHE")
                  or Path.home() / ".cache" / "haarpi" / "pdfs")
_TEXT_VERSION = 3      # bump when the extraction changes, so cached text is redone
                       # 2 (2026-11-09): superscript citation numbers kept inline as [44]
                       # 3 (2026-11-13): a unit's exponent (km²) kept as one, not a citation


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


# SUPERSCRIPT CITATIONS. Nature-style papers cite by raised reference numbers ("a collective
# benefit⁴⁴"). They are set smaller and higher than the line, so plain extraction lifts them out
# of their sentence: de Visser & Krug 2014 came out as "…a collective benefit\n. This contrast…"
# with the numbers in stray clusters ("44 45 42 44 44") elsewhere. Without them, a passage that
# reports a cited study reads as the paper's own. So each page is rebuilt from its characters
# (identical to plain extraction otherwise) and every superscript reference number is written
# back where it sits, as "[44]". An exponent after a digit (10³) is left as it was, and so is a
# number that would land inside a word (a rotated watermark) or on a slanted line. In a paper
# that cites by author and year, raised numbers are footnote markers, not citations, and are
# written "[note:3]" so a passage does not read as citing anyone. A raised 2 or 3 right after a
# unit of length is that unit's exponent, not a citation: "million km²" (IPCC 2013 had come out as
# "km[2]"), written back in place as the superscript character.
_AUTHOR_YEAR = re.compile(r"\([A-Z][^()]{0,80}?\b(?:19|20)\d\d[a-z]?\b")
_AUTHOR_YEAR_MIN = 15   # this many "(Name ... 1999" citations: an author-year paper
_SUP_REF = re.compile(r"^\d{1,3}(?:\s*[,–-]\s*\d{1,3})*$")
_SUP_SIZE = 0.85        # a superscript is set at most this fraction of the body size
_UNITS = {"m", "km", "cm", "mm", "µm", "μm", "nm", "ft", "mi", "yd"}   # squared or cubed (² ³)
_SUPER = {"2": "²", "3": "³"}


def _word_before(near: list, x0: float) -> str:
    """The word the raised run sits against, if it touches it ("" if there is a gap)."""
    row = sorted(near, key=lambda c: c[3])
    if not row or x0 - row[-1][3] > 1.5:
        return ""
    w, edge = "", x0
    for c in reversed(row):            # `near` holds no spaces, so a gap is where the word starts
        if edge - c[3] > 1.5 or not (c[5].isalpha() or c[5] in "µμ"):
            break
        w, edge = c[5] + w, c[1]
    return w


def _flat(ln) -> bool:
    dx, dy = ln.get("dir", (1, 0))
    return dx > 0.99 and abs(dy) < 0.01


def _page_text(page, notes: bool = False) -> str:
    raw = page.get_text("rawdict")
    lines = [ln for b in raw.get("blocks", []) if b.get("type") == 0 for ln in b.get("lines", [])]
    sizes = Counter()
    for ln in lines:
        for sp in ln["spans"]:
            sizes[round(sp["size"], 1)] += len(sp["chars"])
    if not sizes:
        return page.get_text()
    small = sizes.most_common(1)[0][0] * _SUP_SIZE
    # Every character, as (line, x0, y0, x1, y1, char, is_small).
    chars = [[(li, *c["bbox"], c["c"], sp["size"] <= small) for sp in ln["spans"] for c in sp["chars"]]
             for li, ln in enumerate(lines)]
    # Runs of small digits, commas and dashes, side by side on one source line: one citation each.
    runs, moved = [], set()
    for li, row in enumerate(chars):
        cur: list[int] = []
        for k, ch in enumerate(row + [None]):
            ok = ch is not None and ch[6] and (ch[5].isdigit() or ch[5] in ",–- ")
            if ok and cur and ch[1] - row[cur[-1]][3] > 3:
                runs.append((li, cur)); cur = []
            if ok:
                cur.append(k)
            elif cur:
                runs.append((li, cur)); cur = []
    inserts: dict[int, list[tuple[float, str]]] = {}
    for li, ks in runs:
        t = "".join(chars[li][k][5] for k in ks).strip()
        if not _SUP_REF.match(t) or not _flat(lines[li]):
            continue
        x0 = min(chars[li][k][1] for k in ks)
        y0 = min(chars[li][k][2] for k in ks)
        y1 = max(chars[li][k][4] for k in ks)
        best, overlap = None, 0.0
        for lj, row in enumerate(chars):
            big = [c for c in row if not c[6] and c[5].strip()]
            if not big or not _flat(lines[lj]):
                continue
            bx0, bx1 = min(c[1] for c in big), max(c[3] for c in big)
            by0, by1 = min(c[2] for c in big), max(c[4] for c in big)
            if not (bx0 - 2 <= x0 <= bx1 + 4) or y0 > by1 or y1 < by0:
                continue
            ov = min(y1, by1) - max(y0, by0)
            if ov > overlap and (y0 + y1) / 2 < (by0 + by1) / 2:     # raised against that line
                best, overlap = lj, ov
        if best is None:
            continue
        before = [c for c in chars[best] if not c[6] and c[3] <= x0 + 1]
        after = [c for c in chars[best] if not c[6] and c[1] >= x0 - 1]
        near = [c for k, c in enumerate(chars[best]) if (best, k) not in moved and c[5].strip()
                and c[3] <= x0 + 1]
        if near and max(near, key=lambda c: c[3])[5].isdigit():   # 10³, or a table: not a citation
            continue
        if before and after and before[-1][5].isalnum() and after[0][5].isalnum():
            continue                                              # inside a word: not a citation
        moved.update((li, k) for k in ks)
        num = re.sub(r"\s+", "", t)
        if num in _SUPER and _word_before(near, x0) in _UNITS:     # km²: the unit's exponent
            inserts.setdefault(best, []).append((x0, _SUPER[num]))
            continue
        inserts.setdefault(best, []).append((x0, f"[note:{num}]" if notes else f"[{num}]"))
    out = []
    for li, row in enumerate(chars):
        kept = [(c[1], c[5]) for k, c in enumerate(row) if (li, k) not in moved]
        for x, tag in sorted(inserts.get(li, [])):
            n = next((i for i, (cx, _) in enumerate(kept) if cx >= x), len(kept))
            kept.insert(n, (x, tag))
        line = "".join(c for _, c in kept)
        if row and not line.strip() and any((li, k) in moved for k in range(len(row))):
            continue                                              # held only moved numbers
        out.append(line + "\n")
    return "".join(out)


def _doc_pages(doc) -> list[str]:
    """Each page's text, raised numbers as citations, or as notes in an author-year paper."""
    plain = "".join(p.get_text() for p in doc)
    notes = len(_AUTHOR_YEAR.findall(plain)) >= _AUTHOR_YEAR_MIN
    return [_page_text(p, notes) for p in doc]


def _extract_text(path: Path) -> tuple[str, int]:
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return "", 0
    try:
        doc = fitz.open(path)
    except Exception:  # noqa: BLE001
        return "", 0
    pages = _doc_pages(doc)
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
    for i, text in enumerate(_doc_pages(doc), 1):
        out.append(f"[p.{i}]\n{text}")
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

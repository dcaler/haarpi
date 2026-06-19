"""Free academic discovery APIs: OpenAlex, Crossref, Semantic Scholar, arXiv,
plus Unpaywall for open-access PDF resolution. No browser, no paid keys.

Each search function returns a list[Candidate]; failures degrade to [] with a
warning rather than crashing the run.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET

import httpx

from .models import Author, Candidate, norm_doi, _norm_title

TIMEOUT = httpx.Timeout(30.0)


def _warn(msg: str) -> None:
    print(f"  [warn] {msg}", file=sys.stderr)


def _client(email: str) -> httpx.Client:
    ua = f"rabbitHole/0.1 (mailto:{email})" if email else "rabbitHole/0.1"
    return httpx.Client(timeout=TIMEOUT, headers={"User-Agent": ua}, follow_redirects=True)


# ──────────────────────────────────────────────────────────────────────────
# OpenAlex
# ──────────────────────────────────────────────────────────────────────────
def _openalex_abstract(inv: dict | None) -> str:
    if not inv:
        return ""
    positions: list[tuple[int, str]] = []
    for word, idxs in inv.items():
        for i in idxs:
            positions.append((i, word))
    positions.sort()
    return " ".join(w for _, w in positions)


def _openalex_keywords(w: dict) -> list[str]:
    kws: list[str] = []
    for c in w.get("concepts", []) or []:
        if (c.get("score") or 0) >= 0.3 and c.get("display_name"):
            kws.append(c["display_name"])
    for k in w.get("keywords", []) or []:
        name = k.get("display_name") or k.get("keyword")
        if name:
            kws.append(name)
    return kws


def search_openalex(query: str, limit: int, email: str,
                    year_from: int | None = None, year_to: int | None = None,
                    sort: str = "relevance_score:desc") -> list[Candidate]:
    params = {
        "search": query,
        "per-page": min(limit, 200),
        "sort": sort,
    }
    if email:
        params["mailto"] = email
    filters = ["language:en"]
    if year_from:
        filters.append(f"from_publication_date:{year_from}-01-01")
    if year_to:
        filters.append(f"to_publication_date:{year_to}-12-31")
    if filters:
        params["filter"] = ",".join(filters)

    out: list[Candidate] = []
    try:
        with _client(email) as c:
            r = c.get("https://api.openalex.org/works", params=params)
            r.raise_for_status()
            data = r.json()
    except Exception as e:  # noqa: BLE001
        _warn(f"OpenAlex failed: {e}")
        return out

    return [_openalex_work_to_candidate(w) for w in data.get("results", [])]


def _openalex_work_to_candidate(w: dict) -> Candidate:
    authors = [_split_name((a.get("author") or {}).get("display_name", ""))
               for a in w.get("authorships", [])]
    src = (w.get("primary_location") or {}).get("source") or {}
    oa = w.get("open_access") or {}
    best = w.get("best_oa_location") or {}
    return Candidate(
        title=w.get("display_name", "") or "",
        authors=authors,
        year=w.get("publication_year"),
        venue=src.get("display_name", "") or "",
        doi=w.get("doi", "") or "",
        url=(w.get("primary_location") or {}).get("landing_page_url", "") or w.get("id", ""),
        abstract=_openalex_abstract(w.get("abstract_inverted_index")),
        keywords=_openalex_keywords(w),
        publisher=src.get("host_organization_name", "") or "",
        oa_status=oa.get("oa_status", "") or "",
        oa_pdf_url=best.get("pdf_url", "") or "",
        cited_by_count=w.get("cited_by_count", 0) or 0,
        item_type=w.get("type", "journal-article") or "journal-article",
        language=w.get("language", "") or "",
        source="openalex",
    )


def _openalex_fetch_by_ids(c: httpx.Client, ids: list[str], email: str) -> list[Candidate]:
    out: list[Candidate] = []
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        try:
            r = c.get("https://api.openalex.org/works",
                      params={"filter": f"openalex_id:{'|'.join(chunk)}",
                              "per-page": 50, "mailto": email})
            if r.status_code == 200:
                out += [_openalex_work_to_candidate(w) for w in r.json().get("results", [])]
        except Exception:  # noqa: BLE001
            continue
    return out


def openalex_snowball(seed_dois: list[str], email: str,
                      max_seeds: int = 8, per_seed: int = 15) -> list[Candidate]:
    """Expand from seed DOIs via OpenAlex citation trails (references + citers)."""
    out: list[Candidate] = []
    try:
        with _client(email) as c:
            for doi in seed_dois[:max_seeds]:
                try:
                    r = c.get(f"https://api.openalex.org/works/doi:{doi}",
                              params={"mailto": email} if email else {})
                    if r.status_code != 200:
                        continue
                    w = r.json()
                except Exception:  # noqa: BLE001
                    continue
                wid = (w.get("id") or "").rsplit("/", 1)[-1]
                refs = [u.rsplit("/", 1)[-1] for u in (w.get("referenced_works") or [])][:per_seed]
                if refs:
                    out += _openalex_fetch_by_ids(c, refs, email)
                if wid:
                    try:
                        rc = c.get("https://api.openalex.org/works",
                                   params={"filter": f"cites:{wid}",
                                           "sort": "cited_by_count:desc",
                                           "per-page": per_seed, "mailto": email})
                        if rc.status_code == 200:
                            out += [_openalex_work_to_candidate(x)
                                    for x in rc.json().get("results", [])]
                    except Exception:  # noqa: BLE001
                        pass
    except Exception as e:  # noqa: BLE001
        _warn(f"OpenAlex snowball failed: {e}")
    return out


# ── single-reference resolution (for `ingest`) ───────────────────────────────
def title_overlap(a: str, b: str) -> float:
    """Jaccard overlap of normalised title words — cheap fuzzy title match."""
    wa, wb = set(_norm_title(a).split()), set(_norm_title(b).split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def resolve_by_doi(doi: str, email: str) -> Candidate | None:
    """Full metadata for a known DOI, via OpenAlex (Crossref fallback)."""
    doi = norm_doi(doi)
    if not doi:
        return None
    try:
        with _client(email) as c:
            r = c.get(f"https://api.openalex.org/works/doi:{doi}",
                      params={"mailto": email} if email else {})
            if r.status_code == 200:
                return _openalex_work_to_candidate(r.json())
    except Exception as e:  # noqa: BLE001
        _warn(f"openalex DOI resolve failed for {doi}: {e}")
    try:
        hits = search_crossref(doi, 1, email)
        if hits:
            return hits[0]
    except Exception:  # noqa: BLE001
        pass
    return None


def resolve_by_title(title: str, email: str, year: int | None = None,
                     min_overlap: float = 0.6) -> Candidate | None:
    """Best metadata match for a free-text reference with no DOI.

    Searches Crossref then OpenAlex and keeps the closest title match (Jaccard >=
    min_overlap), nudged by year agreement. Returns None when nothing matches well
    enough — a wrong match is worse than reporting the reference as unresolved."""
    title = (title or "").strip()
    if not title:
        return None
    cands: list[Candidate] = []
    for fn in (search_crossref, search_openalex):
        try:
            cands += fn(title, 3, email)
        except Exception:  # noqa: BLE001
            continue
        if cands:
            break
    best, best_score = None, 0.0
    for c in cands:
        s = title_overlap(title, c.title)
        if year and c.year and abs(c.year - year) > 1:
            s -= 0.2
        if s > best_score:
            best, best_score = c, s
    return best if best_score >= min_overlap else None


def _split_name(name: str) -> Author:
    """'Jane Q. Smith' -> Author(given='Jane Q.', family='Smith')."""
    name = (name or "").strip()
    if not name:
        return Author()
    parts = name.split()
    if len(parts) == 1:
        return Author(family=parts[0])
    return Author(given=" ".join(parts[:-1]), family=parts[-1])


# ──────────────────────────────────────────────────────────────────────────
# Crossref
# ──────────────────────────────────────────────────────────────────────────
def search_crossref(query: str, limit: int, email: str,
                    year_from: int | None = None, year_to: int | None = None) -> list[Candidate]:
    params = {"query": query, "rows": min(limit, 100), "select":
              "DOI,title,author,issued,container-title,publisher,abstract,"
              "is-referenced-by-count,URL,type,subject"}
    if email:
        params["mailto"] = email
    if year_from or year_to:
        lo = f"{year_from}-01-01" if year_from else "1000-01-01"
        hi = f"{year_to}-12-31" if year_to else "3000-12-31"
        params["filter"] = f"from-pub-date:{lo},until-pub-date:{hi}"

    out: list[Candidate] = []
    try:
        with _client(email) as c:
            r = c.get("https://api.crossref.org/works", params=params)
            r.raise_for_status()
            items = r.json().get("message", {}).get("items", [])
    except Exception as e:  # noqa: BLE001
        _warn(f"Crossref failed: {e}")
        return out

    for it in items:
        authors = [Author(family=a.get("family", "") or a.get("name", ""),
                          given=a.get("given", "")) for a in it.get("author", [])]
        year = None
        dp = (it.get("issued") or {}).get("date-parts") or [[None]]
        if dp and dp[0]:
            year = dp[0][0]
        abstract = it.get("abstract", "") or ""
        abstract = _strip_jats(abstract)
        out.append(Candidate(
            title=(it.get("title") or [""])[0],
            authors=authors,
            year=year,
            venue=(it.get("container-title") or [""])[0],
            doi=it.get("DOI", "") or "",
            url=it.get("URL", "") or "",
            abstract=abstract,
            keywords=it.get("subject", []) or [],
            publisher=it.get("publisher", "") or "",
            cited_by_count=it.get("is-referenced-by-count", 0) or 0,
            item_type=it.get("type", "journal-article") or "journal-article",
            source="crossref",
        ))
    return out


def _strip_jats(s: str) -> str:
    import re
    return re.sub(r"<[^>]+>", "", s).strip()


# ──────────────────────────────────────────────────────────────────────────
# Semantic Scholar
# ──────────────────────────────────────────────────────────────────────────
def search_semantic_scholar(query: str, limit: int, email: str,
                            api_key: str = "") -> list[Candidate]:
    fields = ("title,year,abstract,venue,authors,externalIds,openAccessPdf,"
              "citationCount,publicationTypes,publicationVenue,"
              "fieldsOfStudy,s2FieldsOfStudy,url,paperId")
    params = {"query": query, "limit": min(limit, 100), "fields": fields}
    headers = {"x-api-key": api_key} if api_key else {}
    import time as _t
    out: list[Candidate] = []
    items = None
    attempts = 6
    try:
        with _client(email) as c:
            for attempt in range(attempts):
                r = c.get("https://api.semanticscholar.org/graph/v1/paper/search",
                          params=params, headers=headers)
                if r.status_code == 429:           # shared pool rate limit
                    if attempt == attempts - 1:
                        break
                    # Honor Retry-After if given, else exponential backoff (cap 30s).
                    ra = (r.headers.get("Retry-After") or "").strip()
                    delay = float(ra) if ra.isdigit() else min(5 * 2 ** attempt, 30)
                    _t.sleep(delay)
                    continue
                r.raise_for_status()
                items = r.json().get("data", []) or []
                break
    except Exception as e:  # noqa: BLE001
        _warn(f"Semantic Scholar failed: {e}")
        return out
    if items is None:
        hint = "" if api_key else " — set [semantic_scholar] api_key for a higher quota"
        _warn(f"Semantic Scholar rate-limited (429) after retries — skipping{hint}.")
        return out

    for it in items:
        authors = [_split_name(a.get("name", "")) for a in (it.get("authors") or [])]
        ext = it.get("externalIds") or {}
        oa = it.get("openAccessPdf") or {}
        pv = it.get("publicationVenue") or {}
        ptypes = it.get("publicationTypes") or []
        fos = it.get("fieldsOfStudy") or [
            f.get("category") for f in (it.get("s2FieldsOfStudy") or []) if f.get("category")]
        out.append(Candidate(
            title=it.get("title", "") or "",
            authors=authors,
            year=it.get("year"),
            venue=it.get("venue", "") or "",
            doi=ext.get("DOI", "") or "",
            url=(oa.get("url", "")
                 or (f"https://doi.org/{ext.get('DOI')}" if ext.get("DOI") else "")
                 or it.get("url", "")
                 or (f"https://www.semanticscholar.org/paper/{it['paperId']}"
                     if it.get("paperId") else "")),
            abstract=it.get("abstract", "") or "",
            keywords=[k for k in fos if k],
            publisher=pv.get("publisher", "") or "",
            oa_pdf_url=oa.get("url", "") or "",
            oa_status="green" if oa else "",
            cited_by_count=it.get("citationCount", 0) or 0,
            item_type=(ptypes[0].lower() if ptypes else "journal-article"),
            source="semantic_scholar",
        ))
    return out


# ──────────────────────────────────────────────────────────────────────────
# arXiv (Atom XML)
# ──────────────────────────────────────────────────────────────────────────
_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV = "{http://arxiv.org/schemas/atom}"


def search_arxiv(query: str, limit: int, email: str) -> list[Candidate]:
    params = {"search_query": f"all:{query}", "start": 0,
              "max_results": min(limit, 100),
              "sortBy": "relevance", "sortOrder": "descending"}
    out: list[Candidate] = []
    try:
        with _client(email) as c:
            r = c.get("http://export.arxiv.org/api/query", params=params)
            r.raise_for_status()
            root = ET.fromstring(r.text)
    except Exception as e:  # noqa: BLE001
        _warn(f"arXiv failed: {e}")
        return out

    for entry in root.findall(f"{_ATOM}entry"):
        title = (entry.findtext(f"{_ATOM}title") or "").strip().replace("\n", " ")
        summary = (entry.findtext(f"{_ATOM}summary") or "").strip().replace("\n", " ")
        published = entry.findtext(f"{_ATOM}published") or ""
        year = int(published[:4]) if published[:4].isdigit() else None
        authors = [_split_name((a.findtext(f"{_ATOM}name") or "").strip())
                   for a in entry.findall(f"{_ATOM}author")]
        doi = entry.findtext(f"{_ARXIV}doi") or ""
        abs_url = entry.findtext(f"{_ATOM}id") or ""
        cats = [c.get("term") for c in entry.findall(f"{_ATOM}category") if c.get("term")]
        pdf_url = ""
        for link in entry.findall(f"{_ATOM}link"):
            if link.get("title") == "pdf" or link.get("type") == "application/pdf":
                pdf_url = link.get("href", "")
        out.append(Candidate(
            title=title, authors=authors, year=year, venue="arXiv",
            doi=doi, url=abs_url, abstract=summary, keywords=cats, publisher="arXiv",
            oa_pdf_url=pdf_url or (abs_url.replace("/abs/", "/pdf/") if abs_url else ""),
            oa_status="green", item_type="preprint", source="arxiv",
        ))
    return out


# ──────────────────────────────────────────────────────────────────────────
# Unpaywall — resolve a legal OA PDF for a DOI
# ──────────────────────────────────────────────────────────────────────────
def unpaywall_pdf(doi: str, email: str) -> str:
    if not doi or not email:
        return ""
    try:
        with _client(email) as c:
            r = c.get(f"https://api.unpaywall.org/v2/{doi}", params={"email": email})
            if r.status_code != 200:
                return ""
            data = r.json()
    except Exception:  # noqa: BLE001
        return ""
    best = data.get("best_oa_location") or {}
    return best.get("url_for_pdf", "") or ""

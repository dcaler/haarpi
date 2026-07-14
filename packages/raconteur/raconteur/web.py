"""Venue web research: OpenAlex Sources API, WikiCFP, direct page fetches."""

from __future__ import annotations

import calendar
import html as html_lib
import re
import sys
from datetime import date
from typing import Any

import httpx

TIMEOUT = httpx.Timeout(20.0, connect=10.0)
_DEADLINE_MONTHS = 8


def _client(email: str) -> httpx.Client:
    ua = f"raconteur/0.1 (mailto:{email})" if email else "raconteur/0.1"
    return httpx.Client(
        timeout=TIMEOUT,
        headers={"User-Agent": ua},
        follow_redirects=True,
    )


def _strip_html(text: str, max_chars: int = 3000) -> str:
    text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.DOTALL)
    text = re.sub(r"<style[^>]*>.*?</style>", " ", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def _add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    year = d.year + m // 12
    month = m % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _parse_date(s: str) -> date | None:
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d", "%d %b %Y", "%d %B %Y"):
        try:
            from datetime import datetime
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _pdf_text(data: bytes, max_chars: int) -> str:
    """The text of a PDF. A call for papers is very often a PDF, not a web page."""
    try:
        import fitz                                     # PyMuPDF
    except ImportError:
        print("[web] the page is a PDF and PyMuPDF is not installed", file=sys.stderr)
        return ""
    try:
        with fitz.open(stream=data, filetype="pdf") as doc:
            text = "\n".join(page.get_text() for page in doc)
    except Exception as e:  # noqa: BLE001
        print(f"[web] could not read the PDF: {e}", file=sys.stderr)
        return ""
    return re.sub(r"[ \t]+", " ", text).strip()[:max_chars]


def fetch_page_text(url: str, email: str, max_chars: int = 3000) -> str:
    """Fetch a URL and return its text — from HTML or from a PDF.

    Read the bytes, not the extension: a CFP is served from every kind of URL, and
    `…/CSS2026_CFP_Draft.docx-2.pdf` is a real one. Handed a PDF, an HTML stripper returns
    the mangled remains of a binary stream — from which a model will confidently extract a
    page limit that is not in the document.
    """
    try:
        with _client(email) as client:
            r = client.get(url, timeout=30)
            if r.status_code != 200:
                print(f"[web] {url} -> HTTP {r.status_code}", file=sys.stderr)
                return ""
            ctype = (r.headers.get("content-type") or "").lower()
            if "pdf" in ctype or r.content[:5] == b"%PDF-":
                return _pdf_text(r.content, max_chars)
            return _strip_html(r.text, max_chars)
    except Exception as e:  # noqa: BLE001
        print(f"[web] fetch failed for {url}: {e}", file=sys.stderr)
    return ""


def openalex_source(name: str, email: str) -> dict[str, Any]:
    """Fetch OpenAlex source metadata for a journal by name."""
    try:
        params: dict[str, Any] = {"search": name, "per-page": 1}
        if email:
            params["mailto"] = email
        with _client(email) as client:
            r = client.get("https://api.openalex.org/sources", params=params)
        data = r.json()
        results = data.get("results", [])
        if not results:
            return {}
        s = results[0]
        stats = s.get("summary_stats", {})
        return {
            "display_name": s.get("display_name", ""),
            "type": s.get("type", ""),
            "issn": s.get("issn_l", ""),
            "h_index": stats.get("h_index"),
            "impact_factor": stats.get("2yr_mean_citedness"),
            "works_count": s.get("works_count"),
            "apc_usd": s.get("apc_usd"),
            "is_oa": s.get("is_oa", False),
            "homepage_url": s.get("homepage_url", ""),
            "in_doaj": s.get("is_in_doaj", False),
        }
    except Exception as e:
        print(f"[web] openalex failed for {name!r}: {e}", file=sys.stderr)
        return {}


def wikicfp_conference(query: str, email: str) -> list[dict[str, Any]]:
    """Search WikiCFP for conferences; return those with deadlines in next 8 months."""
    today = date.today()
    deadline_end = _add_months(today, _DEADLINE_MONTHS)

    results: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for year in (str(today.year), str(today.year + 1)):
        try:
            with _client(email) as client:
                r = client.get(
                    "http://www.wikicfp.com/cfp/search",
                    params={"q": query, "year": year, "b": "t"},
                    timeout=30,
                )
            events = _parse_wikicfp_search(r.text)
        except Exception as e:
            print(f"[web] wikicfp search failed ({query!r}, {year}): {e}", file=sys.stderr)
            continue

        for ev in events:
            eid = ev.get("event_id", "")
            if not eid or eid in seen_ids:
                continue
            seen_ids.add(eid)

            detail = _fetch_wikicfp_event(eid, email)
            ev.update(detail)

            dl_str = ev.get("submission_deadline", "")
            dl = _parse_date(dl_str)
            if dl and today <= dl <= deadline_end:
                results.append(ev)
            elif not dl:
                results.append(ev)

            if len(results) >= 6:
                break
        if len(results) >= 6:
            break

    return results


def _parse_wikicfp_search(html: str) -> list[dict[str, Any]]:
    pattern = re.compile(
        r'href=["\']?/cfp/servlet/event\.showcfp\?eventid=(\d+)(?:&[^"\'>\s]*)?["\']?[^>]*>\s*([^<]+?)\s*</a>',
        re.IGNORECASE,
    )
    seen: set[str] = set()
    results = []
    for m in pattern.finditer(html):
        eid = m.group(1)
        name = html_lib.unescape(m.group(2)).strip()
        if eid not in seen and name and len(name) < 80:
            seen.add(eid)
            results.append({"event_id": eid, "name": name})
        if len(results) >= 12:
            break
    return results


def _fetch_wikicfp_event(event_id: str, email: str) -> dict[str, Any]:
    url = f"http://www.wikicfp.com/cfp/servlet/event.showcfp?eventid={event_id}"
    try:
        with _client(email) as client:
            r = client.get(url, timeout=20)
        return _parse_wikicfp_event_page(r.text)
    except Exception as e:
        print(f"[web] wikicfp event {event_id} failed: {e}", file=sys.stderr)
        return {}


def _parse_wikicfp_event_page(html: str) -> dict[str, Any]:
    def extract(label: str) -> str:
        m = re.search(
            rf"{re.escape(label)}\s*</td>\s*<td[^>]*>\s*([^<]+?)\s*</td>",
            html,
            re.IGNORECASE | re.DOTALL,
        )
        return html_lib.unescape(m.group(1)).strip() if m else ""

    result: dict[str, Any] = {}

    full_name = extract("Event") or extract("Conference")
    if full_name:
        result["full_name"] = full_name

    deadline = extract("Submission Deadline") or extract("Abstract Deadline") or extract("Abstract Registration Due")
    if deadline:
        result["submission_deadline"] = deadline

    notification = extract("Notification Due") or extract("Notification")
    if notification:
        result["notification_date"] = notification

    conf_dates = extract("Conference Date") or extract("Event Dates") or extract("When")
    if conf_dates:
        result["conference_dates"] = conf_dates

    location = extract("Place") or extract("Location") or extract("Where")
    if location:
        result["location"] = location

    homepage = extract("Link")
    if homepage:
        m = re.search(r'href=["\']([^"\']+)["\']', homepage)
        result["homepage"] = m.group(1) if m else homepage

    return result

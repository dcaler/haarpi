"""Zotero Web API client (no desktop app, no MCP — runs headless).

Used to:
  * create a collection for the project (gather)
  * read the collection's items + metadata (report)
  * download attached PDFs so we can extract text locally (report)

File *upload* is intentionally not done here — the user adds PDFs to Zotero
manually, which sync to the Zotero cloud; rabbitHole reads them back through
this API.
"""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

import httpx

from .config import GlobalConfig

API = "https://api.zotero.org"

# Connect fast and let the retry loop handle a dead link; read generously, since a PDF
# download can pause between chunks. A 60 s connect timeout was how one item took 182 s.
_TIMEOUT = httpx.Timeout(connect=15.0, read=120.0, write=60.0, pool=15.0)
_RETRY_STATUS = {429, 500, 502, 503, 504}
_BACKOFF_SECS = (5, 10, 20, 40, 60)          # then 60 s apart until the outage deadline


def outage_wait_secs() -> int:
    """How long one Zotero request keeps retrying through a network outage."""
    v = os.environ.get("HAARPI_ZOTERO_OUTAGE_WAIT")
    return int(v) if v else 1800


class _ResilientClient:
    """httpx.Client with retries, so a WiFi blip costs a pause instead of the run.

    oddjob reaches the internet over a USB WiFi dongle whose link drops out in bursts —
    hundreds of connection timeouts in a bad hour. A single failed request used to end a
    whole Zotero ingest (DigiPros and elephantRoom, 26 Sep, both on one DNS failure).

    GET is retried on any transport error and on 429/5xx. POST and PATCH are retried only
    when the request never left (connect errors) or on 429/503, so a reply lost in transit
    can never create a collection twice. Retry-After is honoured."""

    def __init__(self, client: httpx.Client):
        self._c = client

    def get(self, url, **kw):
        return self._send("GET", url, kw)

    def post(self, url, **kw):
        return self._send("POST", url, kw)

    def patch(self, url, **kw):
        return self._send("PATCH", url, kw)

    def _send(self, method: str, url: str, kw: dict):
        idempotent = method == "GET"
        deadline = time.monotonic() + outage_wait_secs()
        attempt = 0
        while True:
            why, retry_after = "", 0.0
            try:
                r = self._c.request(method, url, **kw)
            except httpx.TransportError as e:
                sent = not isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout))
                if sent and not idempotent:
                    raise
                why, r = type(e).__name__, None
                last_exc = e
            else:
                retryable = r.status_code in (_RETRY_STATUS if idempotent else {429, 503})
                if not retryable:
                    return r
                why = f"HTTP {r.status_code}"
                try:
                    retry_after = float(r.headers.get("Retry-After") or 0)
                except ValueError:
                    retry_after = 0.0
            wait = max(_BACKOFF_SECS[min(attempt, len(_BACKOFF_SECS) - 1)], retry_after)
            attempt += 1
            if time.monotonic() + wait > deadline:
                if r is not None:
                    return r                      # the caller's status handling decides
                raise last_exc
            path = re.sub(r"^/(users|groups)/[^/]+", "", url.removeprefix(API))
            print(f"[zotero] {method} {path}: {why} — retry {attempt} in {wait:.0f}s",
                  flush=True)
            time.sleep(wait)


class ZoteroClient:
    def __init__(self, gc: GlobalConfig):
        if not gc.have_zotero:
            raise RuntimeError("Zotero API key / library ID not configured.")
        self.prefix = f"{API}/{gc.zotero_library_type}s/{gc.zotero_library_id}"
        self.headers = {
            "Zotero-API-Version": "3",
            "Zotero-API-Key": gc.zotero_api_key,
        }
        self._client = _ResilientClient(httpx.Client(timeout=_TIMEOUT, headers=self.headers,
                                                     follow_redirects=True))

    # ── collections ──────────────────────────────────────────────────────
    def create_collection(self, name: str) -> str:
        """Find-or-create by name. Only `resolve_collection` should call this, and only when no
        collection is configured anywhere — see Manifest.zotero_collection."""
        existing = self.find_collection(name)
        if existing:
            return existing
        r = self._client.post(f"{self.prefix}/collections",
                              json=[{"name": name}],
                              headers={"Content-Type": "application/json"})
        r.raise_for_status()
        data = r.json()
        ok = data.get("successful", {})
        if "0" in ok:
            return ok["0"]["key"]
        raise RuntimeError(f"collection create returned: {data}")

    def collections(self) -> list[dict]:
        """Every collection in the library, all pages (the API returns at most 100 at a time)."""
        out, start = [], 0
        while True:
            r = self._client.get(f"{self.prefix}/collections",
                                 params={"limit": 100, "start": start})
            r.raise_for_status()
            page = r.json()
            out += page
            if len(page) < 100:
                return out
            start += 100

    def collection_name(self, key: str) -> str | None:
        """The collection's name, or None when no collection has this key (deleted, or a typo)."""
        r = self._client.get(f"{self.prefix}/collections/{key}")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return (r.json().get("data") or {}).get("name") or ""

    def find_collection(self, name: str) -> str:
        """The key of the ONE collection with this name, ignoring case and surrounding space.

        "" when none matches. Raises CollectionError when several do: picking one is how a corpus
        ends up read from the wrong half of a split.
        """
        want = (name or "").strip().lower()
        hits = [c for c in self.collections()
                if (c.get("data", {}).get("name") or "").strip().lower() == want]
        if len(hits) > 1:
            raise CollectionError(
                f"{len(hits)} Zotero collections are named '{name}' (ignoring case): "
                + ", ".join(f"{c['key']} '{c['data']['name']}'" for c in hits)
                + ". Pin the right one as `zotero_collection:` in haarpi.yaml.")
        return hits[0]["key"] if hits else ""

    def similar_collections(self, name: str, exclude: str = "") -> list[tuple[str, str]]:
        """Other collections whose name is this one's apart from case, punctuation or a suffix
        such as "_old" — the trail a split corpus leaves behind."""
        base = re.sub(r"[^a-z0-9]", "", (name or "").lower())
        if not base:
            return []
        return [(c["key"], c["data"]["name"]) for c in self.collections()
                if c["key"] != exclude
                and re.sub(r"[^a-z0-9]", "", (c["data"].get("name") or "").lower()).startswith(base)]

    # ── items ────────────────────────────────────────────────────────────
    def items_by_keys(self, keys: list[str]) -> list[dict]:
        """Fetch specific library items by their Zotero item keys."""
        out = []
        for i in range(0, len(keys), 50):
            chunk = keys[i:i + 50]
            r = self._client.get(f"{self.prefix}/items",
                                 params={"itemKey": ",".join(chunk), "format": "json"})
            r.raise_for_status()
            out += r.json()
        return out

    def search_by_author(self, name: str) -> list[dict]:
        """Top-level library items where name matches a creator."""
        out, start = [], 0
        while True:
            r = self._client.get(f"{self.prefix}/items/top",
                                 params={"q": name, "format": "json",
                                         "limit": 100, "start": start})
            r.raise_for_status()
            batch = r.json()
            if not batch:
                break
            out += batch
            if len(batch) < 100:
                break
            start += 100
        return out

    def search(self, query: str, limit: int = 50) -> list[dict]:
        """Quick top-level library search across title/creator/year (Zotero `q=`)."""
        try:
            r = self._client.get(f"{self.prefix}/items/top",
                                 params={"q": query, "qmode": "titleCreatorYear",
                                         "format": "json", "limit": limit})
            r.raise_for_status()
            return r.json()
        except Exception:  # noqa: BLE001
            return []

    def collection_items(self, collection_key: str) -> list[dict]:
        """Top-level items in the collection (excludes attachments/notes)."""
        out, start = [], 0
        while True:
            r = self._client.get(
                f"{self.prefix}/collections/{collection_key}/items/top",
                params={"limit": 100, "start": start})
            r.raise_for_status()
            batch = r.json()
            if not batch:
                break
            out += batch
            start += len(batch)
            if len(batch) < 100:
                break
        return out

    def library_items(self) -> list[dict]:
        """All top-level items across the whole library (not just one collection)."""
        out, start = [], 0
        while True:
            r = self._client.get(f"{self.prefix}/items/top",
                                 params={"limit": 100, "start": start})
            r.raise_for_status()
            batch = r.json()
            if not batch:
                break
            out += batch
            start += len(batch)
            if len(batch) < 100:
                break
        return out

    def add_item_to_collection(self, item: dict, collection_key: str) -> bool:
        """Add an existing library item to a collection by PATCHing its collections."""
        data = item.get("data", {})
        key = data.get("key") or item.get("key")
        cols = list(data.get("collections", []))
        if collection_key in cols:
            return True
        cols.append(collection_key)
        version = item.get("version") or data.get("version") or 0
        try:
            r = self._client.patch(
                f"{self.prefix}/items/{key}",
                json={"collections": cols},
                headers={"Content-Type": "application/json",
                         "If-Unmodified-Since-Version": str(version)})
            return r.status_code in (200, 204)
        except Exception:  # noqa: BLE001
            return False

    def move_item_between_collections(self, item: dict, from_key: str, to_key: str) -> bool:
        """Move an item from one collection to another by PATCHing its `collections` array —
        the quarantine mechanism. NON-DESTRUCTIVE: the item stays in the library and in any
        OTHER collection it belongs to; only `from_key` is removed and `to_key` added. A no-op
        (no PATCH, returns True) when the item already carries `to_key` and not `from_key`, so
        re-running an audit is idempotent."""
        data = item.get("data", {})
        key = data.get("key") or item.get("key")
        cols = list(data.get("collections", []))
        new = [c for c in cols if c != from_key]
        if to_key not in new:
            new.append(to_key)
        if new == cols:
            return True                       # already where it should be — nothing to do
        version = item.get("version") or data.get("version") or 0
        try:
            r = self._client.patch(
                f"{self.prefix}/items/{key}",
                json={"collections": new},
                headers={"Content-Type": "application/json",
                         "If-Unmodified-Since-Version": str(version)})
            return r.status_code in (200, 204)
        except Exception:  # noqa: BLE001
            return False

    def item_children(self, item_key: str) -> list[dict]:
        r = self._client.get(f"{self.prefix}/items/{item_key}/children")
        r.raise_for_status()
        return r.json()

    def pdf_attachment_key(self, item_key: str) -> str:
        att = self.pdf_attachment(item_key)
        return att["key"] if att else ""

    def pdf_attachment(self, item_key: str) -> dict | None:
        """The item's first PDF attachment as {"key", "md5"}. `md5` is Zotero's hash of the
        stored file ("" for a linked file, which has none), so a caller can tell whether a
        copy it already holds is current without downloading it again."""
        for ch in self.item_children(item_key):
            data = ch.get("data", {})
            if data.get("itemType") == "attachment" and \
               data.get("contentType") == "application/pdf":
                return {"key": ch["key"], "md5": data.get("md5") or ""}
        return None

    def fetch_attachment(self, attachment_key: str) -> bytes | None:
        try:
            r = self._client.get(f"{self.prefix}/items/{attachment_key}/file")
        except Exception:  # noqa: BLE001
            return None
        return r.content if r.status_code == 200 else None

    def download_attachment(self, attachment_key: str, dest: Path) -> bool:
        try:
            r = self._client.get(f"{self.prefix}/items/{attachment_key}/file")
            if r.status_code != 200:
                return False
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(r.content)
            return dest.stat().st_size > 2048
        except Exception:  # noqa: BLE001
            return False

    def fulltext(self, attachment_key: str) -> str:
        """Zotero's own indexed full text (fallback if local extraction fails)."""
        try:
            r = self._client.get(f"{self.prefix}/items/{attachment_key}/fulltext")
            if r.status_code == 200:
                return r.json().get("content", "")
        except Exception:  # noqa: BLE001
            pass
        return ""

    def collection_bibtex(self, collection_key: str) -> str:
        """Export a collection as a BibTeX string (paginates automatically)."""
        parts: list[str] = []
        start = 0
        while True:
            r = self._client.get(
                f"{self.prefix}/collections/{collection_key}/items",
                params={"format": "bibtex", "limit": 100, "start": start})
            r.raise_for_status()
            parts.append(r.text)
            total = int(r.headers.get("Total-Results", "0"))
            start += 100
            if start >= total:
                break
        return "\n".join(parts)


# ── the project's ONE collection ────────────────────────────────────────────────

class CollectionError(RuntimeError):
    """The project's collection cannot be resolved without guessing."""


def resolve_collection(zc, cfg, *, create: bool = False) -> str:
    """The project's collection key. Never invents a second collection.

    A configured key (pinned in haarpi.yaml, adopted into the review config) is used as is, after
    checking that Zotero still has it — a missing key is an error, never a reason to create a
    replacement. With no key: the one collection named like the project, ignoring case; and only
    when there is none at all, and `create` is set (the project's first gather), a new one.
    """
    key = (getattr(cfg, "zotero", None) or {}).get("collection_key", "")
    if key:
        if zc.collection_name(key) is None:
            raise CollectionError(
                f"the configured Zotero collection {key} does not exist. Fix "
                "`zotero_collection:` in haarpi.yaml (or the review config); not creating a "
                "replacement, which would split the corpus.")
        return key
    key = zc.find_collection(cfg.project_name)
    if key:
        return key
    if not create:
        raise CollectionError(f"no Zotero collection is configured for '{cfg.project_name}' "
                              "and none has that name. Run gather first.")
    return zc.create_collection(cfg.project_name)


def warn_similar(zc, cfg, key: str) -> list[tuple[str, str]]:
    """Say so when other collections look like this project's — a split, or its leftovers."""
    try:
        others = zc.similar_collections(cfg.project_name, exclude=key)
    except Exception:  # noqa: BLE001 — a warning must never stop a run
        return []
    if others:
        print(f"  [warn] other Zotero collections look like this project's ({key} is the one in "
              f"use): " + ", ".join(f"{k} '{n}'" for k, n in others)
              + ". If they hold sources, merge them into the one in use.", file=sys.stderr)
    return others

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

from pathlib import Path

import httpx

from .config import GlobalConfig

API = "https://api.zotero.org"


class ZoteroClient:
    def __init__(self, gc: GlobalConfig):
        if not gc.have_zotero:
            raise RuntimeError("Zotero API key / library ID not configured.")
        self.prefix = f"{API}/{gc.zotero_library_type}s/{gc.zotero_library_id}"
        self.headers = {
            "Zotero-API-Version": "3",
            "Zotero-API-Key": gc.zotero_api_key,
        }
        self._client = httpx.Client(timeout=60, headers=self.headers,
                                    follow_redirects=True)

    # ── collections ──────────────────────────────────────────────────────
    def create_collection(self, name: str) -> str:
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

    def find_collection(self, name: str) -> str:
        r = self._client.get(f"{self.prefix}/collections", params={"limit": 100})
        r.raise_for_status()
        for col in r.json():
            if col.get("data", {}).get("name") == name:
                return col["key"]
        return ""

    # ── items ────────────────────────────────────────────────────────────
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

    def item_children(self, item_key: str) -> list[dict]:
        r = self._client.get(f"{self.prefix}/items/{item_key}/children")
        r.raise_for_status()
        return r.json()

    def pdf_attachment_key(self, item_key: str) -> str:
        for ch in self.item_children(item_key):
            data = ch.get("data", {})
            if data.get("itemType") == "attachment" and \
               data.get("contentType") == "application/pdf":
                return ch["key"]
        return ""

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

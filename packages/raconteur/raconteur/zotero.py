from __future__ import annotations
import sys
from pathlib import Path
from .config import ZoteroConfig


class ZoteroClient:
    def __init__(self, cfg: ZoteroConfig) -> None:
        import httpx
        self._cfg = cfg
        prefix = "users" if cfg.library_type == "user" else "groups"
        self._base = f"https://api.zotero.org/{prefix}/{cfg.library_id}"
        self._headers = {"Zotero-API-Key": cfg.api_key, "Zotero-API-Version": "3"}
        self._http = httpx.Client(headers=self._headers, timeout=30)

    def search_by_author(self, name: str) -> list[dict]:
        """Return top-level library items where name matches a creator."""
        items: list[dict] = []
        start = 0
        while True:
            r = self._http.get(
                f"{self._base}/items/top",
                params={"q": name, "qmode": "author", "format": "json",
                        "limit": 100, "start": start},
            )
            r.raise_for_status()
            batch = r.json()
            if not batch:
                break
            items.extend(batch)
            if len(batch) < 100:
                break
            start += 100
        return items

    def pdf_attachment_key(self, item_key: str) -> str | None:
        """Return the key of the first PDF attachment for an item."""
        r = self._http.get(f"{self._base}/items/{item_key}/children")
        r.raise_for_status()
        for ch in r.json():
            d = ch.get("data", {})
            if d.get("itemType") == "attachment" and d.get("contentType") == "application/pdf":
                return d.get("key")
        return None

    def fulltext(self, attachment_key: str) -> str:
        """Return Zotero-indexed full text for an attachment."""
        try:
            r = self._http.get(f"{self._base}/items/{attachment_key}/fulltext")
            if r.status_code == 200:
                return r.json().get("content", "")
        except Exception:
            pass
        return ""

    def close(self) -> None:
        self._http.close()

"""Thin client for the trundlr task queue (https://github.com/dcaler/trundlr).

parseNplan uses this to queue a gather → collect → revise → comment chain after
reading reviewer annotations. Commanded steps (gather, revise) are assigned to
the runner resource and carry a shell command the trundlr runner executes once
their dependency is done; human steps (collect, comment, init) carry no command
and simply wait in the queue until marked done.

Fails soft: every call raises TrundlrError on transport/HTTP failure, and the
caller is expected to fall back to printing the plan + manual commands rather
than crashing the pipeline.
"""

from __future__ import annotations

import httpx

from .config import GlobalConfig


class TrundlrError(RuntimeError):
    pass


class TrundlrClient:
    def __init__(self, gc: GlobalConfig) -> None:
        if not gc.trundlr_url:
            raise TrundlrError("no trundlr_url configured ([trundlr] url in config.toml)")
        self.base = gc.trundlr_url.rstrip("/")
        self.runner_resource_id = gc.trundlr_runner_resource_id
        self._http = httpx.Client(timeout=20)

    # ── projects ─────────────────────────────────────────────────────────────
    def list_projects(self) -> list[dict]:
        return self._get("/api/projects/")

    def project_by_name(self, name: str) -> dict | None:
        """Exact-name match against trundlr projects (case-sensitive)."""
        for p in self.list_projects():
            if p.get("name") == name:
                return p
        return None

    def create_project(self, name: str, folder: str = "", description: str = "") -> dict:
        body = {"name": name}
        if folder:
            body["folder"] = folder
        if description:
            body["description"] = description
        return self._post("/api/projects/", body)

    # ── tasks ────────────────────────────────────────────────────────────────
    def tasks_for_project(self, project_id: int) -> list[dict]:
        return self._get(f"/api/tasks/?project_id={project_id}")

    def all_tasks(self) -> list[dict]:
        return self._get("/api/tasks/")

    def create_task(self, title: str, project_id: int, *, command: str | None = None,
                    depends_on_id: int | None = None, description: str = "",
                    resource_id: int | None = None, duration: float | None = None) -> dict:
        body: dict = {"title": title, "project_id": project_id}
        if command:
            body["command"] = command
        if description:
            body["description"] = description
        if depends_on_id is not None:
            body["depends_on_id"] = depends_on_id
        if resource_id is not None:
            body["resource_ids"] = [resource_id]
        if duration is not None:
            body["duration"] = duration
        return self._post("/api/tasks/", body)

    # ── transport ────────────────────────────────────────────────────────────
    def _get(self, path: str):
        try:
            r = self._http.get(self.base + path)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError as e:
            raise TrundlrError(f"GET {path} failed: {e}") from e

    def _post(self, path: str, body: dict):
        try:
            r = self._http.post(self.base + path, json=body)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError as e:
            detail = ""
            if isinstance(e, httpx.HTTPStatusError):
                detail = f" — {e.response.text[:200]}"
            raise TrundlrError(f"POST {path} failed: {e}{detail}") from e

    def close(self) -> None:
        self._http.close()

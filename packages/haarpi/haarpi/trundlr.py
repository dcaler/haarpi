"""The one trundlr API client (https://github.com/dcaler/trundlr) for the ra* tools.

trundlr is the orchestrator/scheduler the pipeline queues into: commanded steps
carry a shell command its runner executes once their dependency is done; human
steps carry no command and wait in the queue until marked done.

Two surfaces over the same stdlib transport, matching how the tools grew:

  * functions taking an explicit api_url (raster/rayleigh `queue` style)
  * TrundlrClient, a small stateful wrapper (rabbitHole `parseNplan` style)

Fails soft by convention: every call raises TrundlrError on transport/HTTP
failure, and callers fall back to printing the plan + manual commands rather
than crashing the pipeline.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request


def coerce_id(v):
    """A trundlr project id may be a numeric API id OR a project name (the tools
    default it to the project name). Keep an all-digit id as an int; pass a name
    through as-is."""
    s = str(v)
    return int(s) if s.isdigit() else v


class TrundlrError(RuntimeError):
    """An API call failed; carries the server's response body (the 422 validation
    detail is far more useful than a bare 'Unprocessable Entity')."""


def _api(api_url: str, method: str, path: str, body=None, timeout: int = 30):
    url = f"{api_url.rstrip('/')}/api{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return None if resp.status == 204 else json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = (e.read() or b"").decode(errors="replace").strip()
        raise TrundlrError(f"{method} {url} -> HTTP {e.code} {e.reason}"
                           + (f"\n    {detail}" if detail else "")) from None
    except urllib.error.URLError as e:
        raise TrundlrError(f"{method} {url} -> {e.reason}") from None


# ── projects ─────────────────────────────────────────────────────────────────

def set_project_directory(api_url: str, project_id: int, directory: str) -> None:
    """Point the trundlr project at the project root (its `folder`) so queued
    commands run there."""
    _api(api_url, "PATCH", f"/projects/{project_id}", {"folder": directory})


def list_projects(api_url: str) -> list:
    return _api(api_url, "GET", "/projects/") or []


PRIORITY_MIN, PRIORITY_MAX = 1, 4      # trundlr's own bounds; 1 = most urgent
PRIORITY_DEFAULT = 3                   # trundlr's own default band


def clamp_priority(value) -> int:
    """A priority trundlr will accept. Out-of-range or unreadable input falls back
    to trundlr's default band rather than failing the whole init on a typo."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return PRIORITY_DEFAULT
    return max(PRIORITY_MIN, min(PRIORITY_MAX, n))


def create_project(api_url: str, name: str, folder: str = None, description: str = None,
                   priority: int = PRIORITY_DEFAULT) -> dict:
    body = {"name": name, "priority": clamp_priority(priority)}
    if folder:
        body["folder"] = folder
    if description:
        body["description"] = description
    return _api(api_url, "POST", "/projects/", body)


def resolve_project_id(api_url: str, name: str, folder: str = None,
                       description: str = None, create: bool = True,
                       priority: int = PRIORITY_DEFAULT):
    """Resolve a project NAME to trundlr's numeric id (trundlr keys projects by int id).
    Returns (id, created). Matches an existing project by exact name; creates one if
    absent and create=True, else returns (None, False). `priority` applies only to a
    project we create — an existing project's band is the user's, not ours to restate."""
    for p in list_projects(api_url):
        if p.get("name") == name:
            return int(p["id"]), False
    if not create:
        return None, False
    return int(create_project(api_url, name, folder, description, priority)["id"]), True


# ── tasks ────────────────────────────────────────────────────────────────────

def create_task(api_url: str, body: dict) -> dict:
    """Create one trundlr task (the `queue` verbs use this to chain work)."""
    return _api(api_url, "POST", "/tasks/", body)


def update_task(api_url: str, task_id: int, body: dict) -> dict:
    """PATCH one existing trundlr task (e.g. re-budget its `duration`)."""
    return _api(api_url, "PATCH", f"/tasks/{task_id}", body)


def list_tasks(api_url: str, project_id: int | None = None) -> list:
    path = f"/tasks/?project_id={project_id}" if project_id is not None else "/tasks/"
    return _api(api_url, "GET", path) or []


# ── stateful wrapper ─────────────────────────────────────────────────────────

class TrundlrClient:
    """Holds the base URL (and optionally the runner resource id) so chain
    builders don't thread api_url through every call."""

    def __init__(self, api_url: str, runner_resource_id: int | None = None) -> None:
        if not api_url:
            raise TrundlrError("no trundlr url configured ([trundlr] url in config.toml)")
        self.base = api_url.rstrip("/")
        self.runner_resource_id = runner_resource_id

    # projects
    def list_projects(self) -> list[dict]:
        return list_projects(self.base)

    def project_by_name(self, name: str) -> dict | None:
        """Exact-name match against trundlr projects (case-sensitive)."""
        for p in self.list_projects():
            if p.get("name") == name:
                return p
        return None

    def create_project(self, name: str, folder: str = "", description: str = "") -> dict:
        return create_project(self.base, name, folder or None, description or None)

    # tasks
    def tasks_for_project(self, project_id: int) -> list[dict]:
        return list_tasks(self.base, project_id)

    def all_tasks(self) -> list[dict]:
        return list_tasks(self.base)

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
        return create_task(self.base, body)

    def update_task(self, task_id: int, **fields) -> dict:
        """PATCH a task (e.g. duration=1.3 to correct a queued estimate)."""
        return update_task(self.base, task_id, fields)

    def close(self) -> None:
        pass  # kept for callers that close() the httpx-era client

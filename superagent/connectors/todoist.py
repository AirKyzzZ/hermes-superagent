from __future__ import annotations

import re
import time
from collections.abc import Mapping
from dataclasses import dataclass

from ..ledger import SourceItem, Upstream
from . import ConnectorConfigError, ConnectorError, ConnectorSpec, Poll
from .process import run_json

KEYS = frozenset({"td", "timeout", "env"})
EXCERPT_CHARS = 500
PROJECTS_SHARE = 0.25
MIN_PROJECTS_SECONDS = 30.0
TASK_URL = "https://app.todoist.com/app/task/{id}"
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_DEFAULT_ENV = ("TODOIST_API_TOKEN", "DBUS_SESSION_BUS_ADDRESS")
_PRIORITY = {4: "p1", 3: "p2", 2: "p3", 1: "p4"}


@dataclass(frozen=True)
class TodoistConnector:
    td: str
    timeout: float
    env_names: tuple[str, ...]

    def poll(self, cursor: str) -> Poll:
        deadline = time.monotonic() + self.timeout
        projects = self._projects(max(self.timeout * PROJECTS_SHARE, MIN_PROJECTS_SECONDS))
        remaining = deadline - time.monotonic()
        listing = run_json([self.td, "task", "list", "--all", "--json", "--full"], self.env_names, remaining)
        if not isinstance(listing, Mapping) or not isinstance(listing.get("results"), list):
            raise ConnectorError("td task list returned an unexpected shape")
        problems = []
        if listing.get("nextCursor"):
            problems.append("td stopped before the last page of tasks, so the list can't be trusted to be whole")
        items = []
        for entry in listing["results"]:
            try:
                items.append(_task(entry, projects))
            except ConnectorError as error:
                problems.append(str(error))
        return Poll(tuple(items), complete=not problems, problems=tuple(problems))

    def confirm(self, external_id: str) -> Upstream:
        if not _SAFE_ID.fullmatch(external_id):
            raise ConnectorError(f"refusing to pass the id {external_id[:80]!r} to td")
        try:
            task = run_json(
                [self.td, "task", "view", f"id:{external_id}", "--json", "--full"], self.env_names, self.timeout
            )
        except ConnectorError as error:
            text = str(error)
            if "NOT_FOUND" in text or "404" in text or "not found" in text.lower():
                return Upstream.UNKNOWN
            raise
        if not isinstance(task, Mapping) or not isinstance(task.get("checked"), bool):
            raise ConnectorError(f"td returned no completion state for task {external_id}")
        if task.get("isDeleted") is True:
            return Upstream.UNKNOWN
        return Upstream.CLOSED if task["checked"] else Upstream.OPEN

    def _projects(self, timeout: float) -> dict[str, str]:
        try:
            listing = run_json([self.td, "project", "list", "--all", "--json"], self.env_names, timeout)
        except ConnectorError as error:
            raise ConnectorError(f"td couldn't list projects, so no task was recorded this poll: {error}") from error
        results = listing.get("results") if isinstance(listing, Mapping) else None
        if not isinstance(results, list):
            raise ConnectorError("td project list returned an unexpected shape")
        return {
            str(project["id"]): str(project.get("name", ""))
            for project in results
            if isinstance(project, Mapping) and project.get("id") is not None
        }


def _task(entry: object, projects: Mapping[str, str]) -> SourceItem:
    if not isinstance(entry, Mapping):
        raise ConnectorError("td returned a task that isn't an object")
    identifier, content = entry.get("id"), entry.get("content")
    if not isinstance(identifier, str) or not _SAFE_ID.fullmatch(identifier):
        raise ConnectorError(f"td returned a task without a usable id: {str(identifier)[:40]!r}")
    if not isinstance(content, str) or not content.strip():
        raise ConnectorError(f"td returned task {identifier} without a title")
    due, deadline = entry.get("due"), entry.get("deadline")
    recurring = isinstance(due, Mapping) and due.get("isRecurring") is True
    when = ""
    if isinstance(due, Mapping):
        when = str((due.get("string") if recurring else None) or due.get("datetime") or due.get("date") or "")
    elif isinstance(deadline, Mapping):
        when = str(deadline.get("date") or "")
    project_id, priority = str(entry.get("projectId") or ""), entry.get("priority")
    labels = entry.get("labels")
    meta = {
        "project": projects.get(project_id, project_id),
        "project_id": project_id,
        "section_id": str(entry.get("sectionId") or ""),
        "parent_id": str(entry.get("parentId") or ""),
        "labels": ",".join(str(label) for label in labels) if isinstance(labels, list) else "",
        "priority": _PRIORITY.get(priority, "") if isinstance(priority, int) else "",
        "added_at": str(entry.get("addedAt") or ""),
        "added_by": str(entry.get("addedByUid") or ""),
        "assignee": str(entry.get("responsibleUid") or ""),
        "recurring": "true" if recurring else "",
        "deadline": str(deadline.get("date") or "") if isinstance(deadline, Mapping) else "",
    }
    description = entry.get("description")
    return SourceItem(
        external_id=identifier,
        title=" ".join(content.split()),
        url=str(entry.get("url") or TASK_URL.format(id=identifier)),
        due=when,
        updated="" if recurring else str(entry.get("updatedAt") or ""),
        excerpt=description[:EXCERPT_CHARS] if isinstance(description, str) else "",
        meta={key: value for key, value in meta.items() if value},
    )


def build(spec: ConnectorSpec) -> TodoistConnector:
    where = f"[connectors.{spec.name}]"
    td = spec.settings.get("td", "td")
    if not isinstance(td, str) or not td:
        raise ConnectorConfigError(f"{where} td must be a command name or path")
    timeout = spec.settings.get("timeout", 120)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 300:
        raise ConnectorConfigError(f"{where} timeout must be between 0 and 300 seconds")
    env = spec.settings.get("env", [])
    if not isinstance(env, list) or not all(isinstance(name, str) for name in env):
        raise ConnectorConfigError(f"{where} env must be a list of variable names")
    return TodoistConnector(td, float(timeout), (*_DEFAULT_ENV, *env))

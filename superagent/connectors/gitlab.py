from __future__ import annotations

import json
import logging
import re
import time
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass, field

from ..ledger import SourceItem, Upstream
from . import ConnectorConfigError, ConnectorError, ConnectorSpec, Poll
from .process import run_text

logger = logging.getLogger(__name__)

KEYS = frozenset({"queries", "glab", "hostname", "timeout", "env"})
DEFAULT_QUERIES = {
    "assigned": "issues?scope=assigned_to_me&state=opened",
    "merge_requests": "merge_requests?scope=assigned_to_me&state=opened",
    "review": "merge_requests?scope=all&state=opened&reviewer_username={me}",
    "todos": "todos?state=pending",
}
OPEN_STATES = {"issues": "opened", "merge_requests": "opened", "todos": "pending"}
PAGE_SIZE = 100
MAX_PAGES = 50
DONE_CACHE_SECONDS = 120.0
CALL_TIMEOUT_SECONDS = 30.0
NEUTRAL_DIRECTORY = "/"
_ID = re.compile(r"(?P<project>[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+)(?P<kind>[#!])(?P<iid>\d+)")
_TODO_ID = re.compile(r"todo-(\d+)")
_HEADER_END = re.compile(r"\r?\n\r?\n")
_DEFAULT_ENV = ("GITLAB_TOKEN", "GITLAB_HOST", "GLAB_CONFIG_DIR", "DBUS_SESSION_BUS_ADDRESS")
_CLOSED = frozenset({"closed", "merged"})
_COVERED = {("assigned", "Issue"): "issues", ("assigned", "MergeRequest"): "merge_requests"}
_COVERED[("review_requested", "MergeRequest")] = "merge_requests"


@dataclass(frozen=True)
class GitlabConnector:
    queries: Mapping[str, str]
    glab: str
    hostname: str
    timeout: float
    env_names: tuple[str, ...]
    done_pages: dict[int, tuple[float, int, list[int]]] = field(default_factory=dict, compare=False, repr=False)

    def poll(self, cursor: str) -> Poll:
        deadline = time.monotonic() + self.timeout
        found: dict[str, SourceItem] = {}
        matched: dict[str, list[str]] = {}
        problems: list[str] = []
        failed: set[str] = set()
        me = ""
        ordered = sorted(self.queries.items(), key=lambda pair: endpoint(pair[1]) == "todos")
        for name, query in ordered:
            kind = endpoint(query)
            try:
                if "{me}" in query and not me:
                    me = self._username(deadline)
                entries = self._list(query.replace("{me}", urllib.parse.quote(me)), deadline, kind == "todos")
            except ConnectorError as error:
                problems.append(f"query {name}: {error}")
                failed.add(kind)
                continue
            for entry in entries:
                try:
                    item = _todo(entry, set(found), failed) if kind == "todos" else _work_item(entry)
                except ConnectorError as error:
                    problems.append(f"query {name}: {error}")
                    continue
                if item is None:
                    continue
                found[item.external_id] = item
                names = matched.setdefault(item.external_id, [])
                if name not in names:
                    names.append(name)
        items = tuple(
            SourceItem(
                item.external_id,
                item.title,
                url=item.url,
                due=item.due,
                updated=item.updated,
                meta={**item.meta, "matched": ",".join(matched[item.external_id])},
            )
            for item in found.values()
        )
        return Poll(items, complete=not problems, problems=tuple(problems))

    def confirm(self, external_id: str) -> Upstream:
        deadline = time.monotonic() + self.timeout
        todo = _TODO_ID.fullmatch(external_id)
        if todo is not None:
            return Upstream.CLOSED if self._done_todo(int(todo[1]), deadline) else Upstream.UNKNOWN
        match = _ID.fullmatch(external_id)
        if match is None:
            return Upstream.UNKNOWN
        kind = "issues" if match["kind"] == "#" else "merge_requests"
        project = urllib.parse.quote(match["project"], safe="")
        entry = self._api(f"projects/{project}/{kind}/{match['iid']}", deadline)[1]
        if not isinstance(entry, Mapping) or not isinstance(entry.get("state"), str):
            raise ConnectorError(f"GitLab returned no state for {external_id}")
        references = entry.get("references")
        moved = references.get("full") if isinstance(references, Mapping) else None
        if isinstance(moved, str) and moved.lower() != external_id.lower():
            raise ConnectorError(f"{external_id} moved to {moved[:120]}, close the old item by hand")
        return Upstream.CLOSED if entry["state"] in _CLOSED else Upstream.OPEN

    def _done_todo(self, wanted: int, deadline: float) -> bool:
        low, high = 1, self._done_page(1, deadline)[0]
        while low <= high:
            middle = (low + high) // 2
            ids = self._done_page(middle, deadline)[1]
            if wanted in ids:
                return True
            if not ids or wanted > max(ids):
                high = middle - 1
            elif wanted < min(ids):
                low = middle + 1
            else:
                return False
        return False

    def _done_page(self, page: int, deadline: float) -> tuple[int, list[int]]:
        cached = self.done_pages.get(page)
        if cached is not None and time.monotonic() - cached[0] < DONE_CACHE_SECONDS:
            return cached[1], cached[2]
        headers, body = self._api(f"todos?state=done&per_page={PAGE_SIZE}&page={page}", deadline)
        if not isinstance(body, list):
            raise ConnectorError("GitLab returned done todos in an unexpected shape")
        pages = headers.get("x-total-pages", "")
        count = int(pages) if pages.isdigit() else page
        ids = [entry["id"] for entry in body if isinstance(entry, Mapping) and isinstance(entry.get("id"), int)]
        self.done_pages[page] = (time.monotonic(), count, ids)
        return count, ids

    def _username(self, deadline: float) -> str:
        user = self._api("user", deadline)[1]
        name = user.get("username") if isinstance(user, Mapping) else None
        if not isinstance(name, str) or not name:
            raise ConnectorError("GitLab returned no username for this login")
        return name

    def _list(self, query: str, deadline: float, hidden_allowed: bool) -> list[Mapping[str, object]]:
        entries: dict[str, Mapping[str, object]] = {}
        total: int | None = None
        hidden = 0
        joiner = "&" if "?" in query else "?"
        for page in range(1, MAX_PAGES + 1):
            headers, body = self._api(f"{query}{joiner}per_page={PAGE_SIZE}&page={page}", deadline)
            if not isinstance(body, list):
                raise ConnectorError("GitLab returned an unexpected shape")
            count = headers.get("x-total", "")
            if not count.isdigit():
                raise ConnectorError("GitLab sent no total, so the list can't be checked for completeness")
            if total is not None and int(count) != total:
                raise ConnectorError(f"the total moved from {total} to {count} while paging, retrying next poll")
            total = int(count)
            expected = max(0, min(PAGE_SIZE, total - (page - 1) * PAGE_SIZE))
            if hidden_allowed and len(body) < expected:
                hidden += expected - len(body)
                logger.info("GitLab counted %d todos it didn't return on page %d", expected - len(body), page)
            for entry in body:
                if isinstance(entry, Mapping):
                    entries[str(entry.get("id"))] = entry
            if not headers.get("x-next-page"):
                break
        else:
            raise ConnectorError(f"it has more than {MAX_PAGES * PAGE_SIZE} items, so narrow it")
        if total is None or len(entries) != total - hidden:
            raise ConnectorError(f"GitLab said {total} items but returned {len(entries)} different ones")
        return list(entries.values())

    def _api(self, path: str, deadline: float) -> tuple[dict[str, str], object]:
        remaining = min(CALL_TIMEOUT_SECONDS, deadline - time.monotonic())
        host = ["--hostname", self.hostname] if self.hostname else []
        argv = [self.glab, "api", "--include", *host, path]
        output = run_text(argv, self.env_names, remaining, cwd=NEUTRAL_DIRECTORY)
        parts = _HEADER_END.split(output, maxsplit=1)
        head, body = (parts[0], parts[1]) if len(parts) == 2 else ("", output)
        headers = {}
        for line in head.splitlines():
            name, separator, value = line.partition(":")
            if separator:
                headers[name.strip().lower()] = value.strip()
        try:
            return headers, json.loads(body)
        except ValueError as error:
            raise ConnectorError(f"GitLab did not return JSON: {error}") from error


def endpoint(query: str) -> str:
    return urllib.parse.urlsplit(query).path.strip("/")


def _reference(target: object) -> str:
    references = target.get("references") if isinstance(target, Mapping) else None
    reference = references.get("full") if isinstance(references, Mapping) else None
    return reference if isinstance(reference, str) and _ID.fullmatch(reference) else ""


def _work_item(entry: Mapping[str, object]) -> SourceItem:
    reference, title = _reference(entry), entry.get("title")
    if not reference or not isinstance(title, str) or not title.strip():
        raise ConnectorError(f"GitLab returned an item without a usable reference or title (id {entry.get('id')})")
    project, kind = re.split(r"[#!]", reference)[0], "issue" if "#" in reference else "mr"
    author, labels = entry.get("author"), entry.get("labels")
    meta = {
        "repo": project,
        "kind": kind,
        "author": str(author.get("username", "")) if isinstance(author, Mapping) else "",
        "labels": ",".join(str(label) for label in labels) if isinstance(labels, list) else "",
    }
    return SourceItem(
        external_id=reference,
        title=f"{reference} {title.strip()}",
        url=str(entry.get("web_url", "")),
        due=str(entry.get("due_date") or ""),
        updated=str(entry.get("updated_at", "")),
        meta=meta,
    )


def _todo(entry: Mapping[str, object], tracked: set[str], failed: set[str]) -> SourceItem | None:
    identifier, action = entry.get("id"), str(entry.get("action_name") or "")
    if not isinstance(identifier, int) or not action:
        raise ConnectorError("GitLab returned a todo without an id or an action")
    target, target_type = entry.get("target"), str(entry.get("target_type") or "")
    covered = _COVERED.get((action, target_type))
    if covered is not None and (_reference(target) in tracked or covered in failed):
        return None
    project = entry.get("project")
    path = str(project.get("path_with_namespace", "")) if isinstance(project, Mapping) else ""
    reference = _reference(target)
    about = str(target.get("title", "")) if isinstance(target, Mapping) else ""
    title = f"{action}: {reference + ' ' if reference else ''}{about or entry.get('body') or target_type}"
    meta = {"repo": path, "kind": "todo", "action": action, "target": reference}
    return SourceItem(
        external_id=f"todo-{identifier}",
        title=" ".join(title.split())[:300],
        url=str(entry.get("target_url", "")),
        updated=str(entry.get("updated_at", "")),
        meta=meta,
    )


def build(spec: ConnectorSpec) -> GitlabConnector:
    where = f"[connectors.{spec.name}]"
    queries = spec.settings.get("queries", DEFAULT_QUERIES)
    if (
        not isinstance(queries, Mapping)
        or not queries
        or not all(
            isinstance(name, str) and isinstance(query, str) and query.strip() for name, query in queries.items()
        )
    ):
        raise ConnectorConfigError(
            f"{where} queries must map names to GitLab API paths, "
            'such as { mine = "issues?scope=assigned_to_me&state=opened" }'
        )
    for query in queries.values():
        fields = urllib.parse.parse_qs(urllib.parse.urlsplit(query).query)
        wanted = OPEN_STATES.get(endpoint(query))
        if wanted is None:
            raise ConnectorConfigError(f"{where} queries read issues, merge_requests or todos, got {query!r}")
        if fields.get("state") != [wanted]:
            raise ConnectorConfigError(
                f"{where} each query needs exactly state={wanted}, since the snapshot is what's open"
            )
        if "page" in fields or "per_page" in fields:
            raise ConnectorConfigError(f"{where} queries can't set page or per_page, paging is done for them")
    glab, hostname = spec.settings.get("glab", "glab"), spec.settings.get("hostname", "")
    if not isinstance(glab, str) or not glab or not isinstance(hostname, str):
        raise ConnectorConfigError(f"{where} glab must be a command name or path, and hostname a string")
    timeout = spec.settings.get("timeout", 120)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 300:
        raise ConnectorConfigError(f"{where} timeout must be between 0 and 300 seconds, for one whole poll")
    env = spec.settings.get("env", [])
    if not isinstance(env, list) or not all(isinstance(name, str) for name in env):
        raise ConnectorConfigError(f"{where} env must be a list of variable names")
    return GitlabConnector(dict(queries), glab, hostname, float(timeout), (*_DEFAULT_ENV, *env))

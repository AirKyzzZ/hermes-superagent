from __future__ import annotations

import json
import re
import time
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass

from ..ledger import SourceItem, Upstream
from . import ConnectorConfigError, ConnectorError, ConnectorSpec, Poll
from .process import run_text

KEYS = frozenset({"queries", "glab", "hostname", "timeout", "env"})
DEFAULT_QUERIES = {
    "assigned": "issues?scope=assigned_to_me&state=opened",
    "merge_requests": "merge_requests?scope=assigned_to_me&state=opened",
    "review": "merge_requests?scope=all&state=opened&reviewer_username={me}",
    "todos": "todos?state=pending",
}
OPEN_FILTERS = ("state=opened", "state=pending")
PAGE_SIZE = 100
MAX_PAGES = 50
CALL_TIMEOUT_SECONDS = 30.0
_ID = re.compile(r"(?P<project>[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+)(?P<kind>[#!])(?P<iid>\d+)")
_HEADER_END = re.compile(r"\r?\n\r?\n")
_DEFAULT_ENV = ("GITLAB_TOKEN", "GITLAB_HOST", "GLAB_CONFIG_DIR", "DBUS_SESSION_BUS_ADDRESS")
_CLOSED = frozenset({"closed", "merged"})


@dataclass(frozen=True)
class GitlabConnector:
    queries: Mapping[str, str]
    glab: str
    hostname: str
    timeout: float
    env_names: tuple[str, ...]

    def poll(self, cursor: str) -> Poll:
        deadline = time.monotonic() + self.timeout
        found: dict[str, SourceItem] = {}
        matched: dict[str, list[str]] = {}
        problems: list[str] = []
        me = ""
        for name, query in self.queries.items():
            try:
                if "{me}" in query and not me:
                    me = self._username(deadline)
                entries = self._list(query.replace("{me}", urllib.parse.quote(me)), deadline)
                parsed = [_item(entry) for entry in entries]
            except ConnectorError as error:
                problems.append(f"query {name}: {error}")
                continue
            for item in parsed:
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
        match = _ID.fullmatch(external_id)
        if match is None:
            return Upstream.UNKNOWN
        kind = "issues" if match["kind"] == "#" else "merge_requests"
        project = urllib.parse.quote(match["project"], safe="")
        entry = self._api(f"projects/{project}/{kind}/{match['iid']}", time.monotonic() + self.timeout)[1]
        state = entry.get("state") if isinstance(entry, Mapping) else None
        if not isinstance(state, str):
            raise ConnectorError(f"GitLab returned no state for {external_id}")
        return Upstream.CLOSED if state in _CLOSED else Upstream.OPEN

    def _username(self, deadline: float) -> str:
        user = self._api("user", deadline)[1]
        name = user.get("username") if isinstance(user, Mapping) else None
        if not isinstance(name, str) or not name:
            raise ConnectorError("GitLab returned no username for this login")
        return name

    def _list(self, query: str, deadline: float) -> list[Mapping[str, object]]:
        entries: dict[str, Mapping[str, object]] = {}
        total: str | None = None
        joiner = "&" if "?" in query else "?"
        for page in range(1, MAX_PAGES + 1):
            headers, body = self._api(f"{query}{joiner}per_page={PAGE_SIZE}&page={page}", deadline)
            if not isinstance(body, list):
                raise ConnectorError("GitLab returned an unexpected shape")
            count = headers.get("x-total")
            if count is None or not count.isdigit():
                raise ConnectorError("GitLab sent no total, so the list can't be checked for completeness")
            if total is not None and count != total:
                raise ConnectorError(f"the total moved from {total} to {count} while paging, retrying next poll")
            total = count
            for entry in body:
                if isinstance(entry, Mapping):
                    entries[str(entry.get("id"))] = entry
            if not headers.get("x-next-page"):
                break
        else:
            raise ConnectorError(f"it has more than {MAX_PAGES * PAGE_SIZE} items, so narrow it")
        if total is None or len(entries) != int(total):
            raise ConnectorError(f"GitLab said {total} items but returned {len(entries)} different ones")
        return list(entries.values())

    def _api(self, path: str, deadline: float) -> tuple[dict[str, str], object]:
        remaining = min(CALL_TIMEOUT_SECONDS, deadline - time.monotonic())
        host = ["--hostname", self.hostname] if self.hostname else []
        output = run_text([self.glab, "api", "--include", *host, path], self.env_names, remaining)
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


def _item(entry: Mapping[str, object]) -> SourceItem:
    target = entry.get("target") if "action_name" in entry else entry
    if isinstance(target, Mapping) and entry.get("target_type", "Issue") in ("Issue", "MergeRequest"):
        references = target.get("references")
        reference = references.get("full") if isinstance(references, Mapping) else None
        title = target.get("title")
        if isinstance(reference, str) and _ID.fullmatch(reference) and isinstance(title, str) and title.strip():
            return _work_item(target, reference, title.strip())
    return _todo(entry)


def _work_item(target: Mapping[str, object], reference: str, title: str) -> SourceItem:
    project, kind = re.split(r"[#!]", reference)[0], "issue" if "#" in reference else "mr"
    author = target.get("author")
    labels = target.get("labels")
    meta = {
        "repo": project,
        "kind": kind,
        "author": str(author.get("username", "")) if isinstance(author, Mapping) else "",
        "labels": ",".join(str(label) for label in labels) if isinstance(labels, list) else "",
    }
    return SourceItem(
        external_id=reference,
        title=f"{reference} {title}",
        url=str(target.get("web_url", "")),
        due=str(target.get("due_date") or ""),
        updated=str(target.get("updated_at", "")),
        meta=meta,
    )


def _todo(entry: Mapping[str, object]) -> SourceItem:
    identifier = entry.get("id")
    if not isinstance(identifier, int) or "action_name" not in entry:
        raise ConnectorError("GitLab returned an item without a reference or a todo id")
    project = entry.get("project")
    path = str(project.get("path_with_namespace", "")) if isinstance(project, Mapping) else ""
    target = entry.get("target")
    about = str(target.get("title", "")) if isinstance(target, Mapping) else ""
    title = f"{entry.get('action_name')}: {about or entry.get('body') or entry.get('target_type')}"
    return SourceItem(
        external_id=f"todo-{identifier}",
        title=" ".join(title.split())[:300],
        url=str(entry.get("target_url", "")),
        updated=str(entry.get("updated_at", "")),
        meta={"repo": path, "kind": "todo"},
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
        filters = urllib.parse.urlsplit(query).query.split("&")
        if not any(open_filter in filters for open_filter in OPEN_FILTERS):
            raise ConnectorConfigError(
                f"{where} every query needs state=opened or state=pending, since the snapshot is what's open"
            )
        if any(part.startswith(("page=", "per_page=")) for part in filters):
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

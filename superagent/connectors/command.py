from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from ..ledger import SourceItem, Upstream
from . import ConnectorConfigError, ConnectorError, ConnectorSpec, Poll, duration

KEYS = frozenset(
    {
        "argv",
        "confirm_argv",
        "items",
        "fields",
        "complete",
        "env",
        "timeout",
        "closed",
        "overlap",
        "total",
        "limit",
        "backfill",
    }
)
_BASE_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "USER", "XDG_CONFIG_HOME", "XDG_DATA_HOME")
_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")
_CURSOR_PLACEHOLDERS = ("{since}", "{since_epoch}", "{since_date}")
_ITEM_FIELDS = ("url", "due", "updated", "excerpt")
_DEFAULT_CLOSED = ("closed", "done", "completed", "true", "merged")
_MISSING = object()
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/#@+=-]*")


@dataclass(frozen=True)
class CommandConnector:
    argv: tuple[str, ...]
    confirm_argv: tuple[str, ...]
    items_path: str
    fields: Mapping[str, str]
    complete: bool
    env_names: tuple[str, ...]
    timeout: float
    closed: frozenset[str]
    overlap: float
    total_path: str
    limit: int
    backfill: float

    def poll(self, cursor: str) -> Poll:
        started = time.time()
        since = float(cursor) if cursor else started - self.backfill
        output = self._run([_with_cursor(part, since) for part in self.argv])
        entries = _lookup(output, self.items_path) if self.items_path else output
        if not isinstance(entries, list):
            raise ConnectorError(
                f"expected a list at {self.items_path or 'the top level'}, got {type(entries).__name__}"
            )
        if self.total_path:
            total = _lookup(output, self.total_path)
            if not isinstance(total, int) or isinstance(total, bool) or total != len(entries):
                raise ConnectorError(
                    f"got {len(entries)} items but {self.total_path} says otherwise, so the list is cut"
                )
        if self.limit and len(entries) >= self.limit:
            raise ConnectorError(f"got {len(entries)} items, the configured limit, so the list is probably cut")
        items: dict[str, SourceItem] = {}
        problems: list[str] = []
        seen: set[str] = set()
        for index, entry in enumerate(entries, 1):
            try:
                item = self._item(entry, seen)
            except ConnectorError as error:
                problems.append(f"item {index}: {error}")
                continue
            if item.external_id in items and items[item.external_id] != item:
                problems.append(f"item {index}: its id appears twice with different content")
            items[item.external_id] = item
        if entries:
            absent = sorted(name for name, path in self.fields.items() if name not in seen and "{" not in path)
            problems += [
                f"no item has {self.fields[name]} (the {name} field), did the output change?" for name in absent
            ]
        next_cursor = "" if self.complete else repr(started - self.overlap)
        return Poll(tuple(items.values()), self.complete, next_cursor, tuple(problems))

    def confirm(self, external_id: str) -> Upstream:
        if not self.confirm_argv:
            return Upstream.UNKNOWN
        if not _SAFE_ID.fullmatch(external_id):
            raise ConnectorError(f"refusing to pass the id {external_id[:80]!r} to a command")
        output = self._run([part.replace("{id}", external_id) for part in self.confirm_argv])
        status = _render(output, self.fields["status"], seen=set(), name="status")
        if status is None:
            return Upstream.UNKNOWN
        return Upstream.CLOSED if status.lower() in self.closed else Upstream.OPEN

    def _item(self, entry: object, seen: set[str]) -> SourceItem:
        values = {name: _render(entry, path, seen=seen, name=name) for name, path in self.fields.items()}
        external_id = values.pop("id") or ""
        title = values.pop("title") or ""
        if not external_id.strip():
            raise ConnectorError("it has no id")
        if not title.strip():
            raise ConnectorError("it has no title")
        status = values.pop("status", None)
        upstream = Upstream.CLOSED if status is not None and status.lower() in self.closed else Upstream.OPEN
        known = {name: values.pop(name, None) or "" for name in _ITEM_FIELDS}
        meta = {name: value for name, value in values.items() if value is not None}
        return SourceItem(external_id, title, upstream=upstream, meta=meta, **known)

    def _run(self, argv: Sequence[str]) -> object:
        env = {name: os.environ[name] for name in (*_BASE_ENV, *self.env_names) if name in os.environ}
        try:
            process = subprocess.Popen(
                list(argv),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                start_new_session=True,
            )
        except OSError as error:
            raise ConnectorError(f"{argv[0]} could not run: {error}") from error
        try:
            stdout, stderr = process.communicate(timeout=self.timeout)
        except subprocess.TimeoutExpired as error:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
            process.communicate()
            raise ConnectorError(f"{argv[0]} took longer than {self.timeout:g}s") from error
        if process.returncode != 0:
            raise ConnectorError(f"{argv[0]} exited {process.returncode}: {stderr.strip()[-200:]}")
        try:
            return json.loads(stdout)
        except ValueError as error:
            raise ConnectorError(f"{argv[0]} did not print JSON: {error}") from error


def build(spec: ConnectorSpec) -> CommandConnector:
    where = f"[connectors.{spec.name}]"
    settings = spec.settings
    fields = settings.get("fields")
    if not isinstance(fields, Mapping) or not all(isinstance(value, str) for value in fields.values()):
        raise ConnectorConfigError(f'{where} fields must map names to paths, such as {{ id = "id" }}')
    if "id" not in fields or "title" not in fields:
        raise ConnectorConfigError(f"{where} fields needs at least id and title")
    complete = settings.get("complete", True)
    if not isinstance(complete, bool):
        raise ConnectorConfigError(f"{where} complete must be true or false")
    timeout = settings.get("timeout", 60)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 600:
        raise ConnectorConfigError(f"{where} timeout must be between 0 and 600 seconds")
    limit = settings.get("limit", 0)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ConnectorConfigError(f"{where} limit must be a whole number")
    paths = {name: settings.get(name, "") for name in ("items", "total")}
    if not all(isinstance(path, str) for path in paths.values()):
        raise ConnectorConfigError(f"{where} items and total must be dotted paths")
    argv = _strings(settings.get("argv"), f"{where} argv", required=True)
    confirm_argv = _strings(settings.get("confirm_argv", []), f"{where} confirm_argv", required=False)
    if confirm_argv and ("{id}" not in " ".join(confirm_argv) or "status" not in fields):
        raise ConnectorConfigError(f"{where} confirm_argv needs an {{id}} placeholder and a status field to read")
    uses_cursor = any(placeholder in part for part in argv for placeholder in _CURSOR_PLACEHOLDERS)
    if complete and uses_cursor:
        raise ConnectorConfigError(f"{where} a complete source returns everything, so it can't use {{since}}")
    if not complete and spec.quiet_after_seconds <= 0:
        raise ConnectorConfigError(f"{where} a delta source needs quiet_after, so a query that stops matching shows")
    overlap = duration(settings.get("overlap", 600), f"{where} overlap")
    if not complete and overlap <= 0:
        raise ConnectorConfigError(f"{where} overlap must be more than 0 for a delta source")
    return CommandConnector(
        argv=argv,
        confirm_argv=confirm_argv,
        items_path=str(paths["items"]),
        fields=dict(fields),
        complete=complete,
        env_names=_strings(settings.get("env", []), f"{where} env", required=False),
        timeout=float(timeout),
        closed=frozenset(
            value.lower() for value in _strings(settings.get("closed", list(_DEFAULT_CLOSED)), where, required=False)
        ),
        overlap=overlap,
        total_path=str(paths["total"]),
        limit=limit,
        backfill=duration(settings.get("backfill", "14d"), f"{where} backfill"),
    )


def _with_cursor(part: str, since: float) -> str:
    moment = datetime.fromtimestamp(since, UTC)
    return (
        part.replace("{since}", moment.strftime("%Y-%m-%dT%H:%M:%SZ"))
        .replace("{since_epoch}", str(int(since)))
        .replace("{since_date}", moment.strftime("%Y/%m/%d"))
    )


def _strings(value: object, where: str, *, required: bool) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(part, str) for part in value) or (required and not value):
        raise ConnectorConfigError(f"{where} must be a {'non-empty ' if required else ''}list of strings")
    return tuple(value)


def _lookup(data: object, path: str) -> object:
    current = data
    for part in path.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return _MISSING
    return current


def _text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return str(value).lower()
    return json.dumps(value) if isinstance(value, (dict, list)) else str(value)


def _render(entry: object, spec: str, *, seen: set[str], name: str) -> str | None:
    if "{" not in spec:
        value = _lookup(entry, spec)
        if value is _MISSING:
            if name in ("id", "title"):
                raise ConnectorError(f"it has no {spec}")
            return None
        seen.add(name)
        return None if value is None else _text(value)

    def substitute(match: re.Match[str]) -> str:
        value = _lookup(entry, match.group(1))
        if value is _MISSING or value is None:
            raise ConnectorError(f"it has no {match.group(1)}")
        return _text(value)

    seen.add(name)
    return _PLACEHOLDER.sub(substitute, spec)

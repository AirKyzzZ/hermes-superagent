from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ..ledger import SourceItem, Upstream
from . import ConnectorConfigError, ConnectorError, ConnectorSpec, Poll, duration

KEYS = frozenset({"argv", "confirm_argv", "items", "fields", "complete", "env", "timeout", "closed", "overlap"})
_BASE_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "USER", "XDG_CONFIG_HOME", "XDG_DATA_HOME")
_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")
_ITEM_FIELDS = ("url", "due", "updated", "excerpt")
_DEFAULT_CLOSED = ("closed", "done", "completed", "true", "merged")
_MISSING = object()


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

    def poll(self, cursor: str) -> Poll:
        started = datetime.now(UTC)
        output = self._run([part.replace("{since}", cursor) for part in self.argv])
        entries = _lookup(output, self.items_path) if self.items_path else output
        if not isinstance(entries, list):
            raise ConnectorError(
                f"expected a list at {self.items_path or 'the top level'}, got {type(entries).__name__}"
            )
        items = {item.external_id: item for item in (self._item(entry) for entry in entries)}
        next_cursor = "" if self.complete else (started - timedelta(seconds=self.overlap)).isoformat()
        return Poll(tuple(items.values()), self.complete, next_cursor)

    def confirm(self, external_id: str) -> Upstream:
        if not self.confirm_argv or "status" not in self.fields:
            return Upstream.UNKNOWN
        output = self._run([part.replace("{id}", external_id) for part in self.confirm_argv])
        status = _render(output, self.fields["status"], required=False)
        if status is None:
            return Upstream.UNKNOWN
        return Upstream.CLOSED if status.lower() in self.closed else Upstream.OPEN

    def _item(self, entry: object) -> SourceItem:
        values = {name: _render(entry, path, required=name in ("id", "title")) for name, path in self.fields.items()}
        external_id = values.pop("id") or ""
        title = values.pop("title") or ""
        if not external_id.strip() or not title.strip():
            raise ConnectorError(f"an item has an empty id or title: {str(entry)[:200]}")
        status = values.pop("status", None)
        upstream = Upstream.CLOSED if status is not None and status.lower() in self.closed else Upstream.OPEN
        known = {name: values.pop(name, None) or "" for name in _ITEM_FIELDS}
        meta = {name: value for name, value in values.items() if value is not None}
        return SourceItem(external_id, title, upstream=upstream, meta=meta, **known)

    def _run(self, argv: Sequence[str]) -> object:
        env = {name: os.environ[name] for name in (*_BASE_ENV, *self.env_names) if name in os.environ}
        try:
            done = subprocess.run(
                list(argv), capture_output=True, text=True, timeout=self.timeout, env=env, check=False
            )
        except subprocess.TimeoutExpired as error:
            raise ConnectorError(f"{argv[0]} took longer than {self.timeout:g}s") from error
        except OSError as error:
            raise ConnectorError(f"{argv[0]} could not run: {error}") from error
        if done.returncode != 0:
            raise ConnectorError(f"{argv[0]} exited {done.returncode}: {done.stderr.strip()[-300:]}")
        try:
            return json.loads(done.stdout)
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
    items_path = settings.get("items", "")
    if not isinstance(items_path, str):
        raise ConnectorConfigError(f"{where} items must be a dotted path")
    return CommandConnector(
        argv=_strings(settings.get("argv"), f"{where} argv", required=True),
        confirm_argv=_strings(settings.get("confirm_argv", []), f"{where} confirm_argv", required=False),
        items_path=items_path,
        fields=dict(fields),
        complete=complete,
        env_names=_strings(settings.get("env", []), f"{where} env", required=False),
        timeout=float(timeout),
        closed=frozenset(
            value.lower() for value in _strings(settings.get("closed", list(_DEFAULT_CLOSED)), where, required=False)
        ),
        overlap=duration(settings.get("overlap", 600), f"{where} overlap"),
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


def _render(entry: object, spec: str, *, required: bool) -> str | None:
    if "{" not in spec:
        value = _lookup(entry, spec)
        if value is _MISSING or value is None:
            if required:
                raise ConnectorError(f"an item has no {spec}: {str(entry)[:200]}")
            return None
        return _text(value)

    def substitute(match: re.Match[str]) -> str:
        value = _lookup(entry, match.group(1))
        if value is _MISSING or value is None:
            raise ConnectorError(f"an item has no {match.group(1)}: {str(entry)[:200]}")
        return _text(value)

    return _PLACEHOLDER.sub(substitute, spec)

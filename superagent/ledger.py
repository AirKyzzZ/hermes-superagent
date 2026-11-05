from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

BUSY_TIMEOUT_SECONDS = 5
HANDLE_PREFIX = "it-"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY, connector TEXT NOT NULL, external_id TEXT NOT NULL, handle TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL, url TEXT NOT NULL, due TEXT NOT NULL, upstream TEXT NOT NULL, updated TEXT NOT NULL,
    excerpt TEXT NOT NULL, meta TEXT NOT NULL, state TEXT NOT NULL, reason TEXT NOT NULL, area TEXT NOT NULL,
    first_seen REAL NOT NULL, last_seen REAL NOT NULL, last_changed REAL NOT NULL,
    missing_since REAL, snoozed_until REAL, ticket_id TEXT
);
CREATE INDEX IF NOT EXISTS items_by_connector ON items (connector, state);
CREATE TABLE IF NOT EXISTS item_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT NOT NULL, at REAL NOT NULL, actor TEXT NOT NULL,
    from_state TEXT, to_state TEXT NOT NULL, area TEXT NOT NULL, note TEXT NOT NULL, content TEXT
);
CREATE INDEX IF NOT EXISTS item_events_by_item ON item_events (item_id, seq);
CREATE TABLE IF NOT EXISTS ledger_schema (version INTEGER NOT NULL);
"""
_MIGRATIONS: tuple[str, ...] = ()


class ItemState(StrEnum):
    NEW = "new"
    TRIAGED = "triaged"
    ASSIGNED = "assigned"
    WAITING = "waiting"
    DONE = "done"
    IGNORED = "ignored"


class Upstream(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    UNKNOWN = "unknown"


CLOSED_STATES = frozenset({ItemState.DONE, ItemState.IGNORED})
_ANY = frozenset(ItemState)
_TRANSITIONS: dict[str, dict[ItemState, frozenset[ItemState]]] = {
    "owner": {
        ItemState.TRIAGED: _ANY,
        ItemState.ASSIGNED: _ANY,
        ItemState.WAITING: _ANY,
        ItemState.DONE: _ANY,
        ItemState.IGNORED: _ANY,
    },
    "rule": {
        ItemState.TRIAGED: frozenset({ItemState.NEW}),
        ItemState.ASSIGNED: frozenset({ItemState.NEW}),
        ItemState.IGNORED: frozenset({ItemState.NEW}),
    },
    "model": {ItemState.IGNORED: frozenset({ItemState.NEW})},
    "worker": {ItemState.WAITING: frozenset({ItemState.ASSIGNED})},
}
_CONTENT_FIELDS = ("title", "url", "due", "upstream", "updated", "excerpt", "meta")


class LedgerError(ValueError):
    pass


@dataclass(frozen=True)
class SourceItem:
    external_id: str
    title: str
    url: str = ""
    due: str = ""
    upstream: Upstream = Upstream.OPEN
    updated: str = ""
    excerpt: str = ""
    meta: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Item:
    id: str
    connector: str
    external_id: str
    handle: str
    title: str
    url: str
    due: str
    upstream: Upstream
    updated: str
    excerpt: str
    meta: Mapping[str, str]
    state: ItemState
    reason: str
    area: str
    first_seen: float
    last_seen: float
    last_changed: float
    missing_since: float | None
    snoozed_until: float | None
    ticket_id: str | None

    @property
    def is_open(self) -> bool:
        return self.state not in CLOSED_STATES


@dataclass(frozen=True)
class ItemEvent:
    at: float
    actor: str
    from_state: ItemState | None
    to_state: ItemState
    area: str
    note: str
    content: Mapping[str, object] | None


def item_id(connector: str, external_id: str) -> str:
    return f"{connector}:{external_id}"


def _handle_for(identifier: str, length: int) -> str:
    digest = base64.b32encode(hashlib.sha256(identifier.encode()).digest()).decode().lower()
    return HANDLE_PREFIX + digest[:length]


class Ledger:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=BUSY_TIMEOUT_SECONDS, isolation_level=None)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(_SCHEMA)
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT version FROM ledger_schema").fetchone()
            for statement in _MIGRATIONS[row[0] if row else 0 :]:
                connection.execute(statement)
            connection.execute("DELETE FROM ledger_schema")
            connection.execute("INSERT INTO ledger_schema VALUES (?)", (len(_MIGRATIONS),))
            connection.execute("COMMIT")
        finally:
            connection.close()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_SECONDS, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def record(self, connector: str, source: SourceItem) -> Item:
        _check_connector(connector)
        identifier = item_id(connector, source.external_id)
        content = _content(source)
        actor = f"connector:{connector}"
        with self._connect() as connection:
            now = time.time()
            row = connection.execute("SELECT * FROM items WHERE id = ?", (identifier,)).fetchone()
            if row is None:
                state = ItemState.DONE if source.upstream is Upstream.CLOSED else ItemState.NEW
                connection.execute(
                    "INSERT INTO items VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,NULL)",
                    (
                        identifier,
                        connector,
                        source.external_id,
                        _free_handle(connection, identifier),
                        *(_stored(content[name]) for name in _CONTENT_FIELDS),
                        state.value,
                        "closed upstream" if state is ItemState.DONE else "",
                        "",
                        now,
                        now,
                        now,
                    ),
                )
                _log(connection, identifier, now, actor, None, state, "", "first seen", content)
                return _reload(connection, identifier)
            previous = ItemState(row["state"])
            changed = any(_stored(content[name]) != row[name] for name in _CONTENT_FIELDS)
            returned = row["missing_since"] is not None
            connection.execute(
                "UPDATE items SET title=?, url=?, due=?, upstream=?, updated=?, excerpt=?, meta=?, last_seen=?, "
                "missing_since=NULL WHERE id=?",
                (*(_stored(content[name]) for name in _CONTENT_FIELDS), now, identifier),
            )
            target, note = previous, "changed at the source"
            if source.upstream is Upstream.CLOSED and previous not in CLOSED_STATES:
                target, note = ItemState.DONE, "closed upstream"
            elif (
                source.upstream is Upstream.OPEN
                and previous in CLOSED_STATES
                and (row["upstream"] == Upstream.CLOSED.value or changed or returned)
            ):
                target, note = ItemState.NEW, "reopened at the source"
            if target is not previous:
                _move(connection, row, now, actor, target, note, content)
            elif changed:
                connection.execute(
                    "UPDATE items SET last_changed = ?, snoozed_until = NULL WHERE id = ?", (now, identifier)
                )
                _log(connection, identifier, now, actor, previous, previous, row["area"], note, content)
            elif returned:
                _log(connection, identifier, now, actor, previous, previous, row["area"], "back in the source", None)
            return _reload(connection, identifier)

    def mark_missing(self, connector: str, present: Sequence[str]) -> list[Item]:
        _check_connector(connector)
        keep = {item_id(connector, external_id) for external_id in present}
        actor = f"connector:{connector}"
        with self._connect() as connection:
            now = time.time()
            rows = connection.execute(
                "SELECT * FROM items WHERE connector = ? AND missing_since IS NULL", (connector,)
            ).fetchall()
            missing = [row for row in rows if row["id"] not in keep]
            for row in missing:
                connection.execute(
                    "UPDATE items SET missing_since = ?, snoozed_until = NULL WHERE id = ?", (now, row["id"])
                )
                state = ItemState(row["state"])
                _log(connection, row["id"], now, actor, state, state, row["area"], "gone from the source", None)
            return [_reload(connection, row["id"]) for row in missing if ItemState(row["state"]) not in CLOSED_STATES]

    def confirm(self, reference: str, upstream: Upstream, actor: str) -> Item:
        with self._connect() as connection:
            row = _find(connection, reference)
            if actor != f"connector:{row['connector']}":
                raise LedgerError(f"{actor} can't confirm an item from {row['connector']}")
            if upstream is not Upstream.CLOSED:
                return _item(row)
            now = time.time()
            connection.execute("UPDATE items SET upstream = ? WHERE id = ?", (Upstream.CLOSED.value, row["id"]))
            content = {name: _loaded(name, row[name]) for name in _CONTENT_FIELDS} | {"upstream": "closed"}
            state = ItemState(row["state"])
            note = "closed upstream, confirmed"
            if state in CLOSED_STATES:
                _log(connection, row["id"], now, actor, state, state, row["area"], note, content)
            else:
                _move(connection, row, now, actor, ItemState.DONE, note, content)
            return _reload(connection, row["id"])

    def move(
        self,
        reference: str,
        target: ItemState,
        actor: str,
        reason: str = "",
        *,
        expected: ItemState | None = None,
        area: str | None = None,
        ticket_id: str | None = None,
    ) -> Item:
        if target in CLOSED_STATES and not reason.strip():
            raise LedgerError(f"setting an item to {target.value} needs a reason")
        with self._connect() as connection:
            row = _find(connection, reference)
            previous = ItemState(row["state"])
            if expected is not None and previous is not expected:
                raise LedgerError(f"{row['handle']} is {previous.value}, not {expected.value}")
            kind, _, who = actor.partition(":")
            if previous not in _TRANSITIONS.get(kind, {}).get(target, frozenset()):
                raise LedgerError(f"{actor} can't move {row['handle']} from {previous.value} to {target.value}")
            if kind == "worker" and (not who or row["ticket_id"] != who):
                raise LedgerError(f"{actor} doesn't hold {row['handle']}")
            if target is ItemState.ASSIGNED and not (ticket_id or row["ticket_id"]):
                raise LedgerError("assigning an item needs a ticket")
            now = time.time()
            if area is not None:
                connection.execute("UPDATE items SET area = ? WHERE id = ?", (area, row["id"]))
            if ticket_id is not None:
                connection.execute("UPDATE items SET ticket_id = ? WHERE id = ?", (ticket_id, row["id"]))
            current = _reload_row(connection, row["id"])
            if previous is target:
                _log(connection, row["id"], now, actor, previous, target, current["area"], reason, None)
            else:
                _move(connection, current, now, actor, target, reason, None)
            return _reload(connection, row["id"])

    def snooze(self, reference: str, until: float, actor: str) -> Item:
        if actor != "owner":
            raise LedgerError("only the owner can snooze an item")
        with self._connect() as connection:
            now = time.time()
            if until <= now:
                raise LedgerError("snooze to a time in the future")
            row = _find(connection, reference)
            state = ItemState(row["state"])
            connection.execute("UPDATE items SET snoozed_until = ? WHERE id = ?", (until, row["id"]))
            _log(connection, row["id"], now, actor, state, state, row["area"], f"snoozed until {until:.0f}", None)
            return _reload(connection, row["id"])

    def get(self, reference: str) -> Item:
        with self._connect() as connection:
            return _item(_find(connection, reference))

    def items(self, *, open_only: bool = False, connector: str = "", missing: bool = False) -> list[Item]:
        clauses: list[str] = []
        params: list[str] = []
        if open_only:
            clauses.append("state NOT IN (?, ?)")
            params += [ItemState.DONE.value, ItemState.IGNORED.value]
        if connector:
            clauses.append("connector = ?")
            params.append(connector)
        if missing:
            clauses.append("missing_since IS NOT NULL")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(f"SELECT * FROM items{where} ORDER BY first_seen", params).fetchall()
        return [_item(row) for row in rows]

    def history(self, reference: str) -> list[ItemEvent]:
        with self._connect() as connection:
            row = _find(connection, reference)
            events = connection.execute(
                "SELECT * FROM item_events WHERE item_id = ? ORDER BY seq", (row["id"],)
            ).fetchall()
        return [
            ItemEvent(
                event["at"],
                event["actor"],
                ItemState(event["from_state"]) if event["from_state"] else None,
                ItemState(event["to_state"]),
                event["area"],
                event["note"],
                json.loads(event["content"]) if event["content"] else None,
            )
            for event in events
        ]


def _check_connector(connector: str) -> None:
    if not connector or ":" in connector:
        raise LedgerError(f"connector names can't be empty or contain ':', got {connector!r}")


def _content(source: SourceItem) -> dict[str, object]:
    return {
        "title": source.title,
        "url": source.url,
        "due": source.due,
        "upstream": source.upstream.value,
        "updated": source.updated,
        "excerpt": source.excerpt,
        "meta": dict(sorted(source.meta.items())),
    }


def _stored(value: object) -> str:
    return value if isinstance(value, str) else json.dumps(value, sort_keys=True)


def _loaded(name: str, value: str) -> object:
    return json.loads(value) if name == "meta" else value


def _free_handle(connection: sqlite3.Connection, identifier: str) -> str:
    for length in range(4, 53):
        handle = _handle_for(identifier, length)
        if connection.execute("SELECT 1 FROM items WHERE handle = ?", (handle,)).fetchone() is None:
            return handle
    raise LedgerError(f"no free handle for {identifier}")


def _find(connection: sqlite3.Connection, reference: str) -> sqlite3.Row:
    handle = reference.lower() if reference.lower().startswith(HANDLE_PREFIX) else reference
    row: sqlite3.Row | None = connection.execute(
        "SELECT * FROM items WHERE id = ? OR handle = ?", (reference, handle)
    ).fetchone()
    if row is None:
        raise LedgerError(f"no item {reference}")
    return row


def _reload_row(connection: sqlite3.Connection, identifier: str) -> sqlite3.Row:
    row: sqlite3.Row = connection.execute("SELECT * FROM items WHERE id = ?", (identifier,)).fetchone()
    return row


def _reload(connection: sqlite3.Connection, identifier: str) -> Item:
    return _item(_reload_row(connection, identifier))


def _move(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    now: float,
    actor: str,
    target: ItemState,
    note: str,
    content: Mapping[str, object] | None,
) -> None:
    connection.execute(
        "UPDATE items SET state = ?, reason = ?, last_changed = ?, snoozed_until = NULL WHERE id = ?",
        (target.value, note, now, row["id"]),
    )
    _log(connection, row["id"], now, actor, ItemState(row["state"]), target, row["area"], note, content)


def _log(
    connection: sqlite3.Connection,
    identifier: str,
    now: float,
    actor: str,
    previous: ItemState | None,
    target: ItemState,
    area: str,
    note: str,
    content: Mapping[str, object] | None,
) -> None:
    connection.execute(
        "INSERT INTO item_events (item_id, at, actor, from_state, to_state, area, note, content) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            identifier,
            now,
            actor,
            previous.value if previous else None,
            target.value,
            area,
            note,
            json.dumps(content, sort_keys=True) if content is not None else None,
        ),
    )


def _item(row: sqlite3.Row) -> Item:
    return Item(
        id=row["id"],
        connector=row["connector"],
        external_id=row["external_id"],
        handle=row["handle"],
        title=row["title"],
        url=row["url"],
        due=row["due"],
        upstream=Upstream(row["upstream"]),
        updated=row["updated"],
        excerpt=row["excerpt"],
        meta=json.loads(row["meta"]),
        state=ItemState(row["state"]),
        reason=row["reason"],
        area=row["area"],
        first_seen=row["first_seen"],
        last_seen=row["last_seen"],
        last_changed=row["last_changed"],
        missing_since=row["missing_since"],
        snoozed_until=row["snoozed_until"],
        ticket_id=row["ticket_id"],
    )

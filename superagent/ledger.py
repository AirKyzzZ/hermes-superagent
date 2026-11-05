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
    from_state TEXT, to_state TEXT NOT NULL, note TEXT NOT NULL, content TEXT
);
CREATE INDEX IF NOT EXISTS item_events_by_item ON item_events (item_id, seq);
"""


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
_CONTENT_FIELDS = ("title", "url", "due", "upstream", "updated", "excerpt", "meta")
_ALLOWED: dict[ItemState, frozenset[str]] = {
    ItemState.NEW: frozenset({"connector"}),
    ItemState.TRIAGED: frozenset({"rule", "owner"}),
    ItemState.ASSIGNED: frozenset({"rule", "owner"}),
    ItemState.WAITING: frozenset({"worker", "owner"}),
    ItemState.DONE: frozenset({"connector", "owner"}),
    ItemState.IGNORED: frozenset({"rule", "owner", "model"}),
}


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
    note: str
    content: Mapping[str, object] | None


def item_id(connector: str, external_id: str) -> str:
    return f"{connector}:{external_id}"


def _handle_for(identifier: str, length: int) -> str:
    digest = base64.b32encode(hashlib.sha256(identifier.encode()).digest()).decode().lower()
    return HANDLE_PREFIX + digest[:length]


def _actor_kind(actor: str) -> str:
    return actor.split(":", 1)[0]


class Ledger:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=BUSY_TIMEOUT_SECONDS)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(_SCHEMA)
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
        identifier = item_id(connector, source.external_id)
        now = time.time()
        content = _content(source)
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM items WHERE id = ?", (identifier,)).fetchone()
            if row is None:
                handle = _free_handle(connection, identifier)
                state = ItemState.DONE if source.upstream is Upstream.CLOSED else ItemState.NEW
                connection.execute(
                    "INSERT INTO items VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,NULL)",
                    (
                        identifier,
                        connector,
                        source.external_id,
                        handle,
                        *(_stored(content[name]) for name in _CONTENT_FIELDS),
                        state.value,
                        "closed upstream" if state is ItemState.DONE else "",
                        "",
                        now,
                        now,
                        now,
                    ),
                )
                _log(connection, identifier, now, f"connector:{connector}", None, state, "first seen", content)
                return _item(connection.execute("SELECT * FROM items WHERE id = ?", (identifier,)).fetchone())
            previous = ItemState(row["state"])
            changed = any(_stored(content[name]) != row[name] for name in _CONTENT_FIELDS)
            connection.execute(
                "UPDATE items SET title=?, url=?, due=?, upstream=?, updated=?, excerpt=?, meta=?, last_seen=?, "
                "missing_since=NULL WHERE id=?",
                (*(_stored(content[name]) for name in _CONTENT_FIELDS), now, identifier),
            )
            actor = f"connector:{connector}"
            if row["missing_since"] is not None:
                _log(connection, identifier, now, actor, previous, previous, "back in the source", None)
            target = previous
            note = "changed at the source"
            if source.upstream is Upstream.CLOSED and previous not in CLOSED_STATES:
                target, note = ItemState.DONE, "closed upstream"
            elif (
                source.upstream is Upstream.OPEN
                and previous in CLOSED_STATES
                and (row["upstream"] == Upstream.CLOSED.value or changed)
            ):
                target, note = ItemState.NEW, "reopened at the source"
            if target is not previous:
                _move(connection, identifier, now, actor, previous, target, note, content)
            elif changed:
                connection.execute(
                    "UPDATE items SET last_changed = ?, snoozed_until = NULL WHERE id = ?", (now, identifier)
                )
                _log(connection, identifier, now, actor, previous, previous, note, content)
            return _item(connection.execute("SELECT * FROM items WHERE id = ?", (identifier,)).fetchone())

    def mark_missing(self, connector: str, present: Sequence[str]) -> list[Item]:
        now = time.time()
        keep = {item_id(connector, external_id) for external_id in present}
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM items WHERE connector = ? AND missing_since IS NULL AND state NOT IN (?, ?)",
                (connector, ItemState.DONE.value, ItemState.IGNORED.value),
            ).fetchall()
            missing = [row for row in rows if row["id"] not in keep]
            for row in missing:
                connection.execute("UPDATE items SET missing_since = ? WHERE id = ?", (now, row["id"]))
                state = ItemState(row["state"])
                _log(connection, row["id"], now, f"connector:{connector}", state, state, "gone from the source", None)
            return [
                _item(connection.execute("SELECT * FROM items WHERE id = ?", (row["id"],)).fetchone())
                for row in missing
            ]

    def confirm(self, identifier: str, upstream: Upstream, actor: str) -> Item:
        if upstream is not Upstream.CLOSED:
            return self.get(identifier)
        return self.move(identifier, ItemState.DONE, actor, "closed upstream, confirmed")

    def move(self, reference: str, target: ItemState, actor: str, reason: str = "") -> Item:
        if _actor_kind(actor) not in _ALLOWED[target]:
            raise LedgerError(f"{actor} can't set an item to {target.value}")
        if target is ItemState.IGNORED and not reason.strip():
            raise LedgerError("ignoring an item needs a reason")
        now = time.time()
        with self._connect() as connection:
            row = _find(connection, reference)
            previous = ItemState(row["state"])
            if previous is target:
                return _item(row)
            _move(connection, row["id"], now, actor, previous, target, reason, None)
            return _item(connection.execute("SELECT * FROM items WHERE id = ?", (row["id"],)).fetchone())

    def snooze(self, reference: str, until: float, actor: str) -> Item:
        now = time.time()
        if until <= now:
            raise LedgerError("snooze to a time in the future")
        with self._connect() as connection:
            row = _find(connection, reference)
            state = ItemState(row["state"])
            connection.execute("UPDATE items SET snoozed_until = ? WHERE id = ?", (until, row["id"]))
            _log(connection, row["id"], now, actor, state, state, f"snoozed until {until:.0f}", None)
            return _item(connection.execute("SELECT * FROM items WHERE id = ?", (row["id"],)).fetchone())

    def get(self, reference: str) -> Item:
        with self._connect() as connection:
            return _item(_find(connection, reference))

    def items(self, *, open_only: bool = False, connector: str = "") -> list[Item]:
        query = "SELECT * FROM items"
        clauses: list[str] = []
        params: list[str] = []
        if open_only:
            clauses.append("state NOT IN (?, ?)")
            params += [ItemState.DONE.value, ItemState.IGNORED.value]
        if connector:
            clauses.append("connector = ?")
            params.append(connector)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        with self._connect() as connection:
            rows = connection.execute(query + " ORDER BY first_seen", params).fetchall()
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
                event["note"],
                json.loads(event["content"]) if event["content"] else None,
            )
            for event in events
        ]


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


def _free_handle(connection: sqlite3.Connection, identifier: str) -> str:
    for length in range(4, 53):
        handle = _handle_for(identifier, length)
        if connection.execute("SELECT 1 FROM items WHERE handle = ?", (handle,)).fetchone() is None:
            return handle
    raise LedgerError(f"no free handle for {identifier}")


def _find(connection: sqlite3.Connection, reference: str) -> sqlite3.Row:
    row: sqlite3.Row | None = connection.execute(
        "SELECT * FROM items WHERE id = ? OR handle = ?", (reference, reference)
    ).fetchone()
    if row is None:
        raise LedgerError(f"no item {reference}")
    return row


def _move(
    connection: sqlite3.Connection,
    identifier: str,
    now: float,
    actor: str,
    previous: ItemState,
    target: ItemState,
    note: str,
    content: Mapping[str, object] | None,
) -> None:
    connection.execute(
        "UPDATE items SET state = ?, reason = ?, last_changed = ? WHERE id = ?", (target.value, note, now, identifier)
    )
    _log(connection, identifier, now, actor, previous, target, note, content)


def _log(
    connection: sqlite3.Connection,
    identifier: str,
    now: float,
    actor: str,
    previous: ItemState | None,
    target: ItemState,
    note: str,
    content: Mapping[str, object] | None,
) -> None:
    connection.execute(
        "INSERT INTO item_events (item_id, at, actor, from_state, to_state, note, content) VALUES (?,?,?,?,?,?,?)",
        (
            identifier,
            now,
            actor,
            previous.value if previous else None,
            target.value,
            note,
            json.dumps(content, sort_keys=True) if content is not None else None,
        ),
    )


def _item(row: sqlite3.Row) -> Item:
    return Item(
        id=row["id"],
        connector=row["connector"],
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

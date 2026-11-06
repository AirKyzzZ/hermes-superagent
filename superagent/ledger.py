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
CREATE TABLE IF NOT EXISTS briefs (
    day TEXT PRIMARY KEY, built_at REAL NOT NULL, first_seq INTEGER NOT NULL, last_seq INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS worker_briefs (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT NOT NULL, digest TEXT NOT NULL, at REAL NOT NULL,
    text TEXT NOT NULL, failures TEXT NOT NULL, needs_approval INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS worker_briefs_by_item ON worker_briefs (item_id, digest);
CREATE TABLE IF NOT EXISTS announcements (
    item_id TEXT NOT NULL, key TEXT NOT NULL, at REAL NOT NULL, PRIMARY KEY (item_id, key)
);
CREATE TABLE IF NOT EXISTS sources (
    connector TEXT PRIMARY KEY, cursor TEXT NOT NULL, first_attempt REAL NOT NULL, last_attempt REAL NOT NULL,
    last_ok REAL, failures INTEGER NOT NULL, last_error TEXT NOT NULL, applied_count INTEGER, held_count INTEGER,
    accept_next INTEGER NOT NULL, last_new REAL, confirm_error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS source_runs (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, connector TEXT NOT NULL, at REAL NOT NULL, ok INTEGER NOT NULL,
    count INTEGER NOT NULL, new_items INTEGER NOT NULL, held INTEGER NOT NULL, error TEXT NOT NULL
);
"""
_MIGRATIONS: tuple[str, ...] = (
    "ALTER TABLE items ADD COLUMN confirm_attempt REAL",
    "ALTER TABLE items ADD COLUMN last_briefed REAL",
)


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
    last_briefed: float | None = None

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


@dataclass(frozen=True)
class SourceState:
    connector: str
    cursor: str = ""
    first_attempt: float | None = None
    last_attempt: float | None = None
    last_ok: float | None = None
    failures: int = 0
    last_error: str = ""
    applied_count: int | None = None
    held_count: int | None = None
    accept_next: bool = False
    last_new: float | None = None
    confirm_error: str = ""


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
                    "INSERT INTO items (id, connector, external_id, handle, title, url, due, upstream, updated, "
                    "excerpt, meta, state, reason, area, first_seen, last_seen, last_changed) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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

    def source(self, connector: str) -> SourceState:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM sources WHERE connector = ?", (connector,)).fetchone()
        if row is None:
            return SourceState(connector)
        return SourceState(
            connector,
            row["cursor"],
            row["first_attempt"],
            row["last_attempt"],
            row["last_ok"],
            row["failures"],
            row["last_error"],
            row["applied_count"],
            row["held_count"],
            bool(row["accept_next"]),
            row["last_new"],
            row["confirm_error"],
        )

    def sources(self) -> list[SourceState]:
        with self._connect() as connection:
            names = [row["connector"] for row in connection.execute("SELECT connector FROM sources ORDER BY 1")]
        return [self.source(name) for name in names]

    def begin_poll(self, connector: str) -> None:
        _check_connector(connector)
        with self._connect() as connection:
            now = time.time()
            connection.execute(
                "INSERT INTO sources VALUES (?, '', ?, ?, NULL, 0, '', NULL, NULL, 0, NULL, '') "
                "ON CONFLICT(connector) DO UPDATE SET last_attempt = excluded.last_attempt",
                (connector, now, now),
            )

    def poll_failed(self, connector: str, error: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE sources SET failures = failures + 1, last_error = ? WHERE connector = ?", (error, connector)
            )
            connection.execute(
                "INSERT INTO source_runs (connector, at, ok, count, new_items, held, error) VALUES (?,?,0,0,0,0,?)",
                (connector, time.time(), error),
            )

    def poll_succeeded(
        self, connector: str, *, count: int, new_items: int, cursor: str, applied: bool, held: bool
    ) -> None:
        with self._connect() as connection:
            now = time.time()
            connection.execute(
                "UPDATE sources SET failures = 0, last_error = '', last_ok = ?, cursor = ?, "
                "last_new = CASE WHEN ? > 0 THEN ? ELSE last_new END, "
                "applied_count = CASE WHEN ? THEN ? ELSE applied_count END, "
                "held_count = CASE WHEN ? THEN ? ELSE NULL END, "
                "accept_next = CASE WHEN ? THEN 0 ELSE accept_next END WHERE connector = ?",
                (now, cursor, new_items, now, applied, count, held, count, applied, connector),
            )
            connection.execute(
                "INSERT INTO source_runs (connector, at, ok, count, new_items, held, error) VALUES (?,?,1,?,?,?,'')",
                (connector, now, count, new_items, int(held)),
            )

    def vanished(self, connector: str, limit: int) -> list[Item]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM items WHERE connector = ? AND missing_since IS NOT NULL AND state NOT IN (?, ?) "
                "ORDER BY confirm_attempt IS NOT NULL, confirm_attempt LIMIT ?",
                (connector, ItemState.DONE.value, ItemState.IGNORED.value, limit),
            ).fetchall()
            now = time.time()
            connection.executemany(
                "UPDATE items SET confirm_attempt = ? WHERE id = ?", [(now, row["id"]) for row in rows]
            )
        return [_item(row) for row in rows]

    def confirm_round(self, connector: str, error: str) -> None:
        with self._connect() as connection:
            connection.execute("UPDATE sources SET confirm_error = ? WHERE connector = ?", (error, connector))

    def accept_next_snapshot(self, connector: str) -> None:
        with self._connect() as connection:
            cursor = connection.execute("UPDATE sources SET accept_next = 1 WHERE connector = ?", (connector,))
            if cursor.rowcount != 1:
                raise LedgerError(f"no source {connector} has been polled yet")

    def announced(self, identifier: str, key: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM announcements WHERE item_id = ? AND key = ?", (identifier, key)
            ).fetchone()
        return row is not None

    def announce_once(self, identifier: str, key: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO announcements VALUES (?, ?, ?)", (identifier, key, time.time())
            )
        return cursor.rowcount == 1

    def events_since(
        self, since: float, until: float, *, to_state: ItemState, note: str = ""
    ) -> list[tuple[Item, ItemEvent]]:
        query = "SELECT * FROM item_events WHERE to_state = ? AND at > ? AND at <= ?"
        params: list[object] = [to_state.value, since, until]
        if note:
            query += " AND note = ?"
            params.append(note)
        else:
            query += " AND (from_state IS NULL OR from_state != ?)"
            params.append(to_state.value)
        with self._connect() as connection:
            events = connection.execute(query + " ORDER BY seq", params).fetchall()
        latest: dict[str, tuple[Item, ItemEvent]] = {}
        for event in events:
            item = self.peek(event["item_id"])
            if item is not None:
                latest[item.id] = (item, _event(event))
        return list(latest.values())

    def last_brief(self, now: float) -> float | None:
        with self._connect() as connection:
            has_outbox = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'outbox'"
            ).fetchone()
            delivered = (
                " AND NOT EXISTS (SELECT 1 FROM outbox o WHERE o.seq BETWEEN b.first_seq AND b.last_seq "
                "AND o.sent_at IS NULL)"
                if has_outbox
                else ""
            )
            row = connection.execute(
                f"SELECT MAX(b.built_at) AS at FROM briefs b WHERE b.built_at <= ?{delivered}", (now,)
            ).fetchone()
        return row["at"] if row and row["at"] is not None else None

    def brief_days(self) -> set[str]:
        with self._connect() as connection:
            return {row["day"] for row in connection.execute("SELECT day FROM briefs")}

    def save_brief(self, day: str, built_at: float, channel: str, parts: Sequence[str], shown: Sequence[str]) -> bool:
        with self._connect() as connection:
            if connection.execute("SELECT 1 FROM briefs WHERE day = ?", (day,)).fetchone():
                return False
            seqs = [
                int(
                    connection.execute(
                        "INSERT INTO outbox (ticket_id, channel, text) VALUES (?, ?, ?)", ("brief", channel, part)
                    ).lastrowid
                    or 0
                )
                for part in parts
            ]
            connection.execute(
                "INSERT INTO briefs VALUES (?, ?, ?, ?)", (day, built_at, min(seqs, default=0), max(seqs, default=0))
            )
            connection.executemany("UPDATE items SET last_briefed = ? WHERE id = ?", [(built_at, i) for i in shown])
        return True

    def begin_worker_brief(self, item_id: str, digest: str) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO worker_briefs (item_id, digest, at, text, failures, needs_approval) "
                "VALUES (?, ?, ?, '', '[]', 0)",
                (item_id, digest, time.time()),
            )
        return int(cursor.lastrowid or 0)

    def finish_worker_brief(self, seq: int, text: str, failures: Sequence[str], needs_approval: bool) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE worker_briefs SET text = ?, failures = ?, needs_approval = ? WHERE seq = ?",
                (text, json.dumps(list(failures)), int(needs_approval), seq),
            )

    def cached_worker_brief(self, item_id: str, digest: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT text FROM worker_briefs WHERE item_id = ? AND digest = ? AND text != '' "
                "ORDER BY seq DESC LIMIT 1",
                (item_id, digest),
            ).fetchone()
        return None if row is None else str(row["text"])

    def worker_briefs_since(self, since: float) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS n FROM worker_briefs WHERE at >= ?", (since,)).fetchone()
        return int(row["n"])

    def peek(self, identifier: str) -> Item | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM items WHERE id = ?", (identifier,)).fetchone()
        return _item(row) if row else None

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
        return [_event(event) for event in events]


def _event(event: sqlite3.Row) -> ItemEvent:
    return ItemEvent(
        event["at"],
        event["actor"],
        ItemState(event["from_state"]) if event["from_state"] else None,
        ItemState(event["to_state"]),
        event["area"],
        event["note"],
        json.loads(event["content"]) if event["content"] else None,
    )


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
        last_briefed=row["last_briefed"],
    )

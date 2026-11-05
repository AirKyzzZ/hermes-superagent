from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from .domain import (
    FINAL_REPORT_STATES,
    Admission,
    Assignment,
    AssignmentStatus,
    Mandate,
    RepairAction,
    Report,
    ReportState,
    Strength,
    Ticket,
    TicketStatus,
    Trigger,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS triggers (
    id TEXT PRIMARY KEY, source TEXT NOT NULL, thread TEXT NOT NULL, actor TEXT NOT NULL, text TEXT NOT NULL,
    channel TEXT NOT NULL, addressed INTEGER NOT NULL, is_bot INTEGER NOT NULL,
    admission TEXT NOT NULL, reason TEXT NOT NULL, policy_version TEXT NOT NULL, received_at REAL NOT NULL,
    assessed_at REAL, assessment TEXT
);
CREATE TABLE IF NOT EXISTS tickets (
    id TEXT PRIMARY KEY, goal TEXT NOT NULL, mandate TEXT NOT NULL, channel TEXT NOT NULL,
    status TEXT NOT NULL, created_at REAL NOT NULL, trigger_id TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS subscriptions (
    thread TEXT NOT NULL, ticket_id TEXT NOT NULL, PRIMARY KEY (thread, ticket_id)
);
CREATE TABLE IF NOT EXISTS assignments (
    id TEXT PRIMARY KEY, ticket_id TEXT NOT NULL, attempt INTEGER NOT NULL, token_hash TEXT NOT NULL,
    status TEXT NOT NULL, pid INTEGER, started_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS reports (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, assignment_id TEXT NOT NULL, ticket_id TEXT NOT NULL,
    state TEXT NOT NULL, summary TEXT NOT NULL, question TEXT NOT NULL, at REAL NOT NULL, relayed_at REAL
);
CREATE TABLE IF NOT EXISTS inputs (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id TEXT NOT NULL, label TEXT NOT NULL, text TEXT NOT NULL,
    trusted INTEGER NOT NULL, delivered_at REAL
);
CREATE TABLE IF NOT EXISTS outbox (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id TEXT NOT NULL, channel TEXT NOT NULL, text TEXT NOT NULL,
    sent_at REAL
);
CREATE TABLE IF NOT EXISTS activities (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, assignment_id TEXT NOT NULL, fingerprint TEXT NOT NULL, at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS watchdog_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, assignment_id TEXT NOT NULL, ticket_id TEXT NOT NULL,
    verdict TEXT NOT NULL, action TEXT NOT NULL, reason TEXT NOT NULL, at REAL NOT NULL, relayed_at REAL
);
"""


class StaleAssignmentError(PermissionError):
    pass


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def record_trigger(self, trigger: Trigger, admission: Admission, reason: str, policy_version: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO triggers VALUES (?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL)",
                (
                    trigger.id,
                    trigger.source,
                    trigger.thread,
                    trigger.actor,
                    trigger.text,
                    trigger.channel,
                    int(trigger.addressed),
                    int(trigger.is_bot),
                    admission.value,
                    reason,
                    policy_version,
                    time.time(),
                ),
            )
            return cursor.rowcount == 1

    def admission_of(self, trigger_id: str) -> Admission | None:
        with self._connect() as connection:
            row = connection.execute("SELECT admission FROM triggers WHERE id = ?", (trigger_id,)).fetchone()
        return Admission(row["admission"]) if row else None

    def get_trigger(self, trigger_id: str) -> Trigger | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM triggers WHERE id = ?", (trigger_id,)).fetchone()
        if row is None:
            return None
        return Trigger(
            id=row["id"],
            source=row["source"],
            thread=row["thread"],
            actor=row["actor"],
            text=row["text"],
            channel=row["channel"],
            addressed=bool(row["addressed"]),
            is_bot=bool(row["is_bot"]),
        )

    def create_ticket(self, goal: str, mandate: Mandate, channel: str, thread: str) -> tuple[Ticket, bool]:
        ticket_id = f"tk-{secrets.token_hex(4)}"
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO tickets VALUES (?,?,?,?,?,?,?)",
                (
                    ticket_id,
                    goal,
                    json.dumps(asdict(mandate)),
                    channel,
                    TicketStatus.OPEN.value,
                    time.time(),
                    mandate.trigger_id,
                ),
            )
            if cursor.rowcount == 1:
                connection.execute("INSERT INTO subscriptions VALUES (?,?)", (thread, ticket_id))
                return Ticket(ticket_id, goal, mandate, channel, TicketStatus.OPEN), True
            row = connection.execute("SELECT * FROM tickets WHERE trigger_id = ?", (mandate.trigger_id,)).fetchone()
        return _ticket_from_row(row), False

    def ticket_for_trigger(self, trigger_id: str) -> Ticket | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM tickets WHERE trigger_id = ?", (trigger_id,)).fetchone()
        return _ticket_from_row(row) if row else None

    def get_ticket(self, ticket_id: str) -> Ticket:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if row is None:
            raise KeyError(f"no such ticket: {ticket_id}")
        return _ticket_from_row(row)

    def list_tickets(self, status: TicketStatus | None = None) -> list[Ticket]:
        query = "SELECT * FROM tickets"
        args: tuple[str, ...] = ()
        if status is not None:
            query += " WHERE status = ?"
            args = (status.value,)
        with self._connect() as connection:
            rows = connection.execute(query + " ORDER BY created_at", args).fetchall()
        return [_ticket_from_row(row) for row in rows]

    def end_ticket(self, ticket_id: str) -> None:
        with self._connect() as connection:
            connection.execute("UPDATE tickets SET status = ? WHERE id = ?", (TicketStatus.ENDED.value, ticket_id))

    def subscribers(self, thread: str) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT s.ticket_id FROM subscriptions s JOIN tickets t ON t.id = s.ticket_id "
                "WHERE s.thread = ? AND t.status = ?",
                (thread, TicketStatus.OPEN.value),
            ).fetchall()
        return [row["ticket_id"] for row in rows]

    def create_assignment(self, ticket_id: str) -> tuple[Assignment, str]:
        token = secrets.token_urlsafe(24)
        assignment_id = f"as-{secrets.token_hex(4)}"
        now = time.time()
        with self._connect() as connection:
            attempt = (
                connection.execute(
                    "SELECT COUNT(*) AS n FROM assignments WHERE ticket_id = ?", (ticket_id,)
                ).fetchone()["n"]
                + 1
            )
            connection.execute(
                "UPDATE assignments SET status = ? WHERE ticket_id = ? AND status = ?",
                (AssignmentStatus.ENDED.value, ticket_id, AssignmentStatus.RUNNING.value),
            )
            connection.execute(
                "INSERT INTO assignments VALUES (?,?,?,?,?,?,?)",
                (assignment_id, ticket_id, attempt, hash_token(token), AssignmentStatus.RUNNING.value, None, now),
            )
        return Assignment(assignment_id, ticket_id, attempt, AssignmentStatus.RUNNING, None, now), token

    def set_assignment_pid(self, assignment_id: str, pid: int) -> None:
        with self._connect() as connection:
            connection.execute("UPDATE assignments SET pid = ? WHERE id = ?", (pid, assignment_id))

    def end_assignment(self, assignment_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE assignments SET status = ? WHERE id = ?", (AssignmentStatus.ENDED.value, assignment_id)
            )

    def get_assignment(self, assignment_id: str) -> Assignment:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM assignments WHERE id = ?", (assignment_id,)).fetchone()
        if row is None:
            raise KeyError(f"no such assignment: {assignment_id}")
        return _assignment_from_row(row)

    def running_assignments(self) -> list[Assignment]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM assignments WHERE status = ? ORDER BY started_at", (AssignmentStatus.RUNNING.value,)
            ).fetchall()
        return [_assignment_from_row(row) for row in rows]

    def add_report(
        self, assignment_id: str, token: str, state: ReportState, summary: str, question: str = ""
    ) -> Report:
        now = time.time()
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM assignments WHERE id = ?", (assignment_id,)).fetchone()
            if row is None or not secrets.compare_digest(row["token_hash"], hash_token(token)):
                raise StaleAssignmentError(f"bad credentials for assignment {assignment_id}")
            if row["status"] != AssignmentStatus.RUNNING.value:
                raise StaleAssignmentError(f"assignment {assignment_id} has ended, report refused")
            connection.execute(
                "INSERT INTO reports (assignment_id, ticket_id, state, summary, question, at) VALUES (?,?,?,?,?,?)",
                (assignment_id, row["ticket_id"], state.value, summary, question, now),
            )
            if state in FINAL_REPORT_STATES:
                connection.execute(
                    "UPDATE assignments SET status = ? WHERE id = ?", (AssignmentStatus.ENDED.value, assignment_id)
                )
                connection.execute(
                    "UPDATE tickets SET status = ? WHERE id = ?", (TicketStatus.ENDED.value, row["ticket_id"])
                )
        return Report(assignment_id, row["ticket_id"], state, summary, question, now)

    def reports_for(self, assignment_id: str, limit: int = 20) -> list[Report]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM reports WHERE assignment_id = ? ORDER BY seq DESC LIMIT ?", (assignment_id, limit)
            ).fetchall()
        return [_report_from_row(row) for row in reversed(rows)]

    def ticket_reports(self, ticket_id: str) -> list[Report]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM reports WHERE ticket_id = ? ORDER BY seq", (ticket_id,)).fetchall()
        return [_report_from_row(row) for row in rows]

    def take_unrelayed_reports(self) -> list[Report]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM reports WHERE relayed_at IS NULL ORDER BY seq").fetchall()
            connection.executemany(
                "UPDATE reports SET relayed_at = ? WHERE seq = ?", [(time.time(), row["seq"]) for row in rows]
            )
        return [_report_from_row(row) for row in rows]

    def take_unrelayed_escalations(self) -> list[tuple[str, str]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT seq, ticket_id, reason FROM watchdog_events "
                "WHERE relayed_at IS NULL AND action = ? ORDER BY seq",
                (RepairAction.ESCALATE.value,),
            ).fetchall()
            connection.executemany(
                "UPDATE watchdog_events SET relayed_at = ? WHERE seq = ?", [(time.time(), row["seq"]) for row in rows]
            )
        return [(row["ticket_id"], row["reason"]) for row in rows]

    def pending_candidates(self) -> list[Trigger]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id FROM triggers WHERE admission = ? AND assessed_at IS NULL ORDER BY received_at",
                (Admission.CANDIDATE.value,),
            ).fetchall()
        triggers = [self.get_trigger(row["id"]) for row in rows]
        return [trigger for trigger in triggers if trigger is not None]

    def mark_assessed(self, trigger_id: str, assessment: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE triggers SET assessed_at = ?, assessment = ? WHERE id = ?",
                (time.time(), assessment, trigger_id),
            )

    def assessment_of(self, trigger_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute("SELECT assessment FROM triggers WHERE id = ?", (trigger_id,)).fetchone()
        return row["assessment"] if row else None

    def add_input(self, ticket_id: str, label: str, text: str, *, trusted: bool) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO inputs (ticket_id, label, text, trusted) VALUES (?,?,?,?)",
                (ticket_id, label, text, int(trusted)),
            )

    def take_inputs(self, ticket_id: str) -> list[tuple[str, str, bool]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT seq, label, text, trusted FROM inputs "
                "WHERE ticket_id = ? AND delivered_at IS NULL ORDER BY seq",
                (ticket_id,),
            ).fetchall()
            connection.executemany(
                "UPDATE inputs SET delivered_at = ? WHERE seq = ?", [(time.time(), row["seq"]) for row in rows]
            )
        return [(row["label"], row["text"], bool(row["trusted"])) for row in rows]

    def add_outbox(self, ticket_id: str, channel: str, text: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO outbox (ticket_id, channel, text) VALUES (?,?,?)", (ticket_id, channel, text)
            )

    def pending_outbox(self) -> list[tuple[int, str, str, str]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT seq, ticket_id, channel, text FROM outbox WHERE sent_at IS NULL ORDER BY seq"
            ).fetchall()
        return [(row["seq"], row["ticket_id"], row["channel"], row["text"]) for row in rows]

    def mark_sent(self, seq: int) -> None:
        with self._connect() as connection:
            connection.execute("UPDATE outbox SET sent_at = ? WHERE seq = ?", (time.time(), seq))

    def record_activity(self, assignment_id: str, token: str, fingerprint: str) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT token_hash, status FROM assignments WHERE id = ?", (assignment_id,)
            ).fetchone()
            if row is None or not secrets.compare_digest(row["token_hash"], hash_token(token)):
                raise StaleAssignmentError(f"bad credentials for assignment {assignment_id}")
            if row["status"] != AssignmentStatus.RUNNING.value:
                raise StaleAssignmentError(f"assignment {assignment_id} has ended, activity refused")
            connection.execute(
                "INSERT INTO activities (assignment_id, fingerprint, at) VALUES (?,?,?)",
                (assignment_id, fingerprint, time.time()),
            )

    def recent_activity(self, assignment_id: str, limit: int) -> list[tuple[str, float]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT fingerprint, at FROM activities WHERE assignment_id = ? ORDER BY seq DESC LIMIT ?",
                (assignment_id, limit),
            ).fetchall()
        return [(row["fingerprint"], row["at"]) for row in reversed(rows)]

    def take_admitted(self) -> list[Trigger]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id FROM triggers WHERE admission = ? AND assessed_at IS NULL ORDER BY received_at",
                (Admission.ACT.value,),
            ).fetchall()
            connection.executemany(
                "UPDATE triggers SET assessed_at = ?, assessment = 'offered to router' WHERE id = ?",
                [(time.time(), row["id"]) for row in rows],
            )
        triggers = [self.get_trigger(row["id"]) for row in rows]
        return [trigger for trigger in triggers if trigger is not None]

    def last_watchdog_at(self, assignment_id: str) -> float | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT MAX(at) AS at FROM watchdog_events WHERE assignment_id = ?", (assignment_id,)
            ).fetchone()
        return float(row["at"]) if row["at"] is not None else None

    def add_watchdog_event(self, assignment: Assignment, verdict: str, action: RepairAction, reason: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO watchdog_events (assignment_id, ticket_id, verdict, action, reason, at) "
                "VALUES (?,?,?,?,?,?)",
                (assignment.id, assignment.ticket_id, verdict, action.value, reason, time.time()),
            )

    def repair_count(self, ticket_id: str, action: RepairAction) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS n FROM watchdog_events WHERE ticket_id = ? AND action = ?",
                (ticket_id, action.value),
            ).fetchone()
        return int(row["n"])

    def watchdog_events(self, ticket_id: str) -> list[tuple[str, str, str]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT verdict, action, reason FROM watchdog_events WHERE ticket_id = ? ORDER BY seq", (ticket_id,)
            ).fetchall()
        return [(row["verdict"], row["action"], row["reason"]) for row in rows]


def _ticket_from_row(row: sqlite3.Row) -> Ticket:
    data = json.loads(row["mandate"])
    mandate = Mandate(
        policy_version=data["policy_version"],
        scope=data["scope"],
        strength=Strength(data["strength"]),
        actor=data["actor"],
        source=data["source"],
        trigger_id=data["trigger_id"],
    )
    return Ticket(row["id"], row["goal"], mandate, row["channel"], TicketStatus(row["status"]))


def _assignment_from_row(row: sqlite3.Row) -> Assignment:
    return Assignment(
        row["id"], row["ticket_id"], row["attempt"], AssignmentStatus(row["status"]), row["pid"], row["started_at"]
    )


def _report_from_row(row: sqlite3.Row) -> Report:
    return Report(
        row["assignment_id"], row["ticket_id"], ReportState(row["state"]), row["summary"], row["question"], row["at"]
    )

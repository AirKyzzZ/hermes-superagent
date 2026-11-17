from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from superagent.config import ConfigError, parse_config
from superagent.domain import RepairAction, ReportState, TicketStatus, Trigger
from superagent.gate import Rules
from superagent.router import Outcome, Router
from superagent.store import Store
from superagent.watchdog import WatchdogPolicy, check_once

from .conftest import FakeBackend, unlabelled


def request(number: int, thread: str = "") -> Trigger:
    return Trigger(f"t{number}", "chat", thread or f"th{number}", "alice", f"task {number}", "chat:c1", True, False)


@pytest.fixture
def capped(store: Store, rules: Rules, backend: FakeBackend) -> Router:
    return Router(store, rules, backend, max_running=1)


def ticket_of(router: Router, trigger_id: str) -> str:
    ticket = router.store.ticket_for_trigger(trigger_id)
    assert ticket is not None
    return ticket.id


def finish(router: Router, backend: FakeBackend, ticket_id: str) -> None:
    assignment = next(a for a in router.store.running_assignments() if a.ticket_id == ticket_id)
    token = next(token for ticket, assignment_id, token in backend.started if assignment_id == assignment.id)
    router.store.add_report(assignment.id, token, ReportState.READY, "done")


def test_work_past_the_cap_waits_and_says_so_once(capped: Router, backend: FakeBackend) -> None:
    capped.ingest(request(1))
    assert capped.ingest(request(2)).outcome is Outcome.DISPATCHED
    queued = ticket_of(capped, "t2")
    assert [started[0] for started in backend.started] == [ticket_of(capped, "t1")]
    assert capped.store.queued_tickets()[0].id == queued
    assert [line["state"] for line in capped.status()] == ["starting", "queued"]
    check_once(capped)
    assert [unlabelled(text) for _, _, text in capped.relay()] == [
        "Queued: 1 worker is already running, it starts when one finishes"
    ]


def test_queued_work_starts_in_order_as_workers_finish(capped: Router, backend: FakeBackend) -> None:
    for number in (1, 2, 3):
        capped.ingest(request(number))
    first, second, third = (ticket_of(capped, f"t{number}") for number in (1, 2, 3))
    check_once(capped)
    assert len(backend.started) == 1
    finish(capped, backend, first)
    check_once(capped)
    assert [started[0] for started in backend.started] == [first, second]
    assert capped.store.queued_tickets()[0].id == third
    finish(capped, backend, second)
    check_once(capped)
    assert [started[0] for started in backend.started] == [first, second, third]
    assert capped.store.queued_tickets() == []


def test_new_work_waits_behind_the_queue_when_a_slot_frees_between_ticks(capped: Router, backend: FakeBackend) -> None:
    capped.ingest(request(1))
    capped.ingest(request(2))
    finish(capped, backend, ticket_of(capped, "t1"))
    capped.ingest(request(3))
    assert [started[0] for started in backend.started] == [ticket_of(capped, "t1")]
    check_once(capped)
    assert backend.started[-1][0] == ticket_of(capped, "t2")
    assert capped.store.queued_tickets()[0].id == ticket_of(capped, "t3")


def test_a_queued_ticket_is_not_repaired_as_an_orphan(capped: Router, backend: FakeBackend) -> None:
    capped.ingest(request(1))
    capped.ingest(request(2))
    queued = ticket_of(capped, "t2")
    results = check_once(capped, policy=WatchdogPolicy(orphan_after_seconds=-1))
    assert [result.ticket_id for result in results if result.action is not RepairAction.NONE] == []
    assert capped.store.get_ticket(queued).status is TicketStatus.OPEN
    assert capped.store.repair_count(queued, RepairAction.RESTART) == 0


def test_a_restart_at_the_cap_keeps_its_own_slot(capped: Router, backend: FakeBackend) -> None:
    capped.ingest(request(1))
    capped.ingest(request(2))
    running, queued = ticket_of(capped, "t1"), ticket_of(capped, "t2")
    for times in (3, 1):
        assignment = capped.store.running_assignments()[0]
        for _ in range(times):
            capped.store.add_report(assignment.id, backend.started[-1][2], ReportState.PROGRESS, "same")
        actions = [result.action for result in check_once(capped)]
    assert actions == [RepairAction.RESTART]
    assert [entry[0] for entry in backend.started] == [running, running]
    assert capped.store.queued_tickets()[0].id == queued


def test_an_answer_at_the_cap_queues_the_resumed_ticket_with_the_answer(capped: Router, backend: FakeBackend) -> None:
    capped.ingest(request(1))
    parked = ticket_of(capped, "t1")
    assignment = capped.store.running_assignments()[0]
    capped.store.add_report(assignment.id, backend.started[0][2], ReportState.BLOCKED, "two ways", "A or B?")
    capped.ingest(request(2))
    capped.ingest(Trigger("t3", "chat", "th1", "alice", "use A", "chat:c1", True, False))
    assert capped.store.get_ticket(parked).status is TicketStatus.OPEN
    assert capped.store.queued_tickets()[0].id == parked
    finish(capped, backend, ticket_of(capped, "t2"))
    check_once(capped)
    assert backend.started[-1][0] == parked
    assert "use A" in backend.contexts[-1]


def test_a_cancelled_queued_ticket_never_starts(capped: Router, backend: FakeBackend) -> None:
    capped.ingest(request(1))
    capped.ingest(request(2))
    capped.cancel(ticket_of(capped, "t2"), "alice")
    finish(capped, backend, ticket_of(capped, "t1"))
    check_once(capped)
    assert len(backend.started) == 1
    assert capped.store.queued_tickets() == []


def test_lifting_the_cap_drains_the_queue(store: Store, rules: Rules, backend: FakeBackend) -> None:
    capped = Router(store, rules, backend, max_running=1)
    capped.ingest(request(1))
    capped.ingest(request(2))
    check_once(Router(store, rules, backend))
    assert len(backend.started) == 2


def test_no_cap_by_default(router: Router, backend: FakeBackend) -> None:
    for number in (1, 2, 3):
        router.ingest(request(number))
    assert len(backend.started) == 3
    assert router.store.queued_tickets() == []


def test_max_running_is_read_from_the_worker_table() -> None:
    config = parse_config('[worker]\nargv = ["w", "{brief}"]\nmax_running = 3\n')
    assert config.max_running == 3
    assert parse_config('[worker]\nargv = ["w", "{brief}"]\n').max_running == 0


@pytest.mark.parametrize("value", ["-1", "1.5", "true", '"2"'])
def test_max_running_must_be_a_whole_number(value: str) -> None:
    with pytest.raises(ConfigError, match="max_running"):
        parse_config(f'[worker]\nargv = ["w", "{{brief}}"]\nmax_running = {value}\n')


def test_an_older_database_gains_the_queue_column(tmp_path: Path) -> None:
    path = tmp_path / "superagent.db"
    Store(path)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("ALTER TABLE tickets DROP COLUMN queued_at")
        connection.execute("PRAGMA user_version = 4")
        connection.commit()
    store = Store(path)
    assert store.queued_tickets() == []
    with closing(sqlite3.connect(path)) as connection:
        assert "queued_at" in [row[1] for row in connection.execute("PRAGMA table_info(tickets)")]

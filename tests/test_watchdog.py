from __future__ import annotations

import pytest

from superagent.domain import RepairAction, Report, ReportState, TicketStatus, Trigger, Verdict
from superagent.router import Router
from superagent.store import StaleAssignmentError
from superagent.watchdog import Judgement, WatchdogPolicy, check_once

from .conftest import FakeBackend

REQUEST = Trigger("t1", "chat", "th1", "alice", "fix the login bug", "chat:th1", True, False)


def report(router: Router, backend: FakeBackend, summary: str, times: int = 1) -> None:
    assignment = router.store.running_assignments()[0]
    for _ in range(times):
        router.store.add_report(assignment.id, backend.started[-1][2], ReportState.PROGRESS, summary)


def test_healthy_worker_is_left_alone(router: Router, backend: FakeBackend) -> None:
    router.ingest(REQUEST)
    report(router, backend, "reading the auth module")
    result = check_once(router)[0]
    assert (result.verdict, result.action) == (Verdict.ON_TRACK, RepairAction.NONE)


def test_repeating_worker_is_nudged_then_restarted_then_escalated(router: Router, backend: FakeBackend) -> None:
    router.ingest(REQUEST)
    report(router, backend, "still looking", 3)

    first = check_once(router)[0]
    assert (first.verdict, first.action) == (Verdict.STUCK, RepairAction.NUDGE)
    ticket_id = first.ticket_id
    assert "Watchdog" in router.store.take_inputs(ticket_id)[0][1]

    report(router, backend, "still looking", 1)
    second = check_once(router)[0]
    assert second.action is RepairAction.RESTART
    assert len(backend.started) == 2
    assert backend.stopped == [second.assignment_id]

    report(router, backend, "still looking", 3)
    third = check_once(router)[0]
    assert third.action is RepairAction.ESCALATE
    assert router.store.get_ticket(ticket_id).status is TicketStatus.ENDED
    assert router.store.running_assignments() == []
    assert [text for _, _, text in router.relay()] == ["Needs you: last 3 reports are identical"]


def test_restart_fences_out_the_old_worker(router: Router, backend: FakeBackend) -> None:
    router.ingest(REQUEST)
    report(router, backend, "same", 3)
    check_once(router)
    report(router, backend, "same", 1)
    restarted = check_once(router)[0]
    with pytest.raises(StaleAssignmentError):
        router.store.add_report(restarted.assignment_id, backend.started[0][2], ReportState.READY, "late fake done")


def test_dead_worker_skips_the_nudge(router: Router, backend: FakeBackend) -> None:
    router.ingest(REQUEST)
    backend.alive = False
    result = check_once(router)[0]
    assert result.action is RepairAction.RESTART
    assert "gone" in result.reason


def test_silent_worker_counts_as_stuck(router: Router) -> None:
    router.ingest(REQUEST)
    result = check_once(router, policy=WatchdogPolicy(stall_after_seconds=-1))[0]
    assert result.verdict is Verdict.STUCK


class DriftingJudge:
    def judge(self, goal: str, reports: tuple[Report, ...]) -> Judgement:
        return Judgement(Verdict.DRIFTING, f"reports do not serve the goal: {goal}")


def test_semantic_drift_triggers_a_repair(router: Router, backend: FakeBackend) -> None:
    router.ingest(REQUEST)
    report(router, backend, "refactoring the billing module")
    result = check_once(router, judge=DriftingJudge())[0]
    assert (result.verdict, result.action) == (Verdict.DRIFTING, RepairAction.NUDGE)


def test_judge_only_runs_on_new_reports(router: Router, backend: FakeBackend) -> None:
    calls: list[str] = []

    class CountingJudge:
        def judge(self, goal: str, reports: tuple[Report, ...]) -> Judgement:
            calls.append(goal)
            return Judgement(Verdict.ON_TRACK, "fine")

    router.ingest(REQUEST)
    report(router, backend, "working")
    check_once(router, judge=CountingJudge())
    check_once(router, judge=CountingJudge())
    assert len(calls) == 1


def test_repeated_identical_tool_calls_count_as_stuck(router: Router, backend: FakeBackend) -> None:
    router.ingest(REQUEST)
    assignment = router.store.running_assignments()[0]
    for _ in range(5):
        router.store.record_activity(assignment.id, backend.started[0][2], "same-call")
    result = check_once(router)[0]
    assert result.verdict is Verdict.STUCK
    assert "same tool call" in result.reason


def test_varied_tool_calls_count_as_alive(router: Router, backend: FakeBackend) -> None:
    router.ingest(REQUEST)
    assignment = router.store.running_assignments()[0]
    for index in range(5):
        router.store.record_activity(assignment.id, backend.started[0][2], f"call-{index}")
    assert check_once(router)[0].verdict is Verdict.ON_TRACK


def test_stale_worker_cannot_record_activity(router: Router, backend: FakeBackend) -> None:
    router.ingest(REQUEST)
    assignment = router.store.running_assignments()[0]
    router.store.end_assignment(assignment.id)
    with pytest.raises(StaleAssignmentError):
        router.store.record_activity(assignment.id, backend.started[0][2], "late")


def test_every_check_is_recorded(router: Router, backend: FakeBackend) -> None:
    router.ingest(REQUEST)
    report(router, backend, "working")
    result = check_once(router)[0]
    assert router.store.watchdog_events(result.ticket_id)[0][1] == "none"

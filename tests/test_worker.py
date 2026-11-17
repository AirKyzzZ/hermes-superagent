from __future__ import annotations

import contextlib
import json
import os
import signal
import stat
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path

import pytest

from superagent.domain import Assignment, AssignmentStatus, Mandate, Strength, Ticket, TicketStatus
from superagent.worker import (
    IDENTITY_FILENAME,
    SubprocessBackend,
    WorkerStopError,
    _list_processes,
    _Process,
    render_brief,
    write_identity,
)

STUBBORN = (
    "import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\nopen('ready', 'w').close()\ntime.sleep(60)\n"
)
PRINT_ENV = "import json, os; print(json.dumps(dict(os.environ)))"
SLEEPER = [sys.executable, "-c", "import time; time.sleep(30)"]


def setsid_grandchild(*, ignore_sigterm: bool = False, worker_stays: bool = True) -> str:
    grandchild = (
        "import os, pathlib, signal, time\n"
        + ("signal.signal(signal.SIGTERM, signal.SIG_IGN)\n" if ignore_sigterm else "")
        + "pathlib.Path('grandchild.tmp').write_text(str(os.getpid()))\n"
        "os.replace('grandchild.tmp', 'grandchild')\n"
        "time.sleep(60)\n"
    )
    return (
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', {grandchild!r}], start_new_session=True)\n"
        + ("time.sleep(60)\n" if worker_stays else "")
    )


def ticket(goal: str = "a task") -> Ticket:
    mandate = Mandate("v1", goal, Strength.EXPLICIT, "alice", "chat", "t1")
    return Ticket("tk-1", goal, mandate, "chat:1", TicketStatus.OPEN)


def assignment(pid: int | None = None) -> Assignment:
    return Assignment("as-1", "tk-1", 1, AssignmentStatus.RUNNING, pid, time.time())


def start_with_grandchild(tmp_path: Path, script: str) -> tuple[SubprocessBackend, Assignment, int]:
    worker = tmp_path / "worker.py"
    worker.write_text(script, encoding="utf-8")
    backend = SubprocessBackend([sys.executable, str(worker), "{brief}"], tmp_path / "w")
    started = assignment()
    pid = backend.start(ticket(), started, "token")
    pid_file = tmp_path / "w" / "tk-1" / "as-1" / "grandchild"
    deadline = time.time() + 10
    while not pid_file.exists():
        assert time.time() < deadline
        time.sleep(0.05)
    running = Assignment(started.id, started.ticket_id, 1, started.status, pid, started.started_at)
    return backend, running, int(pid_file.read_text())


def ps_field(pid: int, field: str) -> str:
    return subprocess.run(["ps", "-o", f"{field}=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()


def is_gone(pid: int) -> bool:
    state = ps_field(pid, "stat")
    return not state or "Z" in state


def kill_quietly(pid: int) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGKILL)


def test_stop_kills_a_worker_that_ignores_sigterm(tmp_path: Path) -> None:
    script = tmp_path / "stubborn.py"
    script.write_text(STUBBORN, encoding="utf-8")
    backend = SubprocessBackend([sys.executable, str(script), "{brief}"], tmp_path / "w")
    started = assignment()
    pid = backend.start(ticket(), started, "token")
    running = Assignment(started.id, started.ticket_id, 1, started.status, pid, started.started_at)
    ready = tmp_path / "w" / "tk-1" / "as-1" / "ready"
    deadline = time.time() + 10
    while not ready.exists() and time.time() < deadline:
        time.sleep(0.05)
    assert ready.exists()
    assert backend.is_alive(running)
    backend.stop(running)
    deadline = time.time() + 5
    while backend.is_alive(running) and time.time() < deadline:
        time.sleep(0.05)
    assert not backend.is_alive(running)


def test_stop_tolerates_a_worker_that_already_exited(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("superagent.worker._STOP_GRACE_SECONDS", 0.2)
    started_at = time.time()
    zombie = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    deadline = time.time() + 10
    while (
        "Z" not in subprocess.run(["ps", "-o", "stat=", "-p", str(zombie.pid)], capture_output=True, text=True).stdout
    ):
        assert time.time() < deadline
        time.sleep(0.05)
    backend = SubprocessBackend(["worker", "{brief}"], tmp_path / "w")
    exited = Assignment("as-1", "tk-1", 1, AssignmentStatus.RUNNING, zombie.pid, started_at)
    try:
        backend.stop(exited)
    finally:
        zombie.wait()


def test_stop_reports_its_group_when_it_refuses_signals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_kill = os.kill

    def refuse(pid: int, signum: int) -> None:
        if signum:
            raise PermissionError(1, "Operation not permitted")
        real_kill(pid, signum)

    monkeypatch.setattr("superagent.worker._STOP_GRACE_SECONDS", 0.2)
    monkeypatch.setattr("superagent.worker._KILL_GRACE_SECONDS", 0.2)
    started_at = time.time()
    leader = subprocess.Popen(SLEEPER, start_new_session=True)
    backend = SubprocessBackend(["worker", "{brief}"], tmp_path / "w")
    try:
        monkeypatch.setattr("superagent.worker.os.kill", refuse)
        with pytest.raises(WorkerStopError, match=rf"still running after SIGKILL: {leader.pid}$"):
            backend.stop(Assignment("as-1", "tk-1", 1, AssignmentStatus.RUNNING, leader.pid, started_at))
    finally:
        monkeypatch.undo()
        leader.kill()
        leader.wait()


def test_stop_treats_a_refused_descendant_outside_the_group_as_not_ours(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend, running, grandchild = start_with_grandchild(tmp_path, setsid_grandchild())
    real_kill = os.kill

    def refuse_grandchild(pid: int, signum: int) -> None:
        if pid == grandchild and signum:
            raise PermissionError(1, "Operation not permitted")
        real_kill(pid, signum)

    try:
        assert running.pid is not None
        monkeypatch.setattr("superagent.worker.os.kill", refuse_grandchild)
        backend.stop(running)
        assert is_gone(running.pid)
        assert not is_gone(grandchild)
    finally:
        monkeypatch.undo()
        kill_quietly(grandchild)


def test_stop_kills_a_child_that_outlives_its_leader(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    started_at = time.time()
    leader = subprocess.Popen(
        ["/bin/sh", "-c", f"sleep 60 >/dev/null 2>&1 & echo $! > {pid_file}; exit 0"], start_new_session=True
    )
    leader.wait()
    child = int(pid_file.read_text())
    backend = SubprocessBackend(["worker", "{brief}"], tmp_path / "w")
    backend.stop(Assignment("as-1", "tk-1", 1, AssignmentStatus.RUNNING, leader.pid, started_at))
    deadline = time.time() + 5
    while time.time() < deadline:
        state = subprocess.run(["ps", "-o", "stat=", "-p", str(child)], capture_output=True, text=True).stdout.strip()
        if not state or "Z" in state:
            return
        time.sleep(0.05)
    os.kill(child, 9)
    pytest.fail("child survived stop")


def test_stop_refuses_a_leaderless_group_too_old_to_identify(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"
    leader = subprocess.Popen(
        ["/bin/sh", "-c", f"sleep 60 >/dev/null 2>&1 & echo $! > {pid_file}; exit 0"], start_new_session=True
    )
    leader.wait()
    child = int(pid_file.read_text())
    backend = SubprocessBackend(["worker", "{brief}"], tmp_path / "w")
    stale = Assignment("as-1", "tk-1", 1, AssignmentStatus.RUNNING, leader.pid, time.time() - 86400)
    try:
        with pytest.raises(WorkerStopError, match="too old"):
            backend.stop(stale)
        os.kill(child, 0)
    finally:
        os.kill(child, 9)


def test_stop_refuses_when_it_cannot_read_a_live_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    live = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
    monkeypatch.setattr("superagent.worker._process_started_at", lambda pid: None)
    backend = SubprocessBackend(["worker", "{brief}"], tmp_path / "w")
    try:
        with pytest.raises(WorkerStopError, match="could not read"):
            backend.stop(Assignment("as-1", "tk-1", 1, AssignmentStatus.RUNNING, live.pid, time.time()))
        assert live.poll() is None
    finally:
        live.kill()
        live.wait()


def test_stop_reports_processes_that_survive_sigkill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = max(os.getpid(), os.getpgrp()) + 1
    sent: list[tuple[int, int]] = []
    monkeypatch.setattr("superagent.worker.os.kill", lambda pid, signum: sent.append((pid, signum)))
    monkeypatch.setattr("superagent.worker._list_processes", lambda: [_Process(fake, 1, fake, False)])
    monkeypatch.setattr("superagent.worker._process_started_at", lambda pid: 1000.0)
    monkeypatch.setattr("superagent.worker._STOP_GRACE_SECONDS", 0.0)
    monkeypatch.setattr("superagent.worker._KILL_GRACE_SECONDS", 0.0)
    backend = SubprocessBackend(["worker", "{brief}"], tmp_path / "w")
    with pytest.raises(WorkerStopError, match=rf"still running after SIGKILL: {fake}$"):
        backend.stop(Assignment("as-1", "tk-1", 1, AssignmentStatus.RUNNING, fake, 1000.0))
    assert sent == [(fake, signal.SIGTERM), (fake, signal.SIGKILL)]


@pytest.mark.parametrize("recovers", [True, False])
def test_a_ps_timeout_after_sigterm_still_sends_sigkill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recovers: bool
) -> None:
    fake = max(os.getpid(), os.getpgrp()) + 1
    sent: list[tuple[int, int]] = []
    listings = iter([f"{fake} 1 {fake} S\n", None])

    def ps(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
        stdout = next(listings, "" if recovers else None)
        if stdout is None:
            raise subprocess.TimeoutExpired(list(argv), 10.0)
        return subprocess.CompletedProcess(list(argv), 0, stdout, "")

    monkeypatch.setattr("superagent.worker._ps", ps)
    monkeypatch.setattr("superagent.worker._process_started_at", lambda pid: 1000.0)
    monkeypatch.setattr("superagent.worker.os.kill", lambda pid, signum: sent.append((pid, signum)))
    backend = SubprocessBackend(["worker", "{brief}"], tmp_path / "w")
    running = Assignment("as-1", "tk-1", 1, AssignmentStatus.RUNNING, fake, 1000.0)
    if recovers:
        backend.stop(running)
    else:
        with pytest.raises(WorkerStopError, match="could not list processes: ps timed out"):
            backend.stop(running)
    assert sent == [(fake, signal.SIGTERM), (fake, signal.SIGKILL)]


def test_stop_kills_a_setsid_grandchild_and_spares_unrelated_processes(tmp_path: Path) -> None:
    unrelated = subprocess.Popen(SLEEPER, start_new_session=True)
    backend, running, grandchild = start_with_grandchild(tmp_path, setsid_grandchild())
    try:
        assert running.pid is not None
        assert ps_field(grandchild, "pgid") != str(running.pid)
        backend.stop(running)
        assert is_gone(running.pid)
        assert is_gone(grandchild)
        assert unrelated.poll() is None
    finally:
        kill_quietly(grandchild)
        unrelated.kill()
        unrelated.wait()


def test_stop_cannot_find_a_process_that_left_the_group_and_lost_its_parent(tmp_path: Path) -> None:
    backend, running, grandchild = start_with_grandchild(tmp_path, setsid_grandchild(worker_stays=False))
    try:
        assert running.pid is not None
        deadline = time.time() + 10
        while not is_gone(running.pid) or ps_field(grandchild, "ppid") == str(running.pid):
            assert time.time() < deadline
            time.sleep(0.05)
        backend.stop(running)
        assert not is_gone(grandchild)
    finally:
        kill_quietly(grandchild)


def test_stop_kills_a_descendant_that_ignores_sigterm_after_its_parent_dies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("superagent.worker._STOP_GRACE_SECONDS", 0.5)
    backend, running, grandchild = start_with_grandchild(tmp_path, setsid_grandchild(ignore_sigterm=True))
    try:
        backend.stop(running)
        assert is_gone(grandchild)
    finally:
        kill_quietly(grandchild)


def test_a_hung_ps_fails_the_stop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def hung(argv: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(argv, 10.0)

    monkeypatch.setattr("superagent.worker.subprocess.run", hung)
    with pytest.raises(WorkerStopError, match="could not list processes: ps timed out"):
        _list_processes()
    with pytest.raises(WorkerStopError, match="ps timed out reading the start time of process 4242"):
        SubprocessBackend(["worker"], tmp_path).stop(Assignment("as-1", "tk-1", 1, AssignmentStatus.RUNNING, 4242, 0.0))


def test_stop_leaves_a_reused_pid_alone(tmp_path: Path) -> None:
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
    try:
        backend = SubprocessBackend(["worker", "{brief}"], tmp_path / "w")
        backend.stop(Assignment("as-1", "tk-1", 1, AssignmentStatus.RUNNING, other.pid, time.time() - 3600))
        assert other.poll() is None
    finally:
        other.kill()
        other.wait()


def test_worker_env_is_an_allowlist(tmp_path: Path) -> None:
    os.environ["SUPERAGENT_TEST_SECRET"] = "hunter2"
    os.environ["HERMES_YOLO_MODE"] = "1"
    try:
        script = tmp_path / "env.py"
        script.write_text(PRINT_ENV, encoding="utf-8")
        backend = SubprocessBackend([sys.executable, str(script), "{brief}"], tmp_path / "w")
        backend.start(ticket(), assignment(), "token")
        log = tmp_path / "w" / "tk-1" / "as-1" / "worker.log"
        deadline = time.time() + 10
        while (not log.exists() or not log.read_text()) and time.time() < deadline:
            time.sleep(0.05)
        output = log.read_text()
    finally:
        del os.environ["SUPERAGENT_TEST_SECRET"]
        del os.environ["HERMES_YOLO_MODE"]
    assert "hunter2" not in output
    assert "HERMES_YOLO_MODE" not in output
    assert "SUPERAGENT_" not in output
    assert "token" not in output


def test_goal_never_reaches_argv(tmp_path: Path) -> None:
    script = tmp_path / "argv.py"
    script.write_text("import sys; print(sys.argv)", encoding="utf-8")
    backend = SubprocessBackend([sys.executable, str(script), "{brief}"], tmp_path / "w")
    backend.start(ticket("--dangerous-flag; rm -rf /"), assignment(), "token")
    log = tmp_path / "w" / "tk-1" / "as-1" / "worker.log"
    deadline = time.time() + 10
    while (not log.exists() or not log.read_text()) and time.time() < deadline:
        time.sleep(0.05)
    assert "dangerous" not in log.read_text()
    assert "--dangerous-flag" in (tmp_path / "w" / "tk-1" / "as-1" / "brief.md").read_text()


def test_identity_is_written_for_the_worker_only(tmp_path: Path) -> None:
    write_identity(tmp_path, "tk-1", "as-1", "secret", 2)
    identity = tmp_path / IDENTITY_FILENAME
    assert stat.S_IMODE(identity.stat().st_mode) == 0o600
    assert json.loads(identity.read_text()) == {"assignment": "as-1", "ticket": "tk-1", "token": "secret", "attempt": 2}


def test_last_output_is_the_tail_of_the_worker_log_without_terminal_codes(tmp_path: Path) -> None:
    backend = SubprocessBackend(["worker", "{brief}"], tmp_path / "w")
    log = tmp_path / "w" / "tk-1" / "as-1" / "worker.log"
    log.parent.mkdir(parents=True)
    log.write_text("noise\n" * 2000 + "\x1b[1mFinal answer: done\x1b[0m\n", encoding="utf-8")
    output = backend.last_output(assignment())
    assert output.endswith("Final answer: done")
    assert "\x1b" not in output
    assert len(output) <= 4000
    assert SubprocessBackend(["worker"], tmp_path / "none").last_output(assignment()) == ""


def test_the_brief_says_who_relays_for_whom() -> None:
    brief = render_brief(ticket("fix the login bug"), attempt=2, on_behalf_of="Sam")
    assert "Relayed by Hermes, the router, for Sam from chat. Ticket tk-1, attempt 2." in brief
    assert brief.index("## Goal\nfix the login bug") < brief.index("## Done means") < brief.index("## Reporting")
    assert "subagents" in brief


def test_the_brief_names_the_sender_when_no_name_is_set() -> None:
    assert "for alice from chat" in render_brief(ticket(), "the last worker asked about Postgres")
    assert "## Context\nthe last worker asked about Postgres" in render_brief(
        ticket(), "the last worker asked about Postgres"
    )

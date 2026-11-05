from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from superagent.domain import Assignment, AssignmentStatus, Mandate, Strength, Ticket, TicketStatus
from superagent.worker import SubprocessBackend

STUBBORN = (
    "import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\nopen('ready', 'w').close()\ntime.sleep(60)\n"
)
PRINT_ENV = "import json, os; print(json.dumps(dict(os.environ)))"


def ticket(goal: str = "a task") -> Ticket:
    mandate = Mandate("v1", goal, Strength.EXPLICIT, "alice", "chat", "t1")
    return Ticket("tk-1", goal, mandate, "chat:1", TicketStatus.OPEN)


def assignment(pid: int | None = None) -> Assignment:
    return Assignment("as-1", "tk-1", 1, AssignmentStatus.RUNNING, pid, time.time())


def test_stop_kills_a_worker_that_ignores_sigterm(tmp_path: Path) -> None:
    script = tmp_path / "stubborn.py"
    script.write_text(STUBBORN, encoding="utf-8")
    backend = SubprocessBackend([sys.executable, str(script), "{brief}"], tmp_path / "db", tmp_path / "w")
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
    backend = SubprocessBackend(["worker", "{brief}"], tmp_path / "db", tmp_path / "w")
    exited = Assignment("as-1", "tk-1", 1, AssignmentStatus.RUNNING, zombie.pid, started_at)
    try:
        backend.stop(exited)
    finally:
        zombie.wait()


def test_worker_env_is_an_allowlist(tmp_path: Path) -> None:
    os.environ["SUPERAGENT_TEST_SECRET"] = "hunter2"
    os.environ["HERMES_YOLO_MODE"] = "1"
    try:
        script = tmp_path / "env.py"
        script.write_text(PRINT_ENV, encoding="utf-8")
        backend = SubprocessBackend([sys.executable, str(script), "{brief}"], tmp_path / "db", tmp_path / "w")
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
    assert "SUPERAGENT_TOKEN" in output


def test_goal_never_reaches_argv(tmp_path: Path) -> None:
    script = tmp_path / "argv.py"
    script.write_text("import sys; print(sys.argv)", encoding="utf-8")
    backend = SubprocessBackend([sys.executable, str(script), "{brief}"], tmp_path / "db", tmp_path / "w")
    backend.start(ticket("--dangerous-flag; rm -rf /"), assignment(), "token")
    log = tmp_path / "w" / "tk-1" / "as-1" / "worker.log"
    deadline = time.time() + 10
    while (not log.exists() or not log.read_text()) and time.time() < deadline:
        time.sleep(0.05)
    assert "dangerous" not in log.read_text()
    assert "--dangerous-flag" in (tmp_path / "w" / "tk-1" / "as-1" / "brief.md").read_text()


def test_allow_same_user_reaches_the_worker_only_when_configured(tmp_path: Path) -> None:
    script = tmp_path / "env.py"
    script.write_text(PRINT_ENV, encoding="utf-8")
    backend = SubprocessBackend(
        [sys.executable, str(script), "{brief}"], tmp_path / "db", tmp_path / "w", allow_same_user=True
    )
    backend.start(ticket(), assignment(), "token")
    log = tmp_path / "w" / "tk-1" / "as-1" / "worker.log"
    deadline = time.time() + 10
    while (not log.exists() or not log.read_text()) and time.time() < deadline:
        time.sleep(0.05)
    assert "SUPERAGENT_ALLOW_SAME_USER" in log.read_text()

from __future__ import annotations

import fcntl
import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .config import DB_FILENAME, WORKDIRS_DIRNAME, Config, load_config
from .domain import Ticket
from .router import Assessor, Router, Sender
from .store import Store
from .watchdog import CheckResult, DriftJudge, check_once
from .worker import SubprocessBackend

logger = logging.getLogger(__name__)

_SEND_TIMEOUT_SECONDS = 60
_TICK_LOCK_FILENAME = "tick.lock"
IGNORED_TRIGGER_RETENTION_SECONDS = 7 * 24 * 3600.0
ALERT_TICKET = "alert"


@dataclass(frozen=True)
class Runtime:
    home: Path
    config: Config
    router: Router

    @property
    def store(self) -> Store:
        return self.router.store


@dataclass(frozen=True)
class TickResult:
    new_tickets: tuple[Ticket, ...] = ()
    checks: tuple[CheckResult, ...] = ()
    delivered: tuple[tuple[str, str, str], ...] = ()
    errors: tuple[str, ...] = ()
    skipped: bool = False


def open_runtime(home: Path) -> Runtime:
    config = load_config(home)
    db_path = home / DB_FILENAME
    backend = SubprocessBackend(config.worker_argv, home / WORKDIRS_DIRNAME)
    return Runtime(home, config, Router(Store(db_path), config.rules, backend))


def argv_sender(argv: tuple[str, ...]) -> Sender | None:
    if not argv:
        return None

    def send(channel: str, text: str) -> None:
        rendered = [part.replace("{channel}", channel).replace("{text}", text) for part in argv]
        subprocess.run(rendered, check=True, timeout=_SEND_TIMEOUT_SECONDS, capture_output=True)

    return send


def tick(runtime: Runtime, judge: DriftJudge | None = None, assessor: Assessor | None = None) -> TickResult:
    lock_fd = os.open(runtime.home / _TICK_LOCK_FILENAME, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return TickResult(skipped=True, errors=_stuck_holder(lock_fd, runtime.config.tick_deadline_seconds))
        os.ftruncate(lock_fd, 0)
        os.pwrite(lock_fd, json.dumps({"pid": os.getpid(), "started": time.time()}).encode(), 0)
        return _run_phases(runtime, judge, assessor)
    finally:
        os.close(lock_fd)


def _stuck_holder(lock_fd: int, deadline: float) -> tuple[str, ...]:
    try:
        holder = json.loads(os.pread(lock_fd, 200, 0) or b"{}")
        held_for = time.time() - float(holder["started"])
    except (OSError, ValueError, KeyError, TypeError):
        return ()
    if held_for <= deadline:
        return ()
    return (f"tick: another tick (pid {holder.get('pid')}) has held the lock for {held_for / 60:.0f} minutes",)


def _run_phases(runtime: Runtime, judge: DriftJudge | None, assessor: Assessor | None) -> TickResult:
    errors: list[str] = [f"config: {runtime.config.problem}"] if runtime.config.problem else []
    tickets: list[Ticket] = []
    checks: list[CheckResult] = []
    delivered: list[tuple[str, str, str]] = []
    if assessor is not None:
        try:
            tickets = runtime.router.assess_candidates(assessor)
        except Exception as error:
            logger.exception("candidate assessment failed")
            errors.append(f"assess: {error}")
    try:
        checks = check_once(runtime.router, judge, runtime.config.watchdog)
    except Exception as error:
        logger.exception("watchdog check failed")
        errors.append(f"watchdog: {error}")
    try:
        delivered = runtime.router.relay(argv_sender(runtime.config.send_argv))
    except Exception as error:
        logger.exception("relay failed")
        errors.append(f"relay: {error}")
    if waiting := runtime.store.failed_send_count():
        errors.append(
            f"relay: {waiting} message(s) not delivered yet, retrying with backoff. `superagent outbox` lists them"
        )
    try:
        runtime.store.prune_ignored_triggers(IGNORED_TRIGGER_RETENTION_SECONDS)
    except Exception as error:
        logger.exception("pruning failed")
        errors.append(f"prune: {error}")
    try:
        delivered += _raise_alerts(runtime, errors)
    except Exception as error:
        logger.exception("alerting failed")
        errors.append(f"alerts: {error}")
    if not errors and runtime.config.heartbeat_argv:
        try:
            subprocess.run(
                runtime.config.heartbeat_argv, check=True, timeout=_SEND_TIMEOUT_SECONDS, capture_output=True
            )
        except (subprocess.SubprocessError, OSError) as error:
            errors.append(f"heartbeat: {error}")
    return TickResult(tuple(tickets), tuple(checks), tuple(delivered), tuple(errors))


def _raise_alerts(runtime: Runtime, errors: list[str]) -> list[tuple[str, str, str]]:
    active: dict[str, str] = {}
    for error in errors:
        key = error.split(":", 1)[0]
        active[key] = f"{active[key]}; {error}" if key in active else error
    raised, resolved = runtime.store.sync_alerts(active, runtime.config.alert_after_seconds)
    channel = runtime.config.alerts_channel
    if not channel or not (raised or resolved):
        return []
    for _, text in raised:
        runtime.store.queue_message(ALERT_TICKET, channel, f"Alert: {text}")
    for _, text in resolved:
        runtime.store.queue_message(ALERT_TICKET, channel, f"Resolved: {text}")
    return runtime.router.relay(argv_sender(runtime.config.send_argv))

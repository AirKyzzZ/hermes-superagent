from __future__ import annotations

import fcntl
import logging
import os
import subprocess
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
            return TickResult(skipped=True)
        return _run_phases(runtime, judge, assessor)
    finally:
        os.close(lock_fd)


def _run_phases(runtime: Runtime, judge: DriftJudge | None, assessor: Assessor | None) -> TickResult:
    errors: list[str] = []
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
    return TickResult(tuple(tickets), tuple(checks), tuple(delivered), tuple(errors))

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import DB_FILENAME, WORKDIRS_DIRNAME, Config, load_config
from .domain import Ticket
from .router import Assessor, Router, Sender
from .store import Store
from .watchdog import CheckResult, DriftJudge, check_once
from .worker import SubprocessBackend

_SEND_TIMEOUT_SECONDS = 60


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
    new_tickets: tuple[Ticket, ...]
    checks: tuple[CheckResult, ...]
    delivered: tuple[tuple[str, str, str], ...]


def open_runtime(home: Path) -> Runtime:
    config = load_config(home)
    db_path = home / DB_FILENAME
    backend = SubprocessBackend(config.worker_argv, db_path, home / WORKDIRS_DIRNAME)
    return Runtime(home, config, Router(Store(db_path), config.rules, backend))


def argv_sender(argv: tuple[str, ...]) -> Sender | None:
    if not argv:
        return None

    def send(channel: str, text: str) -> None:
        rendered = [part.replace("{channel}", channel).replace("{text}", text) for part in argv]
        subprocess.run(rendered, check=True, timeout=_SEND_TIMEOUT_SECONDS, capture_output=True)

    return send


def tick(runtime: Runtime, judge: DriftJudge | None = None, assessor: Assessor | None = None) -> TickResult:
    tickets = runtime.router.assess_candidates(assessor) if assessor is not None else []
    checks = check_once(runtime.router, judge, runtime.config.watchdog)
    delivered = runtime.router.relay(argv_sender(runtime.config.send_argv))
    return TickResult(tuple(tickets), tuple(checks), tuple(delivered))

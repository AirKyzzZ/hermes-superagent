from __future__ import annotations

import fcntl
import os
import shutil
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .config import CONFIG_FILENAME, LAST_GOOD_FILENAME
from .connectors import ConnectorSpec
from .polling import source_health
from .runtime import TICK_LOCK_FILENAME, Runtime, last_tick, open_runtime

TICK_QUIET_SECONDS = 15 * 60.0
PROBLEMS_SHOWN = 3


class Status(StrEnum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str


def diagnose(home: Path, *, poll: bool = True) -> list[Check]:
    if not (home / CONFIG_FILENAME).is_file() and not (home / LAST_GOOD_FILENAME).is_file():
        return [Check("config", Status.FAIL, f"no {CONFIG_FILENAME} in {home}, run superagent setup to write one")]
    try:
        runtime = open_runtime(home)
    except Exception as error:
        return [Check("config", Status.FAIL, str(error) or type(error).__name__)]
    now = time.time()
    checks = [
        _config(runtime),
        _outbound(runtime),
        _command("heartbeat", runtime.config.heartbeat_argv, "not set, so nothing outside notices a stopped timer"),
        _tick(runtime, now),
        _alerts(runtime),
        _outbox(runtime),
    ]
    if poll and runtime.config.connectors:
        with _tick_lock(home) as held:
            if held:
                checks += [_poll(runtime, spec) for spec in runtime.config.connectors]
            else:
                checks.append(Check("connectors", Status.WARN, "a tick is running, so they weren't polled now"))
    for spec in runtime.config.connectors:
        checks += [
            Check(spec.name, Status.WARN, warning.text)
            for warning in source_health(spec, runtime.ledger.source(spec.name), now)
        ]
    return checks


@contextmanager
def _tick_lock(home: Path) -> Iterator[bool]:
    descriptor = os.open(home / TICK_LOCK_FILENAME, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True
    finally:
        os.close(descriptor)


def _config(runtime: Runtime) -> Check:
    if runtime.config.problem:
        return Check("config", Status.FAIL, runtime.config.problem)
    if not runtime.config.alerts_channel:
        return Check("config", Status.WARN, "no alerts channel under [channels], so alerts wait for one")
    count = len(runtime.config.connectors)
    plural = "" if count == 1 else "s"
    return Check("config", Status.OK, f"{count} connector{plural}, alerts to {runtime.config.alerts_channel}")


def _outbound(runtime: Runtime) -> Check:
    config = runtime.config
    channels = [config.alerts_channel, config.brief_channel, config.digest_channel]
    channels += [spec.channel for spec in config.connectors]
    if not config.send_argv and any(channels):
        return Check("outbound", Status.FAIL, "channels are set but [outbound] argv isn't, so their messages are lost")
    return _command("outbound", config.send_argv, "not set, so messages are marked sent and go nowhere")


def _command(name: str, argv: tuple[str, ...], unset: str) -> Check:
    if not argv:
        return Check(name, Status.WARN, unset)
    found = shutil.which(argv[0])
    if found is None:
        return Check(name, Status.FAIL, f"{argv[0]} isn't an executable on this PATH")
    return Check(name, Status.OK, found)


def _tick(runtime: Runtime, now: float) -> Check:
    record = last_tick(runtime.home)
    finished, started = record.get("finished"), record.get("started")
    if isinstance(finished, (int, float)):
        minutes = (now - finished) / 60
        if now - finished > TICK_QUIET_SECONDS:
            return Check(
                "tick", Status.FAIL, f"the last tick finished {minutes:.0f} minutes ago, is the timer running?"
            )
        return Check("tick", Status.OK, f"the last tick finished {minutes:.0f} minutes ago")
    if isinstance(started, (int, float)):
        minutes = (now - started) / 60
        if now - started > runtime.config.tick_deadline_seconds:
            return Check("tick", Status.FAIL, f"the tick started {minutes:.0f} minutes ago never finished")
        return Check("tick", Status.OK, f"a tick has been running for {minutes:.0f} minutes")
    try:
        configured = (runtime.home / CONFIG_FILENAME).stat().st_mtime
    except OSError:
        configured = now
    status = Status.FAIL if now - configured > TICK_QUIET_SECONDS else Status.WARN
    return Check("tick", status, "no tick has run yet, schedule superagent tick every few minutes")


def _alerts(runtime: Runtime) -> Check:
    alerts = runtime.store.active_alerts()
    if not alerts:
        return Check("alerts", Status.OK, "none active")
    shown = "; ".join(str(alert["text"])[:200] for alert in alerts[:PROBLEMS_SHOWN])
    return Check("alerts", Status.FAIL, f"{len(alerts)} active: {shown}")


def _outbox(runtime: Runtime) -> Check:
    failing = runtime.store.failed_send_count()
    waiting = runtime.store.undelivered_count()
    if failing:
        detail = f"{failing} of {waiting} waiting messages failed to send, see superagent outbox"
        return Check("outbox", Status.FAIL, detail)
    return Check("outbox", Status.OK, f"{waiting} waiting")


def _poll(runtime: Runtime, spec: ConnectorSpec) -> Check:
    started = time.monotonic()
    try:
        result = runtime.connectors[spec.name].poll(runtime.ledger.source(spec.name).cursor)
    except Exception as error:
        return Check(spec.name, Status.FAIL, str(error) or type(error).__name__)
    if result.problems:
        return Check(spec.name, Status.FAIL, "; ".join(result.problems[:PROBLEMS_SHOWN]))
    if result.notes:
        return Check(spec.name, Status.WARN, "; ".join(result.notes[:PROBLEMS_SHOWN]))
    took = time.monotonic() - started
    count = len(result.items)
    return Check(spec.name, Status.OK, f"{count} item{'' if count == 1 else 's'} in {took:.1f}s")

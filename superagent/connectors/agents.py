from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePath

from ..agents import PROBE_SCRIPT, SessionState, classify
from ..ledger import SourceItem, Upstream
from . import ConnectorConfigError, ConnectorError, ConnectorSpec, Poll, duration

KEYS = frozenset({"host", "python", "stall_after", "timeout"})
GONE_CLOSES_AFTER_SECONDS = 3600.0
_BASE_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "USER", "SSH_AUTH_SOCK")


@dataclass(frozen=True)
class AgentsConnector:
    host: tuple[str, ...]
    python: str
    stall_after: float
    timeout: float

    def poll(self, cursor: str) -> Poll:
        report = self._probe()
        now = report.get("now")
        sessions = report.get("sessions")
        if not isinstance(now, (int, float)) or not isinstance(sessions, list):
            raise ConnectorError("the probe returned an unexpected shape")
        items = []
        for session in sessions:
            if not isinstance(session, Mapping) or not isinstance(session.get("events"), list):
                raise ConnectorError("the probe returned a session without events")
            alive = session.get("alive")
            view = classify(
                session["events"],
                alive if isinstance(alive, bool) else None,
                float(now),
                self.stall_after,
                busy=session.get("busy") is True,
            )
            over = view.state is SessionState.ENDED or (
                view.state is SessionState.GONE and view.quiet_for > GONE_CLOSES_AFTER_SECONDS
            )
            project = PurePath(view.cwd).name or "session"
            branch = str(session.get("branch") or "")
            meta = {"state": view.state.value, "cwd": view.cwd, "branch": branch}
            if view.error:
                meta["error"] = view.error
            meta.update({f"pane.{key}": value for key, value in view.pane.items()})
            items.append(
                SourceItem(
                    external_id=str(session.get("session")),
                    title=f"{project} · {branch}" if branch else project,
                    upstream=Upstream.CLOSED if over else Upstream.OPEN,
                    excerpt=view.question,
                    meta=meta,
                )
            )
        return Poll(tuple(items), complete=True)

    def confirm(self, external_id: str) -> Upstream:
        return Upstream.CLOSED

    def _probe(self) -> Mapping[str, object]:
        argv = [*self.host, self.python if self.host else sys.executable, "-"]
        env = {name: os.environ[name] for name in _BASE_ENV if name in os.environ}
        env.update({name: os.environ[name] for name in ("SUPERAGENT_SESSIONS_DIR",) if name in os.environ})
        try:
            done = subprocess.run(
                argv,
                input=PROBE_SCRIPT.read_text(encoding="utf-8"),
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise ConnectorError(f"the probe on {' '.join(self.host) or 'this host'} took too long") from error
        except OSError as error:
            raise ConnectorError(f"the probe could not run: {error}") from error
        if done.returncode != 0:
            raise ConnectorError(f"the probe exited {done.returncode}: {done.stderr.strip()[-300:]}")
        try:
            report = json.loads(done.stdout)
        except ValueError as error:
            raise ConnectorError(f"the probe did not print JSON: {error}") from error
        if not isinstance(report, dict):
            raise ConnectorError("the probe returned an unexpected shape")
        return report


def build(spec: ConnectorSpec) -> AgentsConnector:
    where = f"[connectors.{spec.name}]"
    host = spec.settings.get("host", [])
    if not isinstance(host, list) or not all(isinstance(part, str) for part in host):
        raise ConnectorConfigError(f'{where} host must be a list such as ["ssh", "mac"], or empty for this machine')
    python = spec.settings.get("python", "python3")
    if not isinstance(python, str) or not python:
        raise ConnectorConfigError(f"{where} python must be a command name")
    timeout = spec.settings.get("timeout", 30)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 300:
        raise ConnectorConfigError(f"{where} timeout must be between 0 and 300 seconds")
    return AgentsConnector(
        host=tuple(host),
        python=python,
        stall_after=duration(spec.settings.get("stall_after", "20m"), f"{where} stall_after"),
        timeout=float(timeout),
    )

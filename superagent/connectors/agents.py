from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePath

from ..agents import PROBE_SCRIPT, RELAUNCH_SCRIPT, SessionState, classify
from ..ledger import SourceItem, Upstream
from . import ConnectorConfigError, ConnectorError, ConnectorSpec, Poll, duration

KEYS = frozenset(
    {"host", "python", "stall_after", "timeout", "relaunch", "nudge", "retry_after", "claude", "cmux", "tmux"}
)
GONE_CLOSES_AFTER_SECONDS = 3600.0
NOT_HOOKED = "no Claude Code hook has written to {where} on this host, install it with superagent agents --hook-script"
_BASE_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "USER", "SSH_AUTH_SOCK")


@dataclass(frozen=True)
class AgentsConnector:
    host: tuple[str, ...]
    python: str
    stall_after: float
    timeout: float
    relaunch: bool = False
    nudge: str = "continue"
    retry_after: float = 600.0
    claude: str = "claude"
    cmux: str = "cmux"
    tmux: str = "tmux"

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
            if view.state in (SessionState.LIMITED, SessionState.GONE):
                meta["since"] = repr(max(float(str(event.get("t") or 0)) for event in session["events"]))
            if view.state is SessionState.LIMITED:
                meta["auto_resume"] = auto_resume(session["events"])
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
        if report.get("hooked") is not True:
            where = str(report.get("sessions_dir") or "the sessions folder")
            return Poll(tuple(items), complete=False, problems=(NOT_HOOKED.format(where=where),))
        return Poll(tuple(items), complete=True)

    def confirm(self, external_id: str) -> Upstream:
        return Upstream.CLOSED

    def act(self, session: str, action: str, since: str) -> tuple[bool, str]:
        args = {
            "session": session,
            "action": action,
            "since": float(since),
            "text": self.nudge,
            "claude": self.claude,
            "cmux": self.cmux,
            "tmux": self.tmux,
        }
        script = RELAUNCH_SCRIPT.read_text(encoding="utf-8") + f"\nmain({json.dumps(args)!r})\n"
        result = self._run(script)
        done, detail = result.get("done"), result.get("detail")
        if not isinstance(done, bool) or not isinstance(detail, str):
            raise ConnectorError("the relaunch script returned an unexpected shape")
        return done, detail[:300]

    def _probe(self) -> Mapping[str, object]:
        return self._run(PROBE_SCRIPT.read_text(encoding="utf-8"))

    def _run(self, script: str) -> Mapping[str, object]:
        argv = [*self.host, self.python if self.host else sys.executable, "-"]
        env = {name: os.environ[name] for name in _BASE_ENV if name in os.environ}
        env.update({name: os.environ[name] for name in ("SUPERAGENT_SESSIONS_DIR",) if name in os.environ})
        try:
            done = subprocess.run(
                argv,
                input=script,
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


def auto_resume(events: list[object]) -> str:
    found = ""
    for event in events:
        if not isinstance(event, Mapping):
            continue
        if event.get("event") == "StopFailure":
            found = ""
        notice = str(event.get("notification") or "")
        if event.get("event") == "Notification" and notice.startswith("quota_auto_resume_"):
            found = notice.removeprefix("quota_auto_resume_")
    return found


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
    relaunch, nudge = spec.settings.get("relaunch", False), spec.settings.get("nudge", "continue")
    if not isinstance(relaunch, bool):
        raise ConnectorConfigError(f"{where} relaunch must be true or false")
    if not isinstance(nudge, str) or not nudge.strip() or len(nudge) > 200 or any(c in nudge for c in "\r\n\\"):
        raise ConnectorConfigError(f"{where} nudge must be one short line of text, such as continue")
    commands = {key: spec.settings.get(key, key) for key in ("claude", "cmux", "tmux")}
    if not all(isinstance(value, str) and value for value in commands.values()):
        raise ConnectorConfigError(f"{where} claude, cmux and tmux must be commands or paths")
    return AgentsConnector(
        host=tuple(host),
        python=python,
        stall_after=duration(spec.settings.get("stall_after", "20m"), f"{where} stall_after"),
        timeout=float(timeout),
        relaunch=relaunch,
        nudge=nudge,
        retry_after=duration(spec.settings.get("retry_after", "10m"), f"{where} retry_after"),
        claude=str(commands["claude"]),
        cmux=str(commands["cmux"]),
        tmux=str(commands["tmux"]),
    )

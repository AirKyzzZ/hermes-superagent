from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

HOOK_SCRIPT = Path(__file__).with_name("hook.py")
PROBE_SCRIPT = Path(__file__).with_name("probe.py")
HOOK_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "PermissionRequest",
    "Notification",
    "Stop",
    "StopFailure",
    "SubagentStop",
    "SessionEnd",
)
_WAITING_TOOLS = frozenset({"AskUserQuestion", "ExitPlanMode"})
_WAITING_NOTIFICATIONS = frozenset(
    {"permission_prompt", "idle_prompt", "elicitation_dialog", "elicitation_url_dialog", "agent_needs_input"}
)
_TOOL_TIMEOUT_GRACE_SECONDS = 60.0
_SECRETS = (
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"), r"\1[redacted]"),
    (
        re.compile(
            r"(?i)\b(token|secret|password|passwd|pwd|api[_-]?key|access[_-]?key|auth)"
            r"([\"']?\s*[:=]\s*[\"']?)[^\s\"',;]+"
        ),
        r"\1\2[redacted]",
    ),
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"), "[redacted]"),
    (re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|xox[abposr]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16})"), "[redacted]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "[redacted]"),
)
_LONG_TOKEN = re.compile(r"[A-Za-z0-9+_-]{40,}={0,2}")
_SNAKE_NAME = re.compile(r"[a-z]{1,12}(?:_[a-z]{1,12}){3,}")
_SHA = re.compile(r"[0-9a-f]{40}")
_SHA_CONTEXT = re.compile(r"(?i)(?:commit|sha|/commit/|/commits/)\W{0,3}$")


class SessionState(StrEnum):
    IDLE = "idle"
    WORKING = "working"
    BACKGROUND = "background"
    WAITING = "waiting"
    STUCK = "stuck"
    LIMITED = "limited"
    ENDED = "ended"
    GONE = "gone"


ATTENTION_STATES = frozenset({SessionState.WAITING, SessionState.STUCK, SessionState.LIMITED, SessionState.GONE})


@dataclass(frozen=True)
class SessionView:
    state: SessionState
    question: str = ""
    error: str = ""
    cwd: str = ""
    pane: Mapping[str, str] = field(default_factory=dict)
    quiet_for: float = 0.0


def redact(text: str) -> str:
    for pattern, replacement in _SECRETS:
        text = pattern.sub(replacement, text)

    def keep(match: re.Match[str]) -> str:
        token = match[0]
        if _SNAKE_NAME.fullmatch(token):
            return token
        if _SHA.fullmatch(token) and _SHA_CONTEXT.search(match.string[max(0, match.start() - 12) : match.start()]):
            return token
        return "[redacted]"

    return _LONG_TOKEN.sub(keep, text)


def _asks(event: Mapping[str, object]) -> bool:
    name = event.get("event")
    return (
        name == "PermissionRequest"
        or (name == "Notification" and event.get("notification") in _WAITING_NOTIFICATIONS)
        or (name == "PreToolUse" and event.get("tool") in _WAITING_TOOLS)
        or (name == "PostToolUseFailure" and event.get("interrupt") is True)
    )


def _counts(event: Mapping[str, object]) -> bool:
    name = event.get("event")
    if name == "Notification":
        return (
            event.get("notification") in _WAITING_NOTIFICATIONS
            or event.get("notification") == "quota_auto_resume_fired"
        )
    if name == "SessionStart" and event.get("source") == "compact":
        return False
    return not event.get("agent") or _asks(event)


def classify(
    events: Sequence[Mapping[str, object]], alive: bool | None, now: float, stall_after: float, busy: bool = False
) -> SessionView:
    main = [event for event in events if _counts(event)]
    if not main:
        return SessionView(SessionState.WORKING)
    last = main[-1]
    cwd = str(next((event.get("cwd") for event in reversed(main) if event.get("cwd")), ""))
    pane_value = next((event.get("pane") for event in reversed(main) if event.get("pane")), {})
    pane = {str(key): str(value) for key, value in pane_value.items()} if isinstance(pane_value, Mapping) else {}
    quiet_for = now - max(float(str(event.get("t") or 0)) for event in events)
    asked = next(
        (
            str(event.get("text"))
            for event in reversed(main)
            if event.get("text") and event.get("notification") != "idle_prompt"
        ),
        "",
    )
    name = last.get("event")

    def view(state: SessionState, question: str = "", error: str = "") -> SessionView:
        return SessionView(state, redact(question), error, cwd, pane, quiet_for)

    if name == "SessionEnd":
        return view(SessionState.ENDED)
    if alive is False:
        return view(SessionState.GONE)
    if name == "StopFailure":
        return view(SessionState.LIMITED, str(last.get("text") or ""), str(last.get("error") or ""))
    if name == "Stop":
        if last.get("background") and quiet_for <= stall_after:
            return view(SessionState.BACKGROUND, asked)
        return view(SessionState.WAITING, asked)
    if _asks(last) and not (busy and name == "PermissionRequest"):
        return view(SessionState.WAITING, asked if name == "Notification" else str(last.get("text") or asked))
    if name == "SessionStart":
        return view(SessionState.IDLE)
    limit = stall_after
    timeout_ms = last.get("timeout_ms")
    if name == "PreToolUse" and isinstance(timeout_ms, (int, float)):
        limit = max(limit, timeout_ms / 1000 + _TOOL_TIMEOUT_GRACE_SECONDS)
    if quiet_for > limit:
        return view(SessionState.STUCK)
    return view(SessionState.WORKING)


def settings_block(command: str = "python3 ~/.superagent/hook.py") -> dict[str, object]:
    return {"hooks": {event: [{"hooks": [{"type": "command", "command": command}]}] for event in HOOK_EVENTS}}

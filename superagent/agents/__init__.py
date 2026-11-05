from __future__ import annotations

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


def classify(events: Sequence[Mapping[str, object]], alive: bool | None, now: float, stall_after: float) -> SessionView:
    main = [event for event in events if not event.get("agent")]
    if not main:
        return SessionView(SessionState.WORKING)
    last = main[-1]
    cwd = str(next((event.get("cwd") for event in reversed(main) if event.get("cwd")), ""))
    pane_value = next((event.get("pane") for event in reversed(main) if event.get("pane")), {})
    pane = {str(key): str(value) for key, value in pane_value.items()} if isinstance(pane_value, Mapping) else {}
    event = last.get("event")
    text = str(last.get("text") or "")
    view = SessionView(SessionState.WORKING, cwd=cwd, pane=pane)
    if event == "SessionEnd":
        return SessionView(SessionState.ENDED, cwd=cwd, pane=pane)
    if alive is False:
        return SessionView(SessionState.GONE, cwd=cwd, pane=pane)
    if event == "StopFailure":
        return SessionView(SessionState.LIMITED, text, str(last.get("error") or ""), cwd, pane)
    if event == "Stop":
        if last.get("background"):
            return SessionView(SessionState.BACKGROUND, cwd=cwd, pane=pane)
        return SessionView(SessionState.WAITING, text, cwd=cwd, pane=pane)
    if (
        event == "PermissionRequest"
        or (event == "Notification" and last.get("notification") in _WAITING_NOTIFICATIONS)
        or (event == "PreToolUse" and last.get("tool") in _WAITING_TOOLS)
    ):
        return SessionView(SessionState.WAITING, text, cwd=cwd, pane=pane)
    if event == "SessionStart":
        return SessionView(SessionState.IDLE, cwd=cwd, pane=pane)
    latest = max(float(str(item.get("t") or 0)) for item in events)
    limit = stall_after
    timeout_ms = last.get("timeout_ms")
    if event == "PreToolUse" and isinstance(timeout_ms, (int, float)):
        limit = max(limit, timeout_ms / 1000 + _TOOL_TIMEOUT_GRACE_SECONDS)
    if now - latest > limit:
        return SessionView(SessionState.STUCK, cwd=cwd, pane=pane)
    return view


def settings_block(command: str = "python3 ~/.superagent/hook.py") -> dict[str, object]:
    return {"hooks": {event: [{"hooks": [{"type": "command", "command": command}]}] for event in HOOK_EVENTS}}

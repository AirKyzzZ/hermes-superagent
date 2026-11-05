from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

SESSIONS = Path(os.environ.get("SUPERAGENT_SESSIONS_DIR") or Path.home() / ".superagent" / "sessions")
TEXT_LIMIT = 500
LINE_LIMIT = 3800
PID_EVENTS = {"SessionStart", "UserPromptSubmit", "Stop", "StopFailure"}
CLAUDE_NAMES = ("claude", "node")
PANE_KEYS = (
    "TMUX_PANE",
    "TMUX",
    "CMUX_WORKSPACE_ID",
    "CMUX_SURFACE_ID",
    "CMUX_TAB_ID",
    "CMUX_PANEL_ID",
    "CMUX_CLAUDE_PID",
)


def ancestor_claude() -> tuple[int, str]:
    pid = os.getppid()
    for _ in range(6):
        try:
            line = subprocess.run(
                ["ps", "-o", "ppid=,lstart=,comm=", "-p", str(pid)], capture_output=True, text=True, timeout=2
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return 0, ""
        if not line:
            return 0, ""
        parts = line.split()
        if len(parts) < 7:
            return 0, ""
        parent, started, command = int(parts[0]), " ".join(parts[1:6]), " ".join(parts[6:])
        if os.path.basename(command).startswith(CLAUDE_NAMES):
            return pid, started
        pid = parent
    return 0, ""


def question(payload: dict[str, object]) -> str:
    event = payload.get("hook_event_name")
    raw_input = payload.get("tool_input")
    tool_input: dict[str, object] = raw_input if isinstance(raw_input, dict) else {}
    if event == "PreToolUse" and payload.get("tool_name") == "AskUserQuestion":
        asked = tool_input.get("questions")
        if isinstance(asked, list) and asked and isinstance(asked[0], dict):
            return str(asked[0].get("question", ""))
    if event == "PermissionRequest":
        return "permission for {}: {}".format(
            payload.get("tool_name", "a tool"), tool_input.get("description") or tool_input.get("command") or ""
        )
    return str(payload.get("last_assistant_message") or payload.get("message") or "")


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return
    session = re.sub(r"[^A-Za-z0-9-]", "", str(payload.get("session_id", "")))[:80]
    event = str(payload.get("hook_event_name", ""))
    if not session or not event:
        return
    raw_input = payload.get("tool_input")
    tool_input: dict[str, object] = raw_input if isinstance(raw_input, dict) else {}
    background = payload.get("background_tasks")
    line: dict[str, object] = {
        "event": event,
        "t": time.time(),
        "cwd": payload.get("cwd", ""),
        "transcript": payload.get("transcript_path", ""),
        "source": payload.get("source", ""),
        "reason": payload.get("reason", ""),
        "notification": payload.get("notification_type", ""),
        "error": payload.get("error", ""),
        "tool": payload.get("tool_name", ""),
        "agent": payload.get("agent_id", ""),
        "timeout_ms": tool_input.get("timeout") if isinstance(tool_input.get("timeout"), (int, float)) else None,
        "background": len(background) if isinstance(background, list) else None,
        "text": question(payload)[:TEXT_LIMIT],
        "pane": {key: os.environ[key][:200] for key in PANE_KEYS if key in os.environ},
    }
    if event in PID_EVENTS and not line["agent"]:
        line["pid"], line["pid_start"] = ancestor_claude()
    SESSIONS.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(line)
    if len(encoded) > LINE_LIMIT:
        line["pane"], line["text"] = {}, str(line["text"])[:100]
        encoded = json.dumps(line)
    descriptor = os.open(str(SESSIONS / (session + ".jsonl")), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(descriptor, (encoded + "\n").encode())
    finally:
        os.close(descriptor)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)

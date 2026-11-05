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
KEEP_SECONDS = 30 * 86400
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
SECRETS = (
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
    (re.compile(r"[A-Za-z0-9+_-]{40,}={0,2}"), "[redacted]"),
)


def redact(text: str) -> str:
    for pattern, replacement in SECRETS:
        text = pattern.sub(replacement, text)
    return text


def start_ticks(pid: int) -> str:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return ""
    fields = stat[stat.rfind(")") + 2 :].split()
    return fields[19] if len(fields) > 19 else ""


def ancestor_claude() -> tuple[int, str]:
    pid = os.getppid()
    env = {**os.environ, "LC_ALL": "C"}
    for _ in range(6):
        try:
            line = subprocess.run(
                ["ps", "-o", "ppid=,comm=", "-p", str(pid)], capture_output=True, text=True, timeout=2, env=env
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return 0, ""
        parts = line.split(None, 1)
        if len(parts) < 2:
            return 0, ""
        if os.path.basename(parts[1]).startswith(CLAUDE_NAMES):
            return pid, start_ticks(pid)
        pid = int(parts[0])
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
        description = str(tool_input.get("description") or "")[:120]
        return "permission for {}{}".format(
            payload.get("tool_name", "a tool"), ": " + description if description else ""
        )
    if event in ("PreToolUse", "PostToolUse", "PostToolUseFailure"):
        return ""
    return str(payload.get("last_assistant_message") or payload.get("message") or "")


def prune() -> None:
    cutoff = time.time() - KEEP_SECONDS
    for path in SESSIONS.glob("*.jsonl"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass


def main() -> None:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return
    if not isinstance(payload, dict):
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
        "source": payload.get("source", ""),
        "reason": payload.get("reason", ""),
        "notification": payload.get("notification_type", ""),
        "error": payload.get("error", "") if event == "StopFailure" else "",
        "interrupt": payload.get("is_interrupt") is True,
        "tool": payload.get("tool_name", ""),
        "agent": payload.get("agent_id", ""),
        "timeout_ms": tool_input.get("timeout") if isinstance(tool_input.get("timeout"), (int, float)) else None,
        "background": len(background) if isinstance(background, list) else None,
        "text": redact(question(payload))[:TEXT_LIMIT],
        "pane": {key: os.environ[key][:200] for key in PANE_KEYS if key in os.environ},
    }
    if event in PID_EVENTS and not line["agent"]:
        line["pid"], line["pid_start"] = ancestor_claude()
    SESSIONS.mkdir(parents=True, exist_ok=True)
    if event == "SessionStart":
        prune()
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

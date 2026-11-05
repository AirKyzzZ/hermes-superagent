from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

SESSIONS = Path(os.environ.get("SUPERAGENT_SESSIONS_DIR") or Path.home() / ".superagent" / "sessions")
MAX_AGE_SECONDS = 7 * 86400
TAIL_BYTES = 65536
EVENTS_KEPT = 40
GIT_BUDGET_SECONDS = 15.0
CLAUDE_NAMES = ("claude", "node")
C_LOCALE = {**os.environ, "LC_ALL": "C", "TZ": "UTC"}


def tail_events(path: Path) -> list[dict[str, object]]:
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - TAIL_BYTES))
        chunk = handle.read().decode("utf-8", "replace")
    lines = chunk.splitlines()
    if size > TAIL_BYTES:
        lines = lines[1:]
    events = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and isinstance(event.get("t"), (int, float)):
            events.append(event)
    return events[-EVENTS_KEPT:]


def processes() -> dict[int, tuple[int, float, str]]:
    try:
        output = subprocess.run(
            ["ps", "-A", "-o", "pid=,ppid=,etime=,comm="], capture_output=True, text=True, timeout=5, env=C_LOCALE
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    table = {}
    for line in output.splitlines():
        parts = line.split(None, 3)
        if len(parts) == 4 and parts[0].isdigit() and parts[1].isdigit():
            table[int(parts[0])] = (int(parts[1]), elapsed(parts[2]), os.path.basename(parts[3]))
    return table


def elapsed(text: str) -> float:
    days, _, clock = text.rpartition("-")
    parts = [int(part) for part in clock.split(":") if part.isdigit()]
    while len(parts) < 3:
        parts.insert(0, 0)
    return (int(days) if days.isdigit() else 0) * 86400 + parts[0] * 3600 + parts[1] * 60 + parts[2]


def start_ticks(pid: int) -> str:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return ""
    fields = stat[stat.rfind(")") + 2 :].split()
    return fields[19] if len(fields) > 19 else ""


def last_pid(events: list[dict[str, object]]) -> tuple[int, str]:
    recorded = [event for event in events if event.get("pid")]
    if not recorded:
        return 0, ""
    return int(str(recorded[-1]["pid"])), str(recorded[-1].get("pid_start") or "")


def alive(pid: int, expected: str, table: dict[int, tuple[int, float, str]]) -> bool | None:
    if not pid:
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    if expected and Path("/proc").is_dir():
        return start_ticks(pid) == expected
    entry = table.get(pid)
    return entry is None or entry[2].startswith(CLAUDE_NAMES)


def busy_since(pid: int, seconds_ago: float, table: dict[int, tuple[int, float, str]]) -> bool:
    return any(parent == pid and age < seconds_ago - 1 for parent, age, _ in table.values())


def branch(cwd: str, cache: dict[str, str], deadline: float) -> str:
    if cwd in cache:
        return cache[cwd]
    result = ""
    if cwd and time.monotonic() < deadline and os.path.isdir(cwd):
        try:
            result = subprocess.run(
                ["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, timeout=3
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            result = ""
    cache[cwd] = result
    return result


def main() -> None:
    now = time.time()
    deadline = time.monotonic() + GIT_BUDGET_SECONDS
    table = processes()
    branches: dict[str, str] = {}
    sessions = []
    if SESSIONS.is_dir():
        for path in sorted(SESSIONS.glob("*.jsonl"), key=lambda path: -path.stat().st_mtime):
            if now - path.stat().st_mtime > MAX_AGE_SECONDS:
                continue
            events = tail_events(path)
            if not events:
                continue
            pid, expected = last_pid(events)
            last = events[-1]
            cwd = str(next((event.get("cwd") for event in reversed(events) if event.get("cwd")), ""))
            busy = last.get("event") == "PermissionRequest" and busy_since(pid, now - float(str(last["t"])), table)
            sessions.append(
                {
                    "session": path.stem,
                    "events": events,
                    "alive": alive(pid, expected, table),
                    "busy": busy,
                    "branch": branch(cwd, branches, deadline),
                }
            )
    print(json.dumps({"now": now, "sessions": sessions}))


if __name__ == "__main__":
    main()

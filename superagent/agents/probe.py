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


def started_at(pid: int) -> str:
    try:
        return subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, timeout=2
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def alive(events: list[dict[str, object]]) -> bool | None:
    recorded = [event for event in events if event.get("pid")]
    if not recorded:
        return None
    pid, expected = int(str(recorded[-1]["pid"])), str(recorded[-1].get("pid_start", ""))
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    actual = started_at(pid)
    return not expected or not actual or " ".join(expected.split()) == " ".join(actual.split())


def branch(cwd: str) -> str:
    if not cwd or not os.path.isdir(cwd):
        return ""
    try:
        return subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, timeout=3
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def main() -> None:
    now = time.time()
    sessions = []
    if SESSIONS.is_dir():
        for path in sorted(SESSIONS.glob("*.jsonl")):
            if now - path.stat().st_mtime > MAX_AGE_SECONDS:
                continue
            events = tail_events(path)
            if not events:
                continue
            cwd = str(next((event.get("cwd") for event in reversed(events) if event.get("cwd")), ""))
            sessions.append({"session": path.stem, "events": events, "alive": alive(events), "branch": branch(cwd)})
    print(json.dumps({"now": now, "sessions": sessions}))


if __name__ == "__main__":
    main()

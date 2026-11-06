from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

SESSIONS = Path(os.environ.get("SUPERAGENT_SESSIONS_DIR") or Path.home() / ".superagent" / "sessions")
SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{7,79}")
TAIL_BYTES = 65536
SCREEN_LINES = 25
BLOCKERS = (
    "interrupted",
    "do you want",
    "esc to cancel",
    "enter to confirm",
    "(y/n)",
    "esc to interrupt",
    "-- normal --",
)
PROMPT_START = re.compile(r"\s*[│|]?\s*[❯>]")
PROMPT_EMPTY = re.compile(r"\s*[│|]?\s*[❯>]\s*[│|]?\s*")
RULE = re.compile(r"\s*[─━]{8,}\s*")
WORKING = re.compile(r"…\s*\(\d")
STARTED = time.time()


def tail(path: Path) -> list[dict[str, object]]:
    with open(path, "rb") as file:
        file.seek(max(0, path.stat().st_size - TAIL_BYTES))
        lines = file.read().splitlines()
    found = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            found.append(event)
    return found


def run(argv: list[str], timeout: float = 10) -> str:
    done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    if done.returncode != 0:
        raise RuntimeError(f"{' '.join(argv[:2])} exited {done.returncode}: {done.stderr.strip()[-200:]}")
    return done.stdout


def ready(screen: str) -> str:
    lines = [line.rstrip() for line in screen.splitlines() if line.strip()][-SCREEN_LINES:]
    lowered = "\n".join(lines).lower()
    for marker in BLOCKERS:
        if marker in lowered:
            return f"its screen shows {marker!r}"
    if any(WORKING.search(line) for line in lines):
        return "its screen shows it still working"
    starts = [index for index, line in enumerate(lines) if PROMPT_START.match(line)]
    if not starts:
        return "its screen shows no input box"
    index = starts[-1]
    if not PROMPT_EMPTY.fullmatch(lines[index]):
        return "its input box isn't empty"
    above = index > 0 and RULE.fullmatch(lines[index - 1])
    below = index + 1 < len(lines) and RULE.fullmatch(lines[index + 1])
    if not above or not below:
        return "its screen shows no empty input box"
    return ""


def latest(events: list[dict[str, object]], key: str) -> object:
    return next((event[key] for event in reversed(events) if event.get(key)), None)


def pane_of(events: list[dict[str, object]]) -> dict[str, str]:
    pane = latest(events, "pane")
    return {str(key): str(value) for key, value in pane.items()} if isinstance(pane, dict) else {}


def last_time(events: list[dict[str, object]]) -> float:
    return max(float(str(event.get("t") or 0)) for event in events)


def start_ticks(pid: int) -> str:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return ""
    return stat.rsplit(")", 1)[-1].split()[19]


def same_process(pid: object, start: object) -> bool:
    if not isinstance(pid, int) or pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    if start and Path("/proc").is_dir():
        return start_ticks(pid) == str(start)
    return True


def foreground_tty(pid: int) -> str:
    fields = run(["ps", "-o", "tty=,tpgid=,pgid=", "-p", str(pid)], timeout=5).split()
    if len(fields) != 3 or fields[1] != fields[2]:
        return ""
    return fields[0]


def ancestors(pid: int) -> list[int]:
    chain: list[int] = []
    while pid > 1 and len(chain) < 64:
        chain.append(pid)
        parent = run(["ps", "-o", "ppid=", "-p", str(pid)], timeout=5).strip()
        if not parent.isdigit():
            break
        pid = int(parent)
    return chain


def shared_with_live_session(session: str, pane: dict[str, str]) -> bool:
    mine = {key: pane[key] for key in ("TMUX_PANE", "CMUX_SURFACE_ID") if key in pane}
    for path in SESSIONS.glob("*.jsonl"):
        if path.stem == session:
            continue
        events = tail(path)
        other = pane_of(events)
        if any(other.get(key) == value for key, value in mine.items()):
            ended = any(event.get("event") == "SessionEnd" for event in events[-3:])
            if not ended and same_process(latest(events, "pid"), latest(events, "pid_start")):
                return True
    return False


def out_of_time(args: dict[str, object]) -> bool:
    return time.time() - STARTED > float(str(args.get("budget", 0)))


def nudge(session: str, events: list[dict[str, object]], args: dict[str, object]) -> dict[str, object]:
    pane, text = pane_of(events), str(args["text"])
    pid = latest(events, "pid")
    if not isinstance(pid, int) or not same_process(pid, latest(events, "pid_start")):
        return refuse("its claude process isn't running")
    if shared_with_live_session(session, pane):
        return refuse("another live session shares its pane")
    tty = foreground_tty(pid)
    if not tty:
        return refuse("its claude process isn't in the foreground of its terminal")
    if pane.get("TMUX_PANE"):
        socket, target = pane.get("TMUX", "").split(",")[0], pane["TMUX_PANE"]
        base = [str(args["tmux"]), "-S", socket] if socket else [str(args["tmux"])]
        query = "#{pane_pid}\t#{pane_tty}\t#{pane_in_mode}\t#{pane_synchronized}\t#{pane_dead}"
        fields = run([*base, "display-message", "-p", "-t", target, query]).rstrip("\n").split("\t")
        if len(fields) != 5 or not fields[0].isdigit():
            return refuse(f"tmux couldn't describe pane {target}")
        pane_pid, pane_tty, in_mode, synchronized, dead = fields
        if dead == "1" or in_mode == "1" or synchronized == "1":
            return refuse(f"tmux pane {target} is dead, scrolled back or synchronized with other panes")
        if int(pane_pid) not in ancestors(pid) or not pane_tty.endswith(tty):
            return refuse(f"its claude process doesn't run in tmux pane {target}")
        problem = ready(run([*base, "capture-pane", "-p", "-t", target, "-S", "-40"]))
        if problem:
            return refuse(problem)
        if out_of_time(args):
            return refuse("it ran out of time before typing")
        run([*base, "send-keys", "-t", target, "-l", text])
        run([*base, "send-keys", "-t", target, "Enter"])
        return {"done": True, "detail": f"typed {text!r} in tmux pane {target}"}
    if pane.get("CMUX_SURFACE_ID") and pane.get("CMUX_WORKSPACE_ID"):
        where = ["--workspace", pane["CMUX_WORKSPACE_ID"], "--surface", pane["CMUX_SURFACE_ID"]]
        problem = ready(run([str(args["cmux"]), "read-screen", *where, "--lines", "40"]))
        if problem:
            return refuse(problem)
        if out_of_time(args):
            return refuse("it ran out of time before typing")
        run([str(args["cmux"]), "send", *where, "--", text + "\\n"])
        return {"done": True, "detail": f"typed {text!r} in its cmux surface"}
    return refuse("it runs outside tmux and cmux")


def refuse(detail: str) -> dict[str, object]:
    return {"done": False, "detail": detail}


def act(args: dict[str, object]) -> dict[str, object]:
    session = str(args.get("session", ""))
    if not SESSION_ID.fullmatch(session):
        return refuse("not a session id")
    path = SESSIONS / f"{session}.jsonl"
    if not path.is_file():
        return refuse("its hook log is gone")
    events = tail(path)
    if not events or abs(last_time(events) - float(str(args.get("since")))) > 0.001:
        return refuse("it moved on since it was checked")
    if args.get("action") == "nudge":
        return nudge(session, events, args)
    return refuse("unknown action")


def main(raw: str) -> None:
    try:
        result = act(json.loads(raw))
    except Exception as error:
        result = {
            "done": False,
            "detail": "the relaunch script failed",
            "log": f"{type(error).__name__}: {error}"[:300],
        }
    print(json.dumps(result))
